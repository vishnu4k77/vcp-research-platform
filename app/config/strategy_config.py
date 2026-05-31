class StrategyConfig:

    SIGNAL_WEIGHTS = {

        "trend_signal": 20,

        "stage2_signal": 20,

        "liquidity_signal": 10,

        "quality_signal": 20,

        "vcp_signal": 15,

        "breakout_signal": 15
    }

    MIN_AVG_VOLUME_20 = 500000

    MIN_TRADED_VALUE = 50000000

    MIN_COMPOSITE_SCORE = 70

    ATR_LOOKBACK = 14

    VOLATILITY_LOOKBACK = 10

    BREAKOUT_LOOKBACK = 20

    STAGE_LOOKBACK = 252

    EMA_SLOPE_PERIOD = 20

    # VCP pattern parameters
    VCP_VOLUME_CONTRACTION_RATIO = 0.70     # relative_volume must be below this
    VCP_VOLATILITY_CONTRACTION_RATIO = 0.80  # volatility_contraction must be below this
    VCP_ATR_CONTRACTION_RATIO = 0.65        # current ATR / 90d peak ATR must be below this
    VCP_ATR_LOOKBACK = 90                   # bars to look back for peak ATR
    VCP_BREAKOUT_VOLUME_MULTIPLIER = 2.0    # relative_volume required on breakout day

    # Breakout pattern parameters
    BREAKOUT_VOLUME_MULTIPLIER = 1.5        # relative_volume required on breakout day
    BREAKOUT_MAX_EXTENSION_FROM_50EMA = 0.10  # max 10% above EMA 50 (avoid chasing)
    BREAKOUT_NEAR_RESISTANCE_THRESHOLD = 0.03  # within 3% of resistance = breakout ready

    # Weinstein stage classification
    STAGE_EMA200_SLOPE_NEUTRAL_BAND = 0.50  # % change over EMA_SLOPE_PERIOD considered flat

    # Yahoo Finance ticker suffix for NSE stocks.
    # nse_universe stores bare NSE symbols (e.g. 'RELIANCE').
    # All pipeline tables (daily_price_data, stock_features, stock_signals)
    # store Yahoo Finance format (e.g. 'RELIANCE.NS').
    # Used in SQL JOINs: REPLACE(ss.symbol, :nse_suffix, '') = nu.symbol
    YAHOO_NSE_SUFFIX = ".NS"

    # Stage 2 age thresholds — number of consecutive trading days in Stage 2.
    # Used for color-coding in scanner and for the "Stage 2 Age" filter.
    #   ≤ STAGE2_EARLY_MAX_DAYS  → "Early"    (green)  — freshest entry window
    #   ≤ STAGE2_MID_MAX_DAYS    → "Mid"      (orange) — still valid but watch momentum
    #   > STAGE2_MID_MAX_DAYS    → "Advanced" (red)    — may be approaching Stage 3 top
    STAGE2_EARLY_MAX_DAYS: int = 20
    STAGE2_MID_MAX_DAYS: int   = 60

    # Nifty 50 index ticker (Yahoo Finance format) — used by regime detection + RS signal
    NIFTY_TICKER = "^NSEI"

    # Relative Strength lookback — trading days for stock vs Nifty return comparison
    RS_LOOKBACK = 63   # ~3 months

    # Yahoo Finance ingestion parameters
    YAHOO_HISTORICAL_PERIOD = "5y"   # lookback for first-time ingestion of a new ticker
    YAHOO_RATE_LIMIT_SLEEP_SECONDS = 3
    YAHOO_RETRY_ATTEMPTS = 3
    YAHOO_RETRY_MIN_WAIT_SECONDS = 3
    YAHOO_RETRY_MAX_WAIT_SECONDS = 30

    # Concurrent workers for Yahoo Finance ingestion.
    # Downloads are serialized by the rate limiter (one Yahoo call per
    # YAHOO_RATE_LIMIT_SLEEP_SECONDS); workers run DB reads/writes in parallel.
    # Safe range: 2–5. Higher values do NOT increase Yahoo throughput.
    INGESTION_MAX_WORKERS = 4

    # Data quality thresholds (applied during ingestion)
    DATA_QUALITY_MAX_DAILY_MOVE_RATIO = 5.0
    DATA_QUALITY_MAX_VOLUME_MULTIPLIER = 100.0

    # SQL Server bulk-insert batch size.
    # Limit: 2100 params / ~20 columns = 105 rows max per statement.
    SQL_BATCH_SIZE = 100

    # Fundamental filters — used by QualitySignal when stock_fundamentals data exists.
    # All numeric thresholds are config-driven so changes never require code edits.
    FUNDAMENTAL_FILTERS = {
        "roe_min": 12,
        "roce_min": 12,
        "debt_to_equity_max": 1.0,
        "sales_growth_min": 10,
        "profit_growth_min": 10,
        "promoter_holding_min": 40,
        "opm_min": 10,
        "market_cap_min_cr": 500,
        "eps_acceleration": True,
        "promoter_pledge_max": 20,
    }


