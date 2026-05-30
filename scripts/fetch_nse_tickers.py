"""
fetch_nse_tickers.py — Sync the NSE equity universe into SQL.

Data loading cadence
  - Run ONCE after fresh install (before first python run.py)
  - Re-run MONTHLY or after each NSE quarterly index rebalance

Tables written
  nse_universe         master company reference (symbol, company_name, sector, isin, is_active)
  nse_index_membership normalized index constituency per (symbol, index_code)

Tables NOT written by this script (daily pipeline writes these)
  daily_price_data, stock_features, stock_signals, market_regime, market_environment

Usage
  python scripts/fetch_nse_tickers.py           # NIFTY 500 + Midcap 150 + Smallcap 250
  python scripts/fetch_nse_tickers.py --all     # full NSE equity list (~2000+ symbols)

Membership semantics
  Symbol joins an index   → INSERT or UPDATE is_current_member=1
  Symbol leaves an index  → UPDATE is_current_member=0, last_removed_date=today
  is_active on nse_universe NEVER set to 0 here; delisting must be done manually
  (set is_active=0 in SSMS when Yahoo returns no data for 10+ consecutive days)
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from sqlalchemy import text


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.db import engine
from app.config.logging_config import get_logger

logger = get_logger("fetch_nse_tickers")


# ── NSE source definitions ────────────────────────────────────────────────────
# index_code must match the seed rows in nse_index_ref (setup_db.py).

_NSE_SOURCES = [
    {
        "name":       "NIFTY 500",
        "url":        "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
        "index_code": "NIFTY500",
    },
    {
        "name":       "NIFTY Midcap 150",
        "url":        "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
        "index_code": "MIDCAP150",
    },
    {
        "name":       "NIFTY Smallcap 250",
        "url":        "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
        "index_code": "SMALLCAP250",
    },
]

_NSE_ALL_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nseindia.com",
    "Accept":  "text/html,application/xhtml+xml,*/*;q=0.8",
}

_REQUEST_TIMEOUT = 20


# ── Download helpers ──────────────────────────────────────────────────────────

def _fetch_index_csv(name: str, url: str) -> Optional[pd.DataFrame]:
    """Download one NSE index CSV. Returns a DataFrame with columns [symbol, company_name, sector, isin]."""

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()

        df = pd.read_csv(StringIO(resp.text))
        df.columns = df.columns.str.strip()

        symbol_col = next((c for c in df.columns if c.strip().lower() == "symbol"), None)
        if not symbol_col:
            logger.error("%s: no Symbol column. Columns: %s", name, df.columns.tolist())
            return None

        name_col   = next((c for c in df.columns if "company" in c.lower()), None)
        sector_col = next((c for c in df.columns if "industr" in c.lower() or "sector" in c.lower()), None)
        isin_col   = next((c for c in df.columns if "isin" in c.lower()), None)

        out = pd.DataFrame()
        out["symbol"]       = df[symbol_col].str.strip().str.upper()
        out["company_name"] = df[name_col].str.strip()   if name_col   else None
        out["sector"]       = df[sector_col].str.strip() if sector_col else None
        out["isin"]         = df[isin_col].str.strip()   if isin_col   else None

        out = out[out["symbol"].notna() & (out["symbol"] != "SYMBOL")]
        out = out.drop_duplicates(subset=["symbol"])

        logger.info("%s: %d stocks downloaded", name, len(out))
        return out

    except requests.HTTPError as exc:
        logger.error("%s: HTTP %s", name, exc.response.status_code)
        return None
    except Exception as exc:
        logger.error("%s: failed — %s", name, exc, exc_info=True)
        return None


def _fetch_all_equity() -> Optional[pd.DataFrame]:
    """Download the full NSE equity list (~2000+ symbols)."""

    try:
        resp = requests.get(_NSE_ALL_EQUITY_URL, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()

        df = pd.read_csv(StringIO(resp.text))
        df.columns = df.columns.str.strip()

        if "SYMBOL" not in df.columns:
            logger.error("EQUITY_L.csv: no SYMBOL column. Columns: %s", df.columns.tolist())
            return None

        if "SERIES" in df.columns:
            df = df[df["SERIES"].str.strip().isin(["EQ", "BE", "BZ"])]

        out = pd.DataFrame()
        out["symbol"]       = df["SYMBOL"].str.strip().str.upper()
        out["company_name"] = df["NAME OF COMPANY"].str.strip() if "NAME OF COMPANY" in df.columns else None
        out["sector"]       = None
        out["isin"]         = df["ISIN NUMBER"].str.strip() if "ISIN NUMBER" in df.columns else None

        out = out[out["symbol"].notna()].drop_duplicates(subset=["symbol"])

        logger.info("All equity: %d stocks downloaded", len(out))
        return out

    except Exception as exc:
        logger.error("EQUITY_L fetch failed: %s", exc, exc_info=True)
        return None


# ── DB sync helpers ───────────────────────────────────────────────────────────

def _build_company_df(stock_frames: list[tuple[pd.DataFrame, Optional[str]]]) -> pd.DataFrame:
    """
    Merge all downloaded frames into a single deduplicated company DataFrame.
    When the same symbol appears in multiple frames, metadata from the first
    occurrence is kept (first-wins on company_name, sector, isin).
    """
    frames = [df for df, _ in stock_frames]
    return (
        pd.concat(frames, ignore_index=True)
        .groupby("symbol", as_index=False)
        .agg({"company_name": "first", "sector": "first", "isin": "first"})
    )


def _build_memberships(
    stock_frames: list[tuple[pd.DataFrame, Optional[str]]]
) -> dict[str, set[str]]:
    """
    Returns {symbol: {index_code, ...}} for every (symbol, index_code) pair
    present in the downloaded frames. Frames with index_code=None (--all mode)
    are skipped — no membership rows are written for those.
    """
    result: dict[str, set[str]] = {}
    for df, index_code in stock_frames:
        if index_code is None:
            continue
        for sym in df["symbol"]:
            result.setdefault(sym, set()).add(index_code)
    return result


def _upsert_companies(
    conn,
    company_df: pd.DataFrame,
    existing_symbols: set[str],
    today: date,
) -> tuple[int, int]:
    """
    INSERT new symbols, UPDATE existing ones in nse_universe.
    Returns (added, updated) counts.
    """
    added = updated = 0

    for _, row in company_df.iterrows():
        sym = row["symbol"]
        params = {
            "symbol":       sym,
            "company_name": row.get("company_name") or None,
            "sector":       row.get("sector")       or None,
            "isin":         row.get("isin")         or None,
            "today":        today,
        }
        try:
            if sym not in existing_symbols:
                conn.execute(
                    text("""
                        INSERT INTO nse_universe
                            (symbol, company_name, sector, isin, is_active, first_seen, last_seen)
                        VALUES
                            (:symbol, :company_name, :sector, :isin, 1, :today, :today)
                    """),
                    params,
                )
                added += 1
            else:
                conn.execute(
                    text("""
                        UPDATE nse_universe
                        SET company_name = :company_name,
                            sector       = :sector,
                            isin         = :isin,
                            is_active    = 1,
                            last_seen    = :today,
                            updated_at   = GETDATE()
                        WHERE symbol = :symbol
                    """),
                    params,
                )
                updated += 1
        except Exception as exc:
            logger.error("Company upsert failed for %s: %s", sym, exc)

    return added, updated


def _upsert_memberships(
    conn,
    memberships: dict[str, set[str]],
    downloaded_index_codes: set[str],
    today: date,
) -> int:
    """
    Sync nse_index_membership for the downloaded index codes only:
      - Current members → INSERT or UPDATE is_current_member=1
      - DB rows for these indices that are NOT in the download → is_current_member=0

    Returns the number of memberships marked as removed.
    """
    if not downloaded_index_codes:
        return 0

    # Load existing membership rows for the indices we just downloaded
    placeholders = ",".join(f"'{c}'" for c in downloaded_index_codes)
    existing_rows = conn.execute(
        text(
            f"SELECT symbol, index_code "
            f"FROM nse_index_membership "
            f"WHERE index_code IN ({placeholders})"
        )
    ).fetchall()
    existing_set = {(row[0], row[1]) for row in existing_rows}

    # Upsert current members
    for symbol, index_codes in memberships.items():
        for index_code in index_codes:
            key = (symbol, index_code)
            try:
                if key in existing_set:
                    conn.execute(
                        text("""
                            UPDATE nse_index_membership
                            SET is_current_member = 1,
                                last_removed_date = NULL,
                                updated_at        = GETDATE()
                            WHERE symbol = :s AND index_code = :ic
                        """),
                        {"s": symbol, "ic": index_code},
                    )
                else:
                    conn.execute(
                        text("""
                            INSERT INTO nse_index_membership
                                (symbol, index_code, is_current_member, first_added_date)
                            VALUES
                                (:s, :ic, 1, :today)
                        """),
                        {"s": symbol, "ic": index_code, "today": today},
                    )
            except Exception as exc:
                logger.error("Membership upsert failed for %s/%s: %s", symbol, index_code, exc)

    # Mark removed: in DB for a downloaded index but absent from this download
    current_keys = {(sym, ic) for sym, codes in memberships.items() for ic in codes}
    removed_rows = [row for row in existing_rows if (row[0], row[1]) not in current_keys]

    for symbol, index_code in removed_rows:
        try:
            conn.execute(
                text("""
                    UPDATE nse_index_membership
                    SET is_current_member = 0,
                        last_removed_date = :today,
                        updated_at        = GETDATE()
                    WHERE symbol = :s AND index_code = :ic
                """),
                {"s": symbol, "ic": index_code, "today": today},
            )
        except Exception as exc:
            logger.error("Membership deactivation failed for %s/%s: %s", symbol, index_code, exc)

    if removed_rows:
        logger.info(
            "%d stock(s) left their index — is_current_member=0; "
            "daily candle collection continues (is_active on nse_universe unchanged)",
            len(removed_rows),
        )

    return len(removed_rows)


def _sync_to_db(stock_frames: list[tuple[pd.DataFrame, Optional[str]]]) -> dict:
    """
    Orchestrates the full DB sync for one fetch run.

    stock_frames: list of (DataFrame, index_code).
        index_code=None means the frame is from --all mode; only nse_universe
        is updated, no membership rows are written.

    Returns a stats dict with added/updated/removed/errors counts.
    """
    if not stock_frames:
        return {"added": 0, "updated": 0, "removed": 0, "errors": 0}

    today            = date.today()
    company_df       = _build_company_df(stock_frames)
    memberships      = _build_memberships(stock_frames)
    downloaded_codes = {ic for _, ic in stock_frames if ic is not None}

    try:
        with engine.begin() as conn:
            existing_symbols = {
                row[0]
                for row in conn.execute(text("SELECT symbol FROM nse_universe")).fetchall()
            }
            added, updated = _upsert_companies(conn, company_df, existing_symbols, today)
            removed        = _upsert_memberships(conn, memberships, downloaded_codes, today)

    except Exception as exc:
        logger.error("DB sync failed: %s", exc, exc_info=True)
        return {"added": 0, "updated": 0, "removed": 0, "errors": 1}

    return {"added": added, "updated": updated, "removed": removed, "errors": 0}


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:

    parser = argparse.ArgumentParser(
        description="Sync NSE equity universe into nse_universe + nse_index_membership"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Fetch all listed NSE equities (~2000+) instead of the index lists",
    )
    args = parser.parse_args()

    stock_frames: list[tuple[pd.DataFrame, Optional[str]]] = []

    if args.all:
        print("Fetching all NSE listed equities...")
        df = _fetch_all_equity()
        if df is not None:
            stock_frames.append((df, None))
    else:
        print("Fetching NIFTY 500 + Midcap 150 + Smallcap 250 in parallel...")
        with ThreadPoolExecutor(max_workers=len(_NSE_SOURCES)) as executor:
            futures = {
                executor.submit(_fetch_index_csv, s["name"], s["url"]): s["index_code"]
                for s in _NSE_SOURCES
            }
            for future in as_completed(futures):
                index_code = futures[future]
                try:
                    df = future.result()
                    if df is not None:
                        stock_frames.append((df, index_code))
                except Exception as exc:
                    logger.error("Source %s failed: %s", index_code, exc, exc_info=True)

    if not stock_frames:
        print(
            "\nERROR: All NSE sources failed.\n"
            "Check internet connection or NSE website availability.\n"
            "No tables were modified."
        )
        return 1

    print("\nSyncing to database...")
    stats = _sync_to_db(stock_frames)

    total = sum(len(df) for df, _ in stock_frames)
    print(f"\nDone.")
    print(f"  Total stocks in download      : {total}")
    print(f"  New companies added           : {stats['added']}")
    print(f"  Existing companies updated    : {stats['updated']}")
    print(f"  Index memberships removed     : {stats['removed']}")
    if stats["errors"]:
        print(f"  Errors                        : {stats['errors']}")

    if stats["removed"]:
        print(
            f"\n  NOTE: {stats['removed']} stock(s) left their index — is_current_member=0 in"
            f"\n  nse_index_membership. Daily candle collection CONTINUES (is_active unchanged)."
            f"\n  To stop collecting a delisted stock, set is_active=0 manually in SSMS."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
