"""Screener.in fundamentals scraper — parallel with global rate limiting.

Architecture
------------
Threading model: ThreadPoolExecutor (I/O-bound HTTP — not multiprocessing).
Rate limiting:   A single _RateLimiter instance shared across ALL threads.
                 Every thread must acquire a token before firing a request.
                 This enforces a global minimum gap of RATE_LIMIT_SECONDS
                 between any two HTTP requests, regardless of concurrency.
SQL writes:      Results are collected in a thread-safe list during scraping,
                 then bulk-inserted in batches after all threads complete.
                 This reduces round-trips from N individual INSERTs to
                 ceil(N / SQL_BATCH_SIZE) batched calls.

Throughput estimate (500 symbols)
----------------------------------
  Sequential (old):  500 × (2s request + 3s sleep) ≈ 41 min
  Parallel (new):    global rate = 1 req / 2s → 500 × 2s = 16 min
                     HTML parsing + DB writes overlap across threads → ~14 min

Data extracted per symbol
--------------------------
  roe, roce, debt_to_equity, sales_growth_3yr, profit_growth_3yr, opm,
  eps_ttm, eps_prev_yr, promoter_holding, promoter_pledge_pct,
  market_cap_cr, pe_ratio, quality_score (0-10 count of passed criteria)
"""

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import FundamentalsConfig, StrategyConfig

logger = get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Rate limiter ──────────────────────────────────────────────────────────────

