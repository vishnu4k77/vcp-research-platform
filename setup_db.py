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
from app.config.strategy_config import DashboardConfig, ScannerQueryConfig

logger = get_logger("setup_db")


# ── Table DDL ─────────────────────────────────────────────────────────────────
# Keys must match REQUIRED_TABLES entries.
# Tables not listed here must be created manually (they have complex DDL or
# already exist in the DB before this script was introduced).

TABLE_DDL = {

    # Pipeline execution audit — one row per run, updated at start and end.
    "pipeline_runs": """
        CREATE TABLE pipeline_runs (
            id             BIGINT         IDENTITY(1,1) NOT NULL,
            run_id         NVARCHAR(50)   NOT NULL,
            pipeline_name  VARCHAR(100)   NOT NULL,
            trade_date     DATE           NULL,
            start_time     DATETIME       NOT NULL CONSTRAINT df_pr_start DEFAULT GETDATE(),
            end_time       DATETIME       NULL,
            status         VARCHAR(20)    NOT NULL CONSTRAINT df_pr_status DEFAULT 'RUNNING',
            rows_processed BIGINT         NOT NULL CONSTRAINT df_pr_rows   DEFAULT 0,
            error_message  NVARCHAR(2000) NULL,
            CONSTRAINT pk_pipeline_runs    PRIMARY KEY (id),
            CONSTRAINT uq_pipeline_run_id  UNIQUE (run_id)
        )
    """,

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

    # Fundamentals from Screener.in — one row per symbol per scrape date.
    # scraped_at tracks when the HTTP fetch happened; trade_date is the reporting
    # period approximation (set to the scrape date by fetch_fundamentals.py).
    # STALE_AFTER_DAYS in FundamentalsConfig controls re-scrape cadence.
    "stock_fundamentals": """
        CREATE TABLE stock_fundamentals (
            id                  INT          IDENTITY(1,1) NOT NULL,
            symbol              VARCHAR(30)  NOT NULL,
            scraped_at          DATETIME     NOT NULL CONSTRAINT df_sfund_scraped DEFAULT GETDATE(),
            trade_date          DATE         NOT NULL,
            roe                 FLOAT        NULL,
            roce                FLOAT        NULL,
            debt_to_equity      FLOAT        NULL,
            sales_growth_3yr    FLOAT        NULL,
            profit_growth_3yr   FLOAT        NULL,
            opm                 FLOAT        NULL,
            eps_ttm             FLOAT        NULL,
            eps_prev_yr         FLOAT        NULL,
            promoter_holding    FLOAT        NULL,
            promoter_pledge_pct FLOAT        NULL,
            market_cap_cr       FLOAT        NULL,
            pe_ratio            FLOAT        NULL,
            quality_score       INT          NULL,
            CONSTRAINT pk_stock_fundamentals      PRIMARY KEY (id),
            CONSTRAINT uq_sfund_symbol_trade_date UNIQUE (symbol, trade_date)
        )
    """,

    # Fundamental filter thresholds — one row per criterion.
    # Update threshold values here (or via direct SQL UPDATE) without code changes.
    # Loaded by screener_service._load_filter_config(); falls back to StrategyConfig defaults.
    "fundamental_filter_config": """
        CREATE TABLE fundamental_filter_config (
            id           INT          IDENTITY(1,1) NOT NULL,
            filter_name  VARCHAR(50)  NOT NULL,
            threshold    FLOAT        NOT NULL,
            description  VARCHAR(200) NULL,
            is_active    BIT          NOT NULL CONSTRAINT df_ffc_active   DEFAULT 1,
            updated_at   DATETIME     NOT NULL CONSTRAINT df_ffc_updated  DEFAULT GETDATE(),
            CONSTRAINT pk_fundamental_filter_config PRIMARY KEY (id),
            CONSTRAINT uq_ffc_filter_name           UNIQUE (filter_name)
        )
    """,

    # Scanner preset weights — one row per (preset_name, signal_name).
    # Each preset is a named scoring strategy shown in the Scanner sidebar dropdown.
    # To add a new preset: INSERT rows here. To tune weights: UPDATE threshold.
    # is_default=1 marks the preset selected on first load; only one row should have 1.
    # sort_order controls the dropdown ordering across presets.
    "scanner_preset_config": """
        CREATE TABLE scanner_preset_config (
            id           INT          IDENTITY(1,1) NOT NULL,
            preset_name  VARCHAR(100) NOT NULL,
            signal_name  VARCHAR(50)  NOT NULL,
            weight       INT          NOT NULL CONSTRAINT df_spc_weight  DEFAULT 0,
            is_active    BIT          NOT NULL CONSTRAINT df_spc_active  DEFAULT 1,
            is_default   BIT          NOT NULL CONSTRAINT df_spc_default DEFAULT 0,
            sort_order   INT          NOT NULL CONSTRAINT df_spc_sort    DEFAULT 0,
            updated_at   DATETIME     NOT NULL CONSTRAINT df_spc_updated DEFAULT GETDATE(),
            CONSTRAINT pk_scanner_preset_config  PRIMARY KEY (id),
            CONSTRAINT uq_spc_preset_signal      UNIQUE (preset_name, signal_name)
        )
    """,

    # Earnings dates — one row per (symbol, earnings_date).
    # Populated by EarningsService; consumed by FeaturePipeline to set is_earnings_day.
    # Symbol stored in Yahoo Finance format (e.g. NYKAA.NS) to match pipeline tables.
    "stock_earnings_dates": """
        CREATE TABLE stock_earnings_dates (
            id            INT          IDENTITY(1,1) NOT NULL,
            symbol        VARCHAR(30)  NOT NULL,
            earnings_date DATE         NOT NULL,
            fetched_at    DATETIME     NOT NULL CONSTRAINT df_sed_fetched DEFAULT GETDATE(),
            CONSTRAINT pk_stock_earnings_dates PRIMARY KEY (id),
            CONSTRAINT uq_sed_symbol_date      UNIQUE (symbol, earnings_date)
        )
    """,

    # Data quality audit — one row per issue found per daily run.
    # check_type: 'stale_delete' | 'gap_detected' | 'thin_history'
    # rows_affected: rows deleted (stale_delete) or 0 (log-only checks)
    "data_quality_log": """
        CREATE TABLE data_quality_log (
            id            INT           IDENTITY(1,1) NOT NULL,
            run_date      DATE          NOT NULL CONSTRAINT df_dql_date DEFAULT CAST(GETDATE() AS DATE),
            check_type    VARCHAR(30)   NOT NULL,
            symbol        VARCHAR(30)   NOT NULL,
            description   VARCHAR(500)  NULL,
            rows_affected INT           NOT NULL CONSTRAINT df_dql_rows DEFAULT 0,
            CONSTRAINT pk_data_quality_log PRIMARY KEY (id)
        )
    """,

    # Sector rotation scores — one row per (trade_date, sector).
    # Computed by SectorMomentum after each SignalPipeline run.
    # momentum_score is the weighted composite; momentum_rank is cross-sector rank (1=best).
    "sector_momentum": """
        CREATE TABLE sector_momentum (
            id                  INT          IDENTITY(1,1) NOT NULL,
            trade_date          DATE         NOT NULL,
            sector              VARCHAR(100) NOT NULL,
            total_stocks        INT          NOT NULL CONSTRAINT df_sm_stocks DEFAULT 0,
            avg_composite_score FLOAT        NULL,
            trend_pct           FLOAT        NULL,
            stage2_pct          FLOAT        NULL,
            rs_pct              FLOAT        NULL,
            liquidity_pct       FLOAT        NULL,
            momentum_score      FLOAT        NULL,
            momentum_rank       INT          NULL,
            prev_momentum_score FLOAT        NULL,
            momentum_delta      FLOAT        NULL,
            CONSTRAINT pk_sector_momentum      PRIMARY KEY (id),
            CONSTRAINT uq_sm_date_sector       UNIQUE (trade_date, sector)
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
    # stock_features — raw OHLC columns (required by BreakoutSignal candle guards)
    ("stock_features", "open_price",                   "FLOAT NULL"),
    ("stock_features", "high_price",                   "FLOAT NULL"),
    ("stock_features", "low_price",                    "FLOAT NULL"),
    # stock_features — earnings day flag (populated by EarningsService from Yahoo Finance)
    ("stock_features", "is_earnings_day",              "BIT   NULL"),
    # stock_features — indicator columns
    ("stock_features", "weinstein_stage",              "INT   NULL"),
    # stock_features — 52-week price context (computed by VolatilityFeature)
    ("stock_features", "high_52w",                     "FLOAT NULL"),
    ("stock_features", "low_52w",                      "FLOAT NULL"),
    # stock_signals — sub-signal transparency columns
    ("stock_signals",  "ema_alignment_signal",         "BIT   NULL"),
    ("stock_signals",  "volume_contraction_signal",    "BIT   NULL"),
    ("stock_signals",  "volatility_contraction_signal","BIT   NULL"),
    ("stock_signals",  "breakout_ready_signal",        "BIT   NULL"),
    ("stock_signals",  "rs_signal",                    "BIT   NULL"),
    ("stock_signals",  "distance_from_pivot_pct",      "FLOAT NULL"),
    ("stock_signals",  "distance_from_52w_high_pct",   "FLOAT NULL"),
    # stock_fundamentals — quarterly EPS acceleration flag (1=accelerating, 0=not, NULL=no data)
    ("stock_fundamentals", "eps_acceleration",         "BIT   NULL"),
    # stock_signals — Stage 2 age: how long the current consecutive Stage 2 run has been active
    ("stock_signals", "stage2_days",          "INT  NULL"),   # trading days in current S2 streak
    ("stock_signals", "stage2_started_date",  "DATE NULL"),   # first date of current S2 streak
    # stock_signals — Phase 2A: RS value, RS new high, Minervini, Darvas
    ("stock_signals", "rs_value",         "FLOAT NULL"),  # excess return vs Nifty 50 (%)
    ("stock_signals", "rs_new_high",      "BIT   NULL"),  # RS value at 52-week rolling max
    ("stock_signals", "minervini_signal", "BIT   NULL"),  # all 8 Trend Template conditions
    ("stock_signals", "darvas_signal",    "BIT   NULL"),  # Darvas box breakout near 52w high
    # stock_signals — Phase 2B: price targets + daily-updating probability (TargetCalculator)
    # base_low_price is NOT stored — it is an intermediate used only inside TargetCalculator.
    # After adding these 6 columns total OUTPUT_COLUMNS = 30; SQL_BATCH_SIZE reduced to 65.
    ("stock_signals", "pivot_price",     "FLOAT NULL"),   # 20-day resistance level in ₹
    ("stock_signals", "base_range_pct",  "FLOAT NULL"),   # base width % of pivot
    ("stock_signals", "target_1_price",  "FLOAT NULL"),   # T1 in ₹ — actual limit order price
    ("stock_signals", "target_1_pct",    "FLOAT NULL"),   # % upside to T1 from close
    ("stock_signals", "target_2_price",  "FLOAT NULL"),   # T2 in ₹ — full measured move price
    ("stock_signals", "target_2_pct",    "FLOAT NULL"),   # % upside to T2 from close
    ("stock_signals", "risk_reward_t2",  "FLOAT NULL"),   # T2_pct / 7% stop
    ("stock_signals", "upside_prob_pct", "FLOAT NULL"),   # formula-based probability (updates daily)
    ("stock_signals", "ev_score",       "FLOAT NULL"),   # EV% = (P×T2%) − (1−P)×7% — primary rank
    # saved_queries — distinguishes seeded samples (1) from user-saved queries (0)
    ("saved_queries",  "is_sample",       "BIT NOT NULL CONSTRAINT df_sq_sample DEFAULT 0"),
]

# Saved scanner queries — user-named SQL strings stored from the dashboard UI.
# One row per named query; soft-deleted via is_active = 0.
# query_name has a UNIQUE constraint so saving the same name overwrites via MERGE.
TABLE_DDL_EXTRA = {
    "saved_queries": """
        CREATE TABLE saved_queries (
            id           INT            IDENTITY(1,1) NOT NULL,
            query_name   VARCHAR(100)   NOT NULL,
            description  VARCHAR(500)   NULL,
            sql_text     NVARCHAR(2000) NOT NULL,
            created_at   DATETIME       NOT NULL CONSTRAINT df_sq_created DEFAULT GETDATE(),
            is_active    BIT            NOT NULL CONSTRAINT df_sq_active  DEFAULT 1,
            CONSTRAINT pk_saved_queries PRIMARY KEY (id),
            CONSTRAINT uq_sq_name       UNIQUE      (query_name)
        )
    """,

    # Price Action signals — populated by scripts/run_pa_pipeline.py.
    # Completely independent of stock_signals — zero disturbance to existing pipeline.
    # scanner_service.get_scanner_data() LEFT JOINs this table so pa_signal
    # appears in scanner rows; returns 0 when PA pipeline has not yet run.
    # Truncated and recomputed on each PA pipeline run (same pattern as stock_signals).
    "stock_pa_signals": """
        CREATE TABLE stock_pa_signals (
            id                BIGINT      IDENTITY(1,1) NOT NULL,
            symbol            VARCHAR(20) NOT NULL,
            trade_date        DATE        NOT NULL,
            -- daily structure
            pa_hh_daily       BIT         NOT NULL CONSTRAINT df_pahh  DEFAULT 0,
            pa_hl_daily       BIT         NOT NULL CONSTRAINT df_pahl  DEFAULT 0,
            -- weekly structure (multi-timeframe gate — hard requirement for pa_signal=1)
            pa_hh_weekly      BIT         NOT NULL CONSTRAINT df_pawhh DEFAULT 0,
            pa_hl_weekly      BIT         NOT NULL CONSTRAINT df_pawhl DEFAULT 0,
            -- component flags
            pa_close_position FLOAT       NULL,
            pa_vol_quality    BIT         NOT NULL CONSTRAINT df_pavol DEFAULT 0,
            pa_momentum_accel BIT         NOT NULL CONSTRAINT df_pamom DEFAULT 0,
            pa_extension_ok   BIT         NOT NULL CONSTRAINT df_paext DEFAULT 0,
            -- composite
            pa_score          FLOAT       NOT NULL CONSTRAINT df_pascore DEFAULT 0,
            pa_signal         BIT         NOT NULL CONSTRAINT df_pasig   DEFAULT 0,
            CONSTRAINT pk_stock_pa_signals  PRIMARY KEY (id),
            CONSTRAINT uq_pa_symbol_date    UNIQUE (symbol, trade_date)
        )
    """,
}


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
    "market_states",              # regime scoring thresholds + trading env params
    "stock_fundamentals",         # Screener.in fundamentals — scraped monthly
    "fundamental_filter_config",  # fundamental threshold config — editable in SQL
    "scanner_preset_config",      # scanner preset weights — editable in SQL without code changes
    "stock_earnings_dates",       # Yahoo Finance earnings dates — used for is_earnings_day flag
    "data_quality_log",           # daily quality audit — stale deletes, gaps, thin history
    "sector_momentum",            # sector rotation scores — computed after each signal run
    "saved_queries",              # user-saved scanner SQL strings from the dashboard UI
    "stock_pa_signals",           # price action signals — populated by run_pa_pipeline.py
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

# Fundamental filter thresholds — 1 point per criterion, max 10.
# quality_pass_score: minimum criteria count for quality_signal = 1.
# Edit directly in SQL: UPDATE fundamental_filter_config SET threshold = X WHERE filter_name = 'Y'
_FUNDAMENTAL_FILTER_SEED = [
    # (filter_name, threshold, description)
    ("roe_min",              12.0, "Min Return on Equity % (>=)"),
    ("roce_min",             12.0, "Min Return on Capital Employed % (>=)"),
    ("debt_to_equity_max",    1.0, "Max Debt-to-Equity ratio (<=)"),
    ("sales_growth_min",     10.0, "Min 3-year Sales CAGR % (>=)"),
    ("profit_growth_min",    10.0, "Min 3-year Profit CAGR % (>=)"),
    ("promoter_holding_min", 40.0, "Min Promoter Holding % (>=)"),
    ("promoter_pledge_max",  20.0, "Max Promoter Pledge % (<=)"),
    ("opm_min",              10.0, "Min Operating Profit Margin % (>=)"),
    ("market_cap_min_cr",   500.0, "Min Market Cap in Crores (>=)"),
    ("quality_pass_score",    6.0, "Min criteria count (out of 10) for quality_signal = 1"),
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


def _seed_fundamental_filters() -> None:
    """Insert default fundamental filter thresholds if the table is empty."""
    try:
        with engine.begin() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM fundamental_filter_config")).scalar()
            if count > 0:
                logger.info("fundamental_filter_config already seeded (%d rows) — skipping", count)
                return

            for filter_name, threshold, description in _FUNDAMENTAL_FILTER_SEED:
                conn.execute(
                    text(
                        "INSERT INTO fundamental_filter_config "
                        "(filter_name, threshold, description, is_active) "
                        "VALUES (:name, :threshold, :desc, 1)"
                    ),
                    {"name": filter_name, "threshold": threshold, "desc": description},
                )

        logger.info("fundamental_filter_config seeded with %d rows", len(_FUNDAMENTAL_FILTER_SEED))

    except Exception as exc:
        logger.error("fundamental_filter_config seed failed: %s", exc, exc_info=True)


def _seed_scanner_presets() -> None:
    """Upsert scanner preset weights from DashboardConfig into scanner_preset_config.

    Uses INSERT WHERE NOT EXISTS per (preset_name, signal_name) row so this
    function is safe to re-run after adding new presets to DashboardConfig —
    existing rows are left untouched, only missing rows are inserted.

    Previous behaviour (skip-if-any-rows-exist) was replaced because it
    prevented new presets added to DashboardConfig from ever reaching the DB.

    To add a new preset via SQL without touching Python code:
        INSERT INTO scanner_preset_config (preset_name, signal_name, weight, sort_order)
        VALUES ('My Preset', 'trend_signal', 30, 10), ...
    """
    inserted = 0
    try:
        preset_names = list(DashboardConfig.SCANNER_PRESETS.keys())
        with engine.begin() as conn:
            for sort_idx, preset_name in enumerate(preset_names, start=1):
                weights    = DashboardConfig.SCANNER_PRESETS[preset_name]
                is_default = 1 if sort_idx == 1 else 0
                for signal_name, weight in weights.items():
                    result = conn.execute(
                        text(
                            "INSERT INTO scanner_preset_config "
                            "    (preset_name, signal_name, weight, is_active, is_default, sort_order) "
                            "SELECT :preset, :signal, :weight, 1, :is_default, :sort "
                            "WHERE NOT EXISTS ( "
                            "    SELECT 1 FROM scanner_preset_config "
                            "    WHERE preset_name = :preset AND signal_name = :signal "
                            ")"
                        ),
                        {
                            "preset":     preset_name,
                            "signal":     signal_name,
                            "weight":     weight,
                            "is_default": is_default,
                            "sort":       sort_idx,
                        },
                    )
                    inserted += result.rowcount

        total = sum(len(v) for v in DashboardConfig.SCANNER_PRESETS.values())
        logger.info(
            "scanner_preset_config sync | presets=%d | total_rows=%d | inserted=%d",
            len(DashboardConfig.SCANNER_PRESETS), total, inserted,
        )

    except Exception as exc:
        logger.error("scanner_preset_config seed failed: %s", exc, exc_info=True)


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

        all_ddl = {**TABLE_DDL, **TABLE_DDL_EXTRA}
        if table in all_ddl:
            try:
                with engine.begin() as conn:
                    conn.execute(text(all_ddl[table]))
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


# ── Performance indexes ───────────────────────────────────────────────────────
# Format: (index_name, table_name, CREATE INDEX DDL)
# _create_indexes() checks sys.indexes before executing — fully idempotent.

INDEX_DEFINITIONS: list[tuple[str, str, str]] = [

    # stock_signals — scanner filters by trade_date + composite_score (no existing index!)
    # Every scanner page-load did a full table scan before this.
    (
        "ix_ss_date_score",
        "stock_signals",
        """
        CREATE NONCLUSTERED INDEX ix_ss_date_score
            ON stock_signals (trade_date ASC, composite_score DESC)
            INCLUDE (
                symbol, institutional_candidate,
                trend_signal, stage2_signal, vcp_signal, breakout_signal,
                breakout_ready_signal, liquidity_signal, quality_signal, rs_signal,
                distance_from_pivot_pct, distance_from_52w_high_pct, stage2_days
            )
        """,
    ),

    # stock_signals — backtest entry query: WHERE trade_date BETWEEN ... AND signal=1 AND score>=N
    # Separate index so the query planner can use the entry_signal column cheaply.
    (
        "ix_ss_date_signal_score",
        "stock_signals",
        """
        CREATE NONCLUSTERED INDEX ix_ss_date_signal_score
            ON stock_signals (trade_date ASC, composite_score ASC)
            INCLUDE (symbol, breakout_signal, vcp_signal, breakout_ready_signal, stage2_days)
        """,
    ),

    # daily_price_data — backtest bulk-loads OHLCV for hundreds of symbols.
    # The existing uq_symbol_date unique index has no INCLUDE, so every row
    # required a RID/key lookup for high/low/close. This covering index eliminates that.
    (
        "ix_dpd_symbol_date_covering",
        "daily_price_data",
        """
        CREATE NONCLUSTERED INDEX ix_dpd_symbol_date_covering
            ON daily_price_data (symbol ASC, trade_date ASC)
            INCLUDE (open_price, high_price, low_price, close_price, volume)
        """,
    ),

    # stock_features — stock detail chart loads feature history per symbol descending.
    # The existing uq_stock_feature (symbol, trade_date) doesn't INCLUDE price/EMA columns.
    (
        "ix_sf_symbol_date_covering",
        "stock_features",
        """
        CREATE NONCLUSTERED INDEX ix_sf_symbol_date_covering
            ON stock_features (symbol ASC, trade_date DESC)
            INCLUDE (
                close_price, ema_10, ema_21, ema_50, ema_150, ema_200,
                atr_14, volatility_contraction, high_52w, low_52w,
                avg_volume_20, relative_volume
            )
        """,
    ),

    # market_regime — had ZERO indexes. Market Overview fetches TOP 1 ORDER BY trade_date DESC.
    (
        "ix_market_regime_date",
        "market_regime",
        """
        CREATE NONCLUSTERED INDEX ix_market_regime_date
            ON market_regime (trade_date DESC)
            INCLUDE (market_status, regime_score, nifty_above_50ema, nifty_above_200ema)
        """,
    ),

    # market_environment — had ZERO indexes. Same TOP 1 pattern.
    (
        "ix_market_environment_date",
        "market_environment",
        """
        CREATE NONCLUSTERED INDEX ix_market_environment_date
            ON market_environment (trade_date DESC)
            INCLUDE (
                market_state, regime_score, exposure_level,
                allow_breakouts, allow_pyramiding, cash_mode
            )
        """,
    ),

    # nse_universe — scanner JOINs on symbol but the existing uq_nse_symbol has no INCLUDEs.
    # Adding company_name + sector eliminates a lookup per scanner row.
    (
        "ix_nu_symbol_covering",
        "nse_universe",
        """
        CREATE NONCLUSTERED INDEX ix_nu_symbol_covering
            ON nse_universe (symbol ASC)
            INCLUDE (company_name, sector, is_active)
        """,
    ),

    # stock_fundamentals — fundamentals panel fetches TOP 1 ORDER BY trade_date DESC per symbol.
    # Existing uq_sfund_symbol_trade_date is (symbol, trade_date ASC); adding DESC variant helps.
    (
        "ix_sfund_symbol_date_desc",
        "stock_fundamentals",
        """
        CREATE NONCLUSTERED INDEX ix_sfund_symbol_date_desc
            ON stock_fundamentals (symbol ASC, trade_date DESC)
            INCLUDE (
                roe, roce, debt_to_equity, sales_growth_3yr, profit_growth_3yr,
                opm, eps_ttm, promoter_holding, promoter_pledge_pct,
                market_cap_cr, pe_ratio, quality_score, eps_acceleration, scraped_at
            )
        """,
    ),
]


def _create_indexes() -> None:
    """Create all performance indexes that don't already exist (idempotent).

    Checks sys.indexes by name before executing each CREATE INDEX so it is
    safe to run multiple times (e.g. repeated setup_db.py runs).
    """
    applied = skipped = failed = 0

    for index_name, table_name, ddl in INDEX_DEFINITIONS:
        try:
            with engine.connect() as conn:
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM sys.indexes "
                        "WHERE name = :name AND object_id = OBJECT_ID(:tbl)"
                    ),
                    {"name": index_name, "tbl": table_name},
                ).fetchone()

            if exists:
                logger.debug("Index %-50s already exists — skipping", index_name)
                skipped += 1
                continue

            with engine.begin() as conn:
                conn.execute(text(ddl))

            logger.info("CREATED  %-50s on %s", index_name, table_name)
            applied += 1

        except Exception as exc:
            logger.error("FAILED   %-50s: %s", index_name, exc)
            failed += 1

    logger.info(
        "Index pass complete — created=%d  skipped=%d  failed=%d",
        applied, skipped, failed,
    )


def _seed_sample_queries() -> None:
    """Upsert built-in sample queries from ScannerQueryConfig into saved_queries.

    Uses MERGE so the function is safe to re-run — existing rows are updated
    to the latest sql_text/description from config; new rows are inserted.
    Only touches rows where is_sample = 1 (user-saved rows are never modified).

    To add or update a sample query: edit ScannerQueryConfig.SAMPLE_QUERIES
    in strategy_config.py and re-run setup_db.py.  No UI code changes needed.
    """
    queries = ScannerQueryConfig.SAMPLE_QUERIES
    if not queries:
        logger.info("ScannerQueryConfig.SAMPLE_QUERIES is empty — skipping seed")
        return

    stmt = text("""
        MERGE saved_queries AS target
        USING (SELECT :name AS query_name) AS src
            ON  target.query_name = src.query_name
            AND target.is_sample  = 1
        WHEN MATCHED THEN
            UPDATE SET
                sql_text    = :sql_text,
                description = :desc,
                is_active   = 1
        WHEN NOT MATCHED THEN
            INSERT (query_name, description, sql_text, is_sample)
            VALUES (:name,      :desc,       :sql_text, 1);
    """)

    upserted = 0
    try:
        with engine.begin() as conn:
            for q in queries:
                conn.execute(stmt, {
                    "name":     q["query_name"],
                    "desc":     q.get("description", ""),
                    "sql_text": q["sql_text"],
                })
                upserted += 1
        logger.info("sample queries upserted | count=%d", upserted)
    except Exception as exc:
        logger.error("_seed_sample_queries failed: %s", exc, exc_info=True)


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
    _seed_fundamental_filters()
    _seed_scanner_presets()
    _seed_sample_queries()

    print("\nCreating performance indexes:")
    _create_indexes()

    print("\nSetup complete.")
    print("Next steps:")
    print("  1. python scripts/fetch_nse_tickers.py   # populates nse_universe + nse_index_membership")
    print("  2. python run.py                          # starts the daily pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