class FundamentalsConfig:
    """Screener.in scraper settings — all tuneable without code changes."""

    # Base URL for Screener.in company pages (bare NSE symbol, no .NS suffix)
    SCREENER_BASE_URL: str = "https://www.screener.in/company"

    # Minimum seconds between any two HTTP requests globally (across all threads).
    # This is a hard floor — the global rate limiter enforces it regardless of concurrency.
    RATE_LIMIT_SECONDS: float = 2.0

    # Number of concurrent scraper threads.
    # With RATE_LIMIT_SECONDS=2 and MAX_CONCURRENT=3, effective throughput is
    # 1 request / 2s with HTML parsing and DB writes overlapping across threads.
    # Keep ≤ 5 to stay respectful of Screener.in's free-tier limits.
    MAX_CONCURRENT_REQUESTS: int = 3

    # Tenacity retry settings for transient HTTP failures
    RETRY_ATTEMPTS: int = 3
    RETRY_MIN_WAIT_SECONDS: int = 5
    RETRY_MAX_WAIT_SECONDS: int = 30

    # HTTP timeout per request
    REQUEST_TIMEOUT_SECONDS: int = 15

    # Skip re-scraping a symbol whose data is fresher than this many days.
    # Set to 7 for weekly refresh, 30 for monthly.
    STALE_AFTER_DAYS: int = 30

    # SQL batch size for bulk inserts into stock_fundamentals
    SQL_BATCH_SIZE: int = 50

    # Minimum passing score (0–10) for a stock to get quality_signal = 1.
    # Each fundamental criterion adds 1 point; tune freely.
    QUALITY_PASS_SCORE: int = 5


class SectorConfig:
    """Sector rotation engine settings."""

    # How many weeks of historical sector scores to compare for momentum.
    # Momentum = (current week score) − (LOOKBACK_WEEKS ago score).
    MOMENTUM_LOOKBACK_WEEKS: int = 4

    # Sectors with fewer stocks than this are excluded from rotation ranking
    # (too thin a sample to be meaningful).
    MIN_STOCKS_FOR_RANKING: int = 5

    # Weights for the composite sector momentum score (must sum to 1.0).
    # Tune to emphasise trend alignment vs RS vs stage quality.
    MOMENTUM_WEIGHTS: dict = {
        "trend_pct":   0.30,
        "rs_pct":      0.35,
        "stage2_pct":  0.25,
        "avg_score":   0.10,
    }


class BacktestConfig:
    """Signal backtesting parameters — all tunable without code changes.

    Entry signals, stop/target rules, and simulation scope are all config-driven.
    The SQL table scanner_preset_config controls which signals can be used as entries.
    """

    # Which stock_signals columns are valid entry triggers for backtesting.
    # Ordered by specificity: breakout > VCP > BO ready.
    ENTRY_SIGNAL_OPTIONS: list[str] = [
        "breakout_signal",
        "vcp_signal",
        "breakout_ready_signal",
    ]
    DEFAULT_ENTRY_SIGNAL: str = "breakout_signal"

    # Exit rules — applied per trade in forward simulation order:
    #   1. Stop loss (checked first — conservative)
    #   2. Target profit
    #   3. Timeout (max holding period)
    STOP_LOSS_PCT: float = 0.07        # 7% below entry close
    TARGET_PCT: float = 0.20           # 20% above entry close
    MAX_HOLDING_DAYS: int = 60         # max calendar days before forced exit

    # Entry quality filter — only entries whose composite_score meets this threshold
    MIN_COMPOSITE_SCORE: float = 50.0

    # How many calendar days beyond end_date to load forward price data
    # (ensures trades opened near end_date still get complete exit simulation).
    FORWARD_PRICE_BUFFER_DAYS: int = 120

    # Number of trading days per year — used for Sharpe annualisation
    TRADING_DAYS_PER_YEAR: int = 252

    # Max symbols per bulk forward-price query.
    # SQL Server + pyodbc handles ~2000 params comfortably; 447 typical symbols
    # fit in one query at this limit, eliminating multi-batch round-trips.
    PRICE_LOAD_BATCH_SIZE: int = 2000