class _RateLimiter:
    """Thread-safe global rate limiter.

    Enforces a minimum interval between HTTP requests across ALL threads.
    Uses a mutex + monotonic clock so no thread can bypass the gap by
    racing ahead while another is sleeping.

    Usage:
        limiter = _RateLimiter(min_interval_seconds=2.0)
        limiter.acquire()   # blocks until allowed, then records the timestamp
        requests.get(url)   # fire the actual request immediately after
    """

    def __init__(self, min_interval_seconds: float) -> None:
        """Initialise the rate limiter.

        Args:
            min_interval_seconds: Minimum seconds between any two consecutive
                HTTP request acquisitions across all threads.
        """
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_request_at: float = 0.0

    def acquire(self) -> None:
        """Block until a request token is available, then claim it.

        All threads compete for the same lock, ensuring only one token is
        issued per _min_interval window.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            gap = self._min_interval - elapsed
            if gap > 0:
                time.sleep(gap)
            self._last_request_at = time.monotonic()


# ── HTTP layer ────────────────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
    stop=stop_after_attempt(FundamentalsConfig.RETRY_ATTEMPTS),
    wait=wait_exponential(
        min=FundamentalsConfig.RETRY_MIN_WAIT_SECONDS,
        max=FundamentalsConfig.RETRY_MAX_WAIT_SECONDS,
    ),
)
def _fetch_page(url: str) -> Optional[str]:
    """Fetch a Screener.in page with retry on transient network failures.

    Args:
        url: Full URL to fetch.

    Returns:
        HTML text on success, None if the response body is empty.

    Raises:
        requests.ConnectionError / requests.Timeout — retried by tenacity.
        requests.HTTPError — not retried (4xx/5xx are deterministic failures).
    """
    resp = requests.get(
        url,
        headers=_HEADERS,
        timeout=FundamentalsConfig.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.text or None


# ── HTML parsing ──────────────────────────────────────────────────────────────

def _safe_float(text_val: Optional[str]) -> Optional[float]:
    """Strip currency / percent symbols and return float, or None if unparseable.

    Args:
        text_val: Raw scraped string (e.g. '₹1,234.56', '12.5%', '--').

    Returns:
        float on success, None otherwise.
    """
    if not text_val:
        return None
    cleaned = (
        text_val.strip()
        .replace(",", "")
        .replace("₹", "")
        .replace("%", "")
        .replace("Cr", "")
        .replace("\xa0", "")
        .strip()
    )
    if cleaned in ("", "--", "-", "N/A", "NA"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _load_filter_config() -> dict:
    """Load fundamental filter thresholds from fundamental_filter_config SQL table.

    Falls back to StrategyConfig.FUNDAMENTAL_FILTERS if the table is empty or
    the query fails (e.g. first run before setup_db.py has been executed).
    The SQL table is the source of truth — update thresholds there without code changes.

    Returns:
        Dict of filter_name → threshold value (float or bool).
    """
    query = text(
        "SELECT filter_name, threshold FROM fundamental_filter_config WHERE is_active = 1"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        if not rows:
            return StrategyConfig.FUNDAMENTAL_FILTERS.copy()
        config = StrategyConfig.FUNDAMENTAL_FILTERS.copy()
        for name, value in rows:
            config[name] = value
        logger.debug("Loaded %d fundamental filter thresholds from DB", len(rows))
        return config
    except Exception as exc:
        logger.debug("fundamental_filter_config unavailable — using Python defaults: %s", exc)
        return StrategyConfig.FUNDAMENTAL_FILTERS.copy()


def _parse_ratios(soup: BeautifulSoup) -> dict[str, str]:
    """Extract key metrics from the top-ratios list (top of Screener page).

    Screener.in HTML structure (verified 2026-05-30):
      <div class="company-ratios">
        <ul id="top-ratios">
          <li ...>
            <span class="name">ROE</span>
            <span class="nowrap value">...<span class="number">7.91</span></span>
          </li>
          ...
        </ul>
      </div>

    The outer div has class "company-ratios" but the actual list has id "top-ratios".
    Searching for ul.company-ratios returns None — must use ul#top-ratios.

    Args:
        soup: Parsed BeautifulSoup object of the full company page.

    Returns:
        Dict mapping lowercase label text → raw value string.
        Keys include: 'roe', 'roce', 'stock p/e', 'market cap', 'book value',
        'current price', 'dividend yield', 'face value'.
    """
    ratios: dict[str, str] = {}
    ul = soup.find("ul", id="top-ratios")   # not class="company-ratios"
    if not ul:
        return ratios
    for li in ul.find_all("li"):
        name_span = li.find("span", class_="name")
        val_span  = li.find("span", class_="number")
        if name_span and val_span:
            ratios[name_span.get_text(strip=True).lower()] = val_span.get_text(strip=True)
    return ratios


def _parse_annual_column_values(
    soup: BeautifulSoup,
    section_id: str,
    row_label: str,
    n_years: int = 4,
) -> list[float]:
    """Extract the last N annual numeric values from a named row in a section table.

    Used to compute multi-year CAGRs (e.g. 3-year sales growth) from the
    annual P&L data, since Screener.in does not expose pre-computed CAGR
    values as labeled rows — they are derived client-side.

    Args:
        soup: Parsed BeautifulSoup object.
        section_id: HTML section id (e.g. 'profit-loss', 'balance-sheet').
        row_label: Exact row label as it appears in the table (case-insensitive).
        n_years: How many trailing values to return.

    Returns:
        List of floats (most-recent last), length ≤ n_years.
        Empty list if the row is not found.
    """
    section = soup.find("section", id=section_id)
    if not section:
        return []
    table = section.find("table", class_="data-table")
    if not table:
        return []

    target = row_label.lower()
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        if cells[0].get_text(strip=True).lower() != target:
            continue
        values: list[float] = []
        for cell in cells[1:]:
            raw = cell.get_text(strip=True).replace(",", "").replace("%", "").strip()
            try:
                values.append(float(raw))
            except ValueError:
                pass
        return values[-n_years:] if len(values) >= n_years else values
    return []


def _cagr(values: list[float], years: int) -> Optional[float]:
    """Compute CAGR from a list of ordered annual values over N years.

    Args:
        values: Ordered list of annual values (oldest first, most-recent last).
                Must have at least years + 1 elements.
        years: Number of years for the CAGR period.

    Returns:
        CAGR percentage (e.g. 12.5 means 12.5%), or None if insufficient data
        or non-positive start/end values.
    """
    if len(values) < years + 1:
        return None
    start = values[-(years + 1)]
    end   = values[-1]
    if start <= 0 or end <= 0:
        return None
    return round(((end / start) ** (1.0 / years) - 1.0) * 100.0, 2)


def _parse_table_rows(soup: BeautifulSoup, section_id: str) -> dict[str, str]:
    """Extract key→most-recent-value pairs from a named Screener.in section table.

    Args:
        soup: Parsed BeautifulSoup object.
        section_id: HTML id of the target section (e.g. 'profit-loss').

    Returns:
        Dict mapping metric name → most-recent (last non-empty column) value.
    """
    result: dict[str, str] = {}
    section = soup.find("section", id=section_id)
    if not section:
        return result
    table = section.find("table", class_="data-table")
    if not table:
        return result
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        name = cells[0].get_text(strip=True).lower()
        for cell in reversed(cells[1:]):
            txt = cell.get_text(strip=True)
            if txt and txt not in ("—", "--", ""):
                result[name] = txt
                break
    return result


def _parse_shareholding(soup: BeautifulSoup) -> dict[str, str]:
    """Extract promoter holding and pledge % from the shareholding section.

    Args:
        soup: Parsed BeautifulSoup object.

    Returns:
        Dict with optional keys 'promoter_holding', 'promoter_pledge_pct'.
    """
    result: dict[str, str] = {}
    section = soup.find("section", id="shareholding")
    if not section:
        return result
    table = section.find("table", class_="data-table")
    if not table:
        return result
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        label = cells[0].get_text(strip=True).lower()
        if "promoter" in label and "pledge" not in label and len(cells) >= 2:
            for cell in reversed(cells[1:]):
                txt = cell.get_text(strip=True)
                if txt and txt not in ("—", "--"):
                    result["promoter_holding"] = txt
                    break
        if "pledge" in label and len(cells) >= 2:
            for cell in reversed(cells[1:]):
                txt = cell.get_text(strip=True)
                if txt and txt not in ("—", "--"):
                    result["promoter_pledge_pct"] = txt
                    break
    return result


def _parse_quarterly_eps(soup: BeautifulSoup, n_quarters: int = 8) -> list[float]:
    """Extract the last N quarterly EPS values from the Screener.in quarters section.

    Uses the same table structure as the annual P&L but under section#quarters.
    Row label 'eps in rs' (case-insensitive) matches what Screener renders.

    Args:
        soup: Parsed BeautifulSoup of the company page.
        n_quarters: How many trailing quarterly values to return.

    Returns:
        List of floats oldest-first, length ≤ n_quarters. Empty if not found.
    """
    return _parse_annual_column_values(soup, "quarters", "eps in rs", n_years=n_quarters)


def _compute_eps_acceleration(quarterly_eps: list[float]) -> Optional[bool]:
    """Check if quarterly YoY EPS growth is accelerating over the last 3 quarters.

    Requires 8 consecutive quarters to produce 3 YoY growth rates:
      growth[-3] = vals[-3] / vals[-7] - 1   (oldest of the three)
      growth[-2] = vals[-2] / vals[-6] - 1
      growth[-1] = vals[-1] / vals[-5] - 1   (most recent)

    Acceleration is confirmed when growth[-3] < growth[-2] < growth[-1].

    Args:
        quarterly_eps: At least 8 quarterly EPS values, oldest-first.

    Returns:
        True  — EPS growth accelerating
        False — EPS growth flat or decelerating
        None  — fewer than 8 quarters or prior-year values non-positive
    """
    if len(quarterly_eps) < 8:
        return None

    vals = quarterly_eps[-8:]   # oldest first, most-recent last

    def _yoy(i: int) -> Optional[float]:
        prior = vals[i - 4]
        if prior <= 0:
            return None
        return (vals[i] / prior - 1.0) * 100.0

    growths = [_yoy(i) for i in (5, 6, 7)]
    if any(g is None for g in growths):
        return None

    return growths[0] < growths[1] < growths[2]


def parse_company_page(html: str, symbol: str) -> dict:
    """Parse a Screener.in company HTML page into a fundamentals dict.

    Key label mappings (verified against live Screener.in HTML 2026-05-30):
      Top-ratios ul#top-ratios:
        'roe'      → ROE %          (NOT 'return on equity')
        'roce'     → ROCE %         (correct)
        'stock p/e'→ P/E ratio      (NOT 'p/e')
        'market cap' → Mkt cap Cr   (correct; strip commas + 'Cr')

      Profit-loss section#profit-loss data-table:
        'opm %'    → OPM %          (NOT 'opm')
        'eps in rs'→ EPS TTM        (correct)
        'sales+'   → Annual sales (multiple columns — compute CAGR)
        'net profit+' → Annual net profit (multiple columns — compute CAGR)

      Balance-sheet section#balance-sheet:
        'borrowings+', 'equity capital', 'reserves' → compute D/E ratio

      Shareholding section#shareholding:
        'promoters+' → Promoter holding % (correct)

    Args:
        html: Raw HTML from the company page.
        symbol: Bare NSE symbol — used for debug logging only.

    Returns:
        Dict with keys matching stock_fundamentals columns. Numeric values are
        float or None. quality_score is an int (count of criteria passed, 0-9).
    """
    soup = BeautifulSoup(html, "html.parser")
    ff   = _load_filter_config()

    ratios  = _parse_ratios(soup)
    pl_data = _parse_table_rows(soup, "profit-loss")
    bs_data = _parse_table_rows(soup, "balance-sheet")
    sh_data = _parse_shareholding(soup)

    # ── Top-ratios section ────────────────────────────────────────────────────
    roe  = _safe_float(ratios.get("roe"))          # label in HTML: 'ROE'
    roce = _safe_float(ratios.get("roce"))         # label in HTML: 'ROCE'
    pe   = _safe_float(ratios.get("stock p/e"))    # label in HTML: 'Stock P/E'
    mcap = _safe_float(ratios.get("market cap"))   # label in HTML: 'Market Cap'

    # ── P&L section ───────────────────────────────────────────────────────────
    opm     = _safe_float(pl_data.get("opm %"))    # label in HTML: 'OPM %'
    eps_ttm = _safe_float(pl_data.get("eps in rs"))# label in HTML: 'EPS in Rs'

    # 3-year CAGR for sales and profit (not a labeled row — computed from annual data)
    sales_vals  = _parse_annual_column_values(soup, "profit-loss", "sales+",       n_years=4)
    profit_vals = _parse_annual_column_values(soup, "profit-loss", "net profit+",  n_years=4)
    sales_growth  = _cagr(sales_vals,  years=3)
    profit_growth = _cagr(profit_vals, years=3)

    # ── Balance-sheet D/E ratio ───────────────────────────────────────────────
    # D/E = Total Borrowings / (Equity Capital + Reserves)
    borrowings  = _safe_float(bs_data.get("borrowings+"))
    equity_cap  = _safe_float(bs_data.get("equity capital"))
    reserves    = _safe_float(bs_data.get("reserves"))
    debt_equity: Optional[float] = None
    if borrowings is not None and equity_cap is not None and reserves is not None:
        net_worth = equity_cap + reserves
        debt_equity = round(borrowings / net_worth, 2) if net_worth > 0 else None

    # ── Shareholding section ──────────────────────────────────────────────────
    promoter_holding    = _safe_float(sh_data.get("promoter_holding"))
    promoter_pledge_pct = _safe_float(sh_data.get("promoter_pledge_pct"))

    # ── Quarterly EPS acceleration ────────────────────────────────────────────
    # Fetch 8 quarters to compute 3 YoY growth rates; check Q-2 < Q-1 < Q (accel).
    quarterly_eps = _parse_quarterly_eps(soup, n_quarters=8)
    eps_accel     = _compute_eps_acceleration(quarterly_eps)
    # Store as 0/1/None for the BIT column; None = insufficient quarterly data
    eps_acceleration_int: Optional[int] = (1 if eps_accel else 0) if eps_accel is not None else None

    # ── Fundamental quality score (0–10, 1 point per criterion) ──────────────
    score = sum([
        roe  is not None and roe  >= ff["roe_min"],
        roce is not None and roce >= ff["roce_min"],
        debt_equity  is not None and debt_equity  <= ff["debt_to_equity_max"],
        sales_growth is not None and sales_growth >= ff["sales_growth_min"],
        profit_growth is not None and profit_growth >= ff["profit_growth_min"],
        promoter_holding    is not None and promoter_holding    >= ff["promoter_holding_min"],
        promoter_pledge_pct is not None and promoter_pledge_pct <= ff["promoter_pledge_max"],
        opm  is not None and opm  >= ff["opm_min"],
        mcap is not None and mcap >= ff["market_cap_min_cr"],
        eps_accel is True,   # 10th criterion: quarterly EPS growth accelerating
    ])

    logger.debug(
        "Parsed %-15s | roe=%s roce=%s d/e=%s opm=%s promoter=%s mcap=%s eps_accel=%s score=%d/10",
        symbol,
        f"{roe:.1f}" if roe is not None else "—",
        f"{roce:.1f}" if roce is not None else "—",
        f"{debt_equity:.2f}" if debt_equity is not None else "—",
        f"{opm:.1f}" if opm is not None else "—",
        f"{promoter_holding:.1f}" if promoter_holding is not None else "—",
        f"{mcap:.0f}" if mcap is not None else "—",
        ("✓" if eps_accel else "✗") if eps_accel is not None else "—",
        score,
    )

    return {
        "roe":                 roe,
        "roce":                roce,
        "debt_to_equity":      debt_equity,
        "sales_growth_3yr":    sales_growth,
        "profit_growth_3yr":   profit_growth,
        "opm":                 opm,
        "eps_ttm":             eps_ttm,
        "eps_prev_yr":         None,
        "promoter_holding":    promoter_holding,
        "promoter_pledge_pct": promoter_pledge_pct,
        "market_cap_cr":       mcap,
        "pe_ratio":            pe,
        "quality_score":       score,
        "eps_acceleration":    eps_acceleration_int,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_stale_symbols(trade_date: date) -> list[str]:
    """Return bare NSE symbols that need re-scraping.

    A symbol is stale if it has no row in stock_fundamentals OR its newest
    row is older than FundamentalsConfig.STALE_AFTER_DAYS.

    Args:
        trade_date: Current run date — used to compute the staleness cutoff.

    Returns:
        Sorted list of bare NSE symbols (no .NS suffix).
    """
    cutoff = trade_date - timedelta(days=FundamentalsConfig.STALE_AFTER_DAYS)
    query = text("""
        SELECT nu.symbol
        FROM nse_universe nu
        WHERE nu.is_active = 1
          AND NOT EXISTS (
              SELECT 1
              FROM stock_fundamentals sf
              WHERE sf.symbol = nu.symbol
                AND sf.trade_date >= :cutoff
          )
        ORDER BY nu.symbol
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query, {"cutoff": cutoff}).fetchall()
        symbols = [r[0] for r in rows]
        logger.info("Symbols needing scrape: %d (cutoff=%s)", len(symbols), cutoff)
        return symbols
    except Exception as exc:
        logger.error("Stale symbol query failed: %s", exc, exc_info=True)
        return []


