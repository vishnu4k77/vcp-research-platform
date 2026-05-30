"""
setup_db.py — Database migration and validation tool.

Run once on a fresh install, or after any schema change.
Fully idempotent: safe to run multiple times.

Usage:
    python setup_db.py

What it does:
  1. Verifies SQL Server connection.
  2. Creates any missing tables (with full schema, constraints, defaults).
  3. Adds any missing columns via ALTER TABLE (never drops existing data).
  4. Seeds reference tables (market_states, nse_index_ref) if empty.
  5. Reports final status.

NSE data loading cadence:
  - setup_db.py          → run ONCE at install to create schema + seed references
  - fetch_nse_tickers.py → run ONCE after setup, then MONTHLY after NSE rebalances
  - run.py (pipeline)    → runs DAILY EOD; reads nse_universe, never downloads it

Deprecated columns on nse_universe (nifty50, nifty500, midcap150, smallcap250):
  These boolean flags are superseded by the nse_index_membership table.
  fetch_nse_tickers.py no longer writes them. Drop manually in SSMS when ready:

    ALTER TABLE nse_universe
        DROP COLUMN nifty50, nifty500, midcap150, smallcap250;
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import inspect, text

from app.config.db import engine
from app.config.logging_config import get_logger

logger = get_logger("setup_db")


# ── Table DDL ─────────────────────────────────────────────────────────────────
# Keys must match REQUIRED_TABLES entries.
# Tables not listed here must be created manually (they have complex DDL or
# already exist in the DB before this script was introduced).

TABLE_DDL = {

    "nse_universe": """
        CREATE TABLE nse_universe (
            id           BIGINT       IDENTITY(1,1) NOT NULL,
            symbol       VARCHAR(30)  NOT NULL,
            company_name VARCHAR(200) NULL,
            sector       VARCHAR(100) NULL,
            isin         VARCHAR(20)  NULL,
            is_active    BIT          NOT NULL DEFAULT 1,
            first_seen   DATE         NOT NULL DEFAULT CAST(GETDATE() AS DATE),
            last_seen    DATE         NOT NULL DEFAULT CAST(GETDATE() AS DATE),
            updated_at   DATETIME     NOT NULL DEFAULT GETDATE(),
            CONSTRAINT pk_nse_universe PRIMARY KEY (id),
            CONSTRAINT uq_nse_symbol   UNIQUE (symbol)
        )
    """,

    # Index constituency master — one row per known NSE index.
    # Add new rows here to support additional indices without schema changes.
    "nse_index_ref": """
        CREATE TABLE nse_index_ref (
            id         INT          IDENTITY(1,1) NOT NULL,
            index_code VARCHAR(20)  NOT NULL,
            index_name VARCHAR(100) NOT NULL,
            is_active  BIT          NOT NULL CONSTRAINT df_nir_active  DEFAULT 1,
            created_at DATETIME     NOT NULL CONSTRAINT df_nir_created DEFAULT GETDATE(),
            CONSTRAINT pk_nse_index_ref  PRIMARY KEY (id),
            CONSTRAINT uq_nse_index_code UNIQUE (index_code)
        )
    """,

    # Normalized index membership — replaces the nifty50/nifty500/midcap150/smallcap250
    # boolean columns that used to live directly on nse_universe.
    # is_current_member=0 means the stock left the index; the row is kept for history.
    "nse_index_membership": """
        CREATE TABLE nse_index_membership (
            id                INT         IDENTITY(1,1) NOT NULL,
            symbol            VARCHAR(30) NOT NULL,
            index_code        VARCHAR(20) NOT NULL,
            is_current_member BIT         NOT NULL CONSTRAINT df_nim_current DEFAULT 1,
            first_added_date  DATE        NOT NULL CONSTRAINT df_nim_added   DEFAULT CAST(GETDATE() AS DATE),
            last_removed_date DATE        NULL,
            updated_at        DATETIME    NOT NULL CONSTRAINT df_nim_updated  DEFAULT GETDATE(),
            CONSTRAINT pk_nse_index_membership PRIMARY KEY (id),
            CONSTRAINT uq_nim_symbol_index     UNIQUE (symbol, index_code)
        )
    """,

    # Regime classification config — state_code, score bands, trading environment params.
    # Seeded by _seed_market_states(). Tune exposure/breakout flags directly in SQL.
    "market_states": """
        CREATE TABLE market_states (
            id                INT         IDENTITY(1,1) NOT NULL,
            state_code        VARCHAR(30) NOT NULL,
            display_name      VARCHAR(50) NOT NULL,
            score_min         INT         NULL,
            score_max         INT         NULL,
            exposure_level    FLOAT       NOT NULL,
            max_position_size FLOAT       NOT NULL,
            allow_breakouts   BIT         NOT NULL CONSTRAINT df_ms_breakouts  DEFAULT 1,
            allow_pyramiding  BIT         NOT NULL CONSTRAINT df_ms_pyramiding DEFAULT 0,
            cash_mode         BIT         NOT NULL CONSTRAINT df_ms_cash       DEFAULT 0,
            is_active         BIT         NOT NULL CONSTRAINT df_ms_active     DEFAULT 1,
            sort_order        INT         NOT NULL CONSTRAINT df_ms_sort       DEFAULT 0,
            created_at        DATETIME    NOT NULL                             DEFAULT GETDATE(),
            CONSTRAINT pk_market_states     PRIMARY KEY (id),
            CONSTRAINT uq_market_state_code UNIQUE (state_code),
            CONSTRAINT chk_ms_score_range   CHECK (
                score_min IS NULL OR (
                    score_min >= 0 AND score_max <= 100 AND score_min <= score_max
                )
            ),
            CONSTRAINT chk_ms_exposure  CHECK (exposure_level    BETWEEN 0.0 AND 1.0),
            CONSTRAINT chk_ms_position  CHECK (max_position_size BETWEEN 0.0 AND 1.0)
        )
    """,
}


# ── Column migrations ─────────────────────────────────────────────────────────
# Format: (table, column, sql_type)
# Only ADD operations — never drops existing data.

COLUMN_MIGRATIONS = [
    # stock_features
    ("stock_features", "weinstein_stage",               "INT   NULL"),
    # stock_signals
    ("stock_signals",  "ema_alignment_signal",           "BIT   NULL"),
    ("stock_signals",  "volume_contraction_signal",      "BIT   NULL"),
    ("stock_signals",  "volatility_contraction_signal",  "BIT   NULL"),
    ("stock_signals",  "breakout_ready_signal",          "BIT   NULL"),
    ("stock_signals",  "rs_signal",                      "BIT   NULL"),
    ("stock_signals",  "distance_from_pivot_pct",        "FLOAT NULL"),
    ("stock_signals",  "distance_from_52w_high_pct",     "FLOAT NULL"),
]


# ── Required tables ───────────────────────────────────────────────────────────
# All must exist before the pipeline can run.
# Tables without a TABLE_DDL entry must be created manually (see sql.sql).

REQUIRED_TABLES = [
    "daily_price_data",
    "stock_features",
    "stock_signals",
    "market_regime",
    "market_environment",
    "pipeline_runs",
    "signal_audit",
    "nse_universe",          # ticker master — source of truth for the ingestion loop
    "nse_index_ref",         # index reference (NIFTY500, MIDCAP150, …)
    "nse_index_membership",  # normalized per-(symbol, index_code) constituency
    "market_states",         # regime scoring thresholds + trading env params
]


# ── Seed data ─────────────────────────────────────────────────────────────────

_MARKET_STATES_SEED = [
    # (state_code, display_name, score_min, score_max, exposure, position, breakouts, pyramiding, cash, sort)
    # score_min/max: 0-100 scale, 5 conditions × 20 pts each.
    # None = not score-based (Phase-2 only; is_active set to 0 below).
    ("STRONG_BULL",     "Strong Bull",     100, 100, 1.0, 1.0, 1, 1, 0, 1),
    ("BULL",            "Bull",            80,  99,  0.8, 0.8, 1, 0, 0, 2),
    ("NEUTRAL",         "Neutral",         60,  79,  0.5, 0.5, 1, 0, 0, 3),
    ("DEFENSIVE",       "Defensive",       40,  59,  0.3, 0.3, 0, 0, 0, 4),
    ("CORRECTION",      "Correction",      20,  39,  0.2, 0.2, 0, 0, 0, 5),
    ("BEAR",            "Bear",            0,   19,  0.0, 0.0, 0, 0, 1, 6),
    ("HIGH_VOLATILITY", "High Volatility", None, None, 0.2, 0.2, 0, 0, 0, 7),
]

_NSE_INDEX_REF_SEED = [
    # (index_code, index_name)
    ("NIFTY50",     "NIFTY 50"),
    ("NIFTY500",    "NIFTY 500"),
    ("MIDCAP150",   "NIFTY Midcap 150"),
    ("SMALLCAP250", "NIFTY Smallcap 250"),
]


# ── Seed functions ────────────────────────────────────────────────────────────

def _seed_market_states() -> None:
    """Insert default market_states rows if the table is empty."""

    try:
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM market_states")).scalar()
            if count > 0:
                logger.info("market_states already seeded (%d rows) — skipping", count)
                return

            for (state_code, display_name, score_min, score_max,
                 exposure, position, breakouts, pyramiding, cash, sort) in _MARKET_STATES_SEED:
                conn.execute(
                    text("""
                        INSERT INTO market_states (
                            state_code, display_name,
                            score_min, score_max,
                            exposure_level, max_position_size,
                            allow_breakouts, allow_pyramiding, cash_mode,
                            is_active, sort_order
                        ) VALUES (
                            :state_code, :display_name,
                            :score_min,  :score_max,
                            :exposure,   :position,
                            :breakouts,  :pyramiding, :cash,
                            :is_active,  :sort
                        )
                    """),
                    {
                        "state_code":   state_code,
                        "display_name": display_name,
                        "score_min":    score_min,
                        "score_max":    score_max,
                        "exposure":     exposure,
                        "position":     position,
                        "breakouts":    breakouts,
                        "pyramiding":   pyramiding,
                        "cash":         cash,
                        "is_active":    0 if score_min is None else 1,
                        "sort":         sort,
                    },
                )

        logger.info("market_states seeded with %d rows", len(_MARKET_STATES_SEED))

    except Exception as exc:
        logger.error("market_states seed failed: %s", exc, exc_info=True)


def _seed_nse_index_ref() -> None:
    """Insert default NSE index reference rows if the table is empty."""

    try:
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM nse_index_ref")).scalar()
            if count > 0:
                logger.info("nse_index_ref already seeded (%d rows) — skipping", count)
                return

            for index_code, index_name in _NSE_INDEX_REF_SEED:
                conn.execute(
                    text(
                        "INSERT INTO nse_index_ref (index_code, index_name, is_active) "
                        "VALUES (:code, :name, 1)"
                    ),
                    {"code": index_code, "name": index_name},
                )

        logger.info("nse_index_ref seeded with %d indices", len(_NSE_INDEX_REF_SEED))

    except Exception as exc:
        logger.error("nse_index_ref seed failed: %s", exc, exc_info=True)


# ── Core setup functions ──────────────────────────────────────────────────────

def _check_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
        return True
    except Exception as exc:
        logger.error("Database connection FAILED: %s", exc)
        return False


def _create_missing_tables(existing: set) -> list[str]:
    """
    Creates any table in TABLE_DDL that does not yet exist.
    Returns the list of tables still missing after this step.
    """
    still_missing = []

    for table in REQUIRED_TABLES:
        if table in existing:
            logger.info("  %-30s OK", table)
            continue

        if table in TABLE_DDL:
            try:
                with engine.begin() as conn:
                    conn.execute(text(TABLE_DDL[table]))
                logger.info("  %-30s CREATED", table)
                existing.add(table)
            except Exception as exc:
                logger.error("  %-30s FAILED to create: %s", table, exc)
                still_missing.append(table)
        else:
            logger.warning("  %-30s MISSING  (no DDL — create manually)", table)
            still_missing.append(table)

    return still_missing


def _run_column_migrations() -> None:
    """Add any missing columns. Checks sys.columns before each ALTER TABLE."""

    inspector = inspect(engine)
    applied = 0

    for table, column, sql_type in COLUMN_MIGRATIONS:
        try:
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
        except Exception:
            logger.warning("Table %s not accessible — skipping column %s", table, column)
            continue

        if column in existing_cols:
            logger.debug("  %-30s %-40s already exists", table, column)
            continue

        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD {column} {sql_type}"))
            logger.info("  ADDED  %-28s to %s  (%s)", column, table, sql_type)
            applied += 1
        except Exception as exc:
            logger.error("  FAILED adding %s to %s: %s", column, table, exc)

    if applied:
        logger.info("%d column migration(s) applied", applied)
    else:
        logger.info("No column migrations needed — schema is current")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:

    print("=" * 60)
    print("NSE Breakout Scanner — Database Setup")
    print("=" * 60)

    if not _check_connection():
        print("\nERROR: Cannot connect to SQL Server. Check .env settings.")
        return 1

    print("\nChecking and creating tables:")
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    still_missing = _create_missing_tables(existing)

    if still_missing:
        print(f"\nWARNING: {len(still_missing)} table(s) still missing:")
        for t in still_missing:
            print(f"  Missing: {t}")
        print("\nCreate them using the DDL in sql.sql, then re-run setup_db.py.")
        return 1

    print("\nRunning column migrations:")
    _run_column_migrations()

    print("\nSeeding reference data:")
    _seed_market_states()
    _seed_nse_index_ref()

    print("\nSetup complete.")
    print("Next steps:")
    print("  1. python scripts/fetch_nse_tickers.py   # populates nse_universe + nse_index_membership")
    print("  2. python run.py                          # starts the daily pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