class DashboardConfig:
    """Streamlit dashboard UI parameters — all tuneable without code changes."""

    # Scanner table
    SCANNER_TOP_N: int = 100           # max rows displayed after preset reranking
    SCANNER_FETCH_N: int = 2000        # rows loaded from DB (must be ≥ universe size)
    SCANNER_DEFAULT_MIN_SCORE: float = 0.0

    # Date picker — how many distinct trading dates to populate the selector
    DATE_PICKER_LIMIT: int = 60

    # Stock detail chart — bars of price + EMA history to render
    CHART_LOOKBACK_DAYS: int = 252    # ~1 year

    # Pipeline health panel — how many recent runs to show
    PIPELINE_RUNS_LIMIT: int = 10

    # Cache TTL for Streamlit @st.cache_data (seconds)
    CACHE_TTL_SECONDS: int = 300

    # Sector heatmap — display label → DataFrame column mapping.
    # Keys are shown in the UI selectbox; values are computed column names.
    # Stage 2 is suppressed in BEAR, so RS is the most useful default.
    HEATMAP_METRICS: dict[str, str] = {
        "RS vs Nifty %":  "rs_pct",
        "Stage 2 %":      "stage2_pct",
        "Liquidity %":    "liquidity_pct",
        "Trend %":        "trend_pct",
    }
    HEATMAP_DEFAULT_METRIC: str = "rs_pct"

    # ── Scanner presets ────────────────────────────────────────────────────────
    # Each preset is a named signal-weight configuration.
    # Weights do NOT need to sum to 100 — the ranker normalises by total weight.
    # Add new presets here; the scanner sidebar populates from this dict automatically.
    # Signal column names must match stock_signals table columns.
    SCANNER_PRESETS: dict[str, dict[str, int]] = {

        # Default: mirrors the pipeline's SIGNAL_WEIGHTS — baseline for comparison
        "Composite (Default)": {
            "trend_signal":     20,
            "stage2_signal":    20,
            "liquidity_signal": 10,
            "quality_signal":   20,
            "vcp_signal":       15,
            "breakout_signal":  15,
            "rs_signal":         0,
        },

        # VCP Setup: heavy weight on Stage 2 base + VCP contraction.
        # Best for identifying stocks coiling before a move.
        "VCP Setup": {
            "trend_signal":     15,
            "stage2_signal":    20,
            "liquidity_signal":  5,
            "quality_signal":   10,
            "vcp_signal":       40,
            "breakout_signal":  10,
            "rs_signal":         0,
        },

        # Breakout Day: maximum weight on breakout + volume surge.
        # Use this during a broad market up-day to find live breakouts.
        "Breakout Day": {
            "trend_signal":     10,
            "stage2_signal":    10,
            "liquidity_signal": 10,
            "quality_signal":    5,
            "vcp_signal":       15,
            "breakout_signal":  50,
            "rs_signal":         0,
        },

        # RS Leaders: stocks outperforming Nifty 50 with good trend.
        # Best for identifying sector/index leaders in a bull run.
        "RS Leaders": {
            "trend_signal":     20,
            "stage2_signal":    15,
            "liquidity_signal": 10,
            "quality_signal":   10,
            "vcp_signal":        5,
            "breakout_signal":   5,
            "rs_signal":        35,
        },

        # Quality Growth: fundamentals-first filter (quality_signal weighted 40%).
        # Once Screener.in data is complete, this surfaces Minervini superperformers.
        "Quality Growth": {
            "trend_signal":     15,
            "stage2_signal":    15,
            "liquidity_signal":  5,
            "quality_signal":   40,
            "vcp_signal":       15,
            "breakout_signal":  10,
            "rs_signal":         0,
        },
    }