def _bulk_upsert_fundamentals(records: list[dict], trade_date: date) -> None:
    """Batch-delete then batch-insert fundamentals for all scraped symbols.

    Replaces one INSERT per symbol with ceil(N / SQL_BATCH_SIZE) bulk writes.
    All operations run inside a single transaction — partial failures roll back.

    Args:
        records: List of dicts with keys matching stock_fundamentals columns
                 (plus 'symbol' and 'trade_date').
        trade_date: Date being inserted — used to scope the DELETE.
    """
    if not records:
        return

    # Convert nan → None so SQL Server FLOAT columns accept the values
    def _clean(v):
        return None if (isinstance(v, float) and math.isnan(v)) else v

    clean_records = [{k: _clean(v) for k, v in row.items()} for row in records]

    symbols = [r["symbol"] for r in clean_records]

    try:
        with engine.begin() as conn:
            # Delete in chunks to avoid excessive IN-clause length
            chunk = FundamentalsConfig.SQL_BATCH_SIZE
            for i in range(0, len(symbols), chunk):
                batch_syms = symbols[i : i + chunk]
                placeholders = ", ".join(f":s{j}" for j in range(len(batch_syms)))
                conn.execute(
                    text(
                        f"DELETE FROM stock_fundamentals "
                        f"WHERE trade_date = :td AND symbol IN ({placeholders})"
                    ),
                    {"td": trade_date, **{f"s{j}": s for j, s in enumerate(batch_syms)}},
                )

            # Bulk insert via executemany in chunks
            insert_sql = text("""
                INSERT INTO stock_fundamentals (
                    symbol, trade_date, scraped_at,
                    roe, roce, debt_to_equity,
                    sales_growth_3yr, profit_growth_3yr, opm,
                    eps_ttm, eps_prev_yr,
                    promoter_holding, promoter_pledge_pct,
                    market_cap_cr, pe_ratio, quality_score,
                    eps_acceleration
                ) VALUES (
                    :symbol, :trade_date, GETDATE(),
                    :roe, :roce, :debt_to_equity,
                    :sales_growth_3yr, :profit_growth_3yr, :opm,
                    :eps_ttm, :eps_prev_yr,
                    :promoter_holding, :promoter_pledge_pct,
                    :market_cap_cr, :pe_ratio, :quality_score,
                    :eps_acceleration
                )
            """)
            for i in range(0, len(clean_records), chunk):
                conn.execute(insert_sql, clean_records[i : i + chunk])

        logger.info(
            "Bulk upsert complete | symbols=%d | batches=%d",
            len(records),
            math.ceil(len(records) / chunk),
        )
    except Exception as exc:
        logger.error("Bulk fundamentals upsert failed: %s", exc, exc_info=True)


# ── Worker (called inside thread pool) ───────────────────────────────────────

def _scrape_symbol_data(
    symbol: str,
    rate_limiter: _RateLimiter,
) -> tuple[str, Optional[dict]]:
    """Fetch and parse one symbol — no DB writes, safe to run in any thread.

    Args:
        symbol: Bare NSE symbol.
        rate_limiter: Shared global rate limiter — acquires a token before HTTP.

    Returns:
        (symbol, data_dict) on success.
        (symbol, None) on any failure.
    """
    url = f"{FundamentalsConfig.SCREENER_BASE_URL}/{symbol}/"
    try:
        rate_limiter.acquire()   # ← global rate-limit gate, blocks if needed
        html = _fetch_page(url)
        if not html:
            logger.warning("Empty response for %s", symbol)
            return symbol, None
        data = parse_company_page(html, symbol)
        return symbol, data

    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        if code == 404:
            logger.warning("%s not found on Screener.in (404)", symbol)
        elif code == 429:
            logger.error("Rate limited (429) for %s — reduce MAX_CONCURRENT_REQUESTS", symbol)
        else:
            logger.error("HTTP %s for %s: %s", code, symbol, exc)
        return symbol, None

    except Exception as exc:
        logger.error("Scrape failed for %s: %s", symbol, exc, exc_info=True)
        return symbol, None


# ── Main entry point ──────────────────────────────────────────────────────────

class ScreenerService:
    """Orchestrates parallel Screener.in scraping with rate limiting and batch SQL writes."""

    @staticmethod
    def run(
        trade_date: Optional[date] = None,
        symbols: Optional[list[str]] = None,
    ) -> dict:
        """Scrape fundamentals for all stale symbols in parallel.

        Execution model:
          1. Determine which symbols need scraping (stale guard).
          2. Submit all symbols to ThreadPoolExecutor.
          3. Each worker acquires a rate-limiter token before firing HTTP.
          4. Results accumulate in a thread-safe list as futures complete.
          5. After all threads finish, bulk-insert all results in one transaction.

        Args:
            trade_date: Date to tag data with. Defaults to today.
            symbols: Explicit symbol list (bare NSE, no .NS). If None, uses
                     stale-guard query against nse_universe.

        Returns:
            Dict with keys 'total', 'success', 'failed'.
        """
        if trade_date is None:
            trade_date = date.today()

        target_symbols = symbols if symbols is not None else _get_stale_symbols(trade_date)

        if not target_symbols:
            logger.info(
                "All symbols fresh (within %d days) — nothing to scrape",
                FundamentalsConfig.STALE_AFTER_DAYS,
            )
            return {"total": 0, "success": 0, "failed": 0}

        logger.info(
            "ScreenerService started | date=%s | symbols=%d | workers=%d | rate=%.1fs",
            trade_date,
            len(target_symbols),
            FundamentalsConfig.MAX_CONCURRENT_REQUESTS,
            FundamentalsConfig.RATE_LIMIT_SECONDS,
        )

        rate_limiter = _RateLimiter(FundamentalsConfig.RATE_LIMIT_SECONDS)
        collected: list[dict] = []          # accumulates parsed results (no lock needed —
        collected_lock = threading.Lock()   # appended only from as_completed callback)

        success = failed = 0

        with ThreadPoolExecutor(
            max_workers=FundamentalsConfig.MAX_CONCURRENT_REQUESTS,
            thread_name_prefix="screener",
        ) as executor:
            future_to_symbol = {
                executor.submit(_scrape_symbol_data, sym, rate_limiter): sym
                for sym in target_symbols
            }

            for i, future in enumerate(as_completed(future_to_symbol), start=1):
                sym, data = future.result()

                if data is not None:
                    with collected_lock:
                        collected.append(
                            {"symbol": sym, "trade_date": trade_date, **data}
                        )
                    success += 1
                    logger.info(
                        "[%d/%d] ✓ %-20s score=%d",
                        i, len(target_symbols), sym, data.get("quality_score", 0),
                    )
                else:
                    failed += 1
                    logger.warning("[%d/%d] ✗ %s", i, len(target_symbols), sym)

                if i % 50 == 0:
                    logger.info(
                        "Progress %d/%d — success=%d failed=%d",
                        i, len(target_symbols), success, failed,
                    )

        # All threads done — batch-insert everything in one go
        if collected:
            logger.info("Bulk-inserting %d records into stock_fundamentals", len(collected))
            _bulk_upsert_fundamentals(collected, trade_date)

        summary = {"total": len(target_symbols), "success": success, "failed": failed}
        logger.info(
            "ScreenerService complete | total=%d success=%d failed=%d",
            summary["total"], summary["success"], summary["failed"],
        )
        return summary
