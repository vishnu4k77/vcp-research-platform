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
    # Candle quality guards (gap-trap protection)
    BREAKOUT_CLOSE_POSITION_MIN = 0.50      # close must be in upper 50% of day's range
    BREAKOUT_MAX_GAP_PCT = 0.03             # gap-and-fail: if open gaps >3% AND closes below open → trap
    # Stage 2 age gate — breakouts from late Stage 2 (>60 days) are entering
    # Stage 3 distribution.  Reuses STAGE2_MID_MAX_DAYS so the cutoff stays
    # consistent between the scanner colour-coding and the signal filter.
    # 0 = not in Stage 2 (allowed — might be entering Stage 2 right now).
    # Tune via STAGE2_MID_MAX_DAYS (currently 60).
    BREAKOUT_MAX_STAGE2_AGE_DAYS: int = 60  # alias of STAGE2_MID_MAX_DAYS for clarity

    # Volume dry-up in base — quiet accumulation before a breakout.
    # Ratio: avg_volume_10 / avg_volume_20.
    # < 0.75 = recent 10-day avg is below the 20-day avg → volume contracting → healthy.
    # > 0.75 = volume still elevated → distribution risk, not quiet accumulation.
    BREAKOUT_BASE_VOLUME_DRY_UP: float = 0.75

    # Minervini Trend Template — missing conditions added to breakout_signal:
    #   Condition 6: close must be above EMA50 (stock still in near-term uptrend)
    #   Condition 8: close must be within 25% of 52-week high (near new highs,
    #                NOT recovering from a crash — eliminates post-crash bounce entries)
    # Source: "Trade Like a Stock Market Wizard" (Minervini, 2013)
    BREAKOUT_CLOSE_ABOVE_50EMA: bool = True      # enforce cond 6 (close > ema_50)
    BREAKOUT_NEAR_52W_HIGH_MAX: float = 0.25     # cond 8: max 25% below 52w high

    # Earnings-day filter — uses is_earnings_day flag from stock_features.
    # is_earnings_day is populated by EarningsService from Yahoo Finance earnings_dates.
    # Minervini: never enter on the earnings day; volume is event-driven, not accumulation.
    BREAKOUT_FILTER_EARNINGS_DAYS: bool = True

    # ── Base quality filters (pivot quality) ──────────────────────────────────
    # O'Neil / Minervini: a valid breakout requires a proper consolidation BASE,
    # not just any 20-day high.  These two guards eliminate the two most common
    # false-breakout scenarios:
    #
    #   Scenario A — post-crash recovery:
    #     Stock gaps down on news, bounces for 10 days, hits the 20-day max.
    #     No real base formed.  fix: prior uptrend minimum.
    #
    #   Scenario B — choppy range:
    #     Stock oscillates in a wide range for weeks with no directional bias.
    #     fix: tight base range (max 15% spread inside the 20-day window).
    #
    # Tune both thresholds here without touching signal logic.

    # Maximum allowed price range inside the 20-day base window.
    # Formula: (rolling_max_close - rolling_min_close) / rolling_max_close
    # < 0.15 = tight consolidation (flat base or handle)
    # > 0.15 = wide chop / V-shaped recovery → reject
    BREAKOUT_MAX_BASE_RANGE_PCT: float = 0.15

    # Minimum gain from the 52-week low before a breakout is valid.
    # A stock only 10% above its yearly low has no institutional accumulation.
    # O'Neil: the stock must have a prior meaningful uptrend before forming the base.
    # 0.30 = stock must be 30%+ above its 52-week low.
    BREAKOUT_PRIOR_UPTREND_MIN: float = 0.30

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

    # Gap detection lookback — how many recent NSE trading days to audit after
    # each ingestion run.  DataGapFiller queries market_regime for this many
    # trading days and verifies every active ticker has a row for each.
    # 5 = catches gaps from this week.  Increase to 10 to audit 2 full weeks.
    INGESTION_GAP_CHECK_DAYS: int = 5

    # Data quality thresholds (applied during ingestion)
    DATA_QUALITY_MAX_DAILY_MOVE_RATIO = 5.0
    DATA_QUALITY_MAX_VOLUME_MULTIPLIER = 100.0

    # SQL Server bulk-insert batch size.
    # Limit: 2100 params / 30 columns (Phase 2B: +6 target columns) = 70 rows max.
    # Using 65 for headroom against future column additions.
    SQL_BATCH_SIZE = 65

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


class EarningsConfig:
    """Yahoo Finance earnings dates fetch and caching settings."""

    # Re-fetch a symbol's earnings dates if the last fetch was older than this.
    # Quarterly cadence means weekly refresh is more than sufficient.
    STALE_AFTER_DAYS: int = 7

    # Seconds between Yahoo Finance Ticker() calls (separate from OHLCV rate limit).
    RATE_LIMIT_SECONDS: float = 2.0

    # Tenacity retry settings for transient Yahoo failures
    RETRY_ATTEMPTS: int = 3
    RETRY_MIN_WAIT_SECONDS: int = 3
    RETRY_MAX_WAIT_SECONDS: int = 30

    # SQL bulk-insert batch size for stock_earnings_dates
    SQL_BATCH_SIZE: int = 100

    # Discard earnings dates further in the future than this many days.
    # Keeps the table clean of Yahoo's speculative far-future estimates.
    MAX_FUTURE_DAYS: int = 365


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

    # SQL query text-area — height auto-scales with query length
    QUERY_BOX_MIN_HEIGHT_PX: int = 68    # floor: shows ~2 lines
    QUERY_BOX_MAX_HEIGHT_PX: int = 180   # ceiling: prevents the box eating the screen
    QUERY_BOX_CHARS_PER_LINE: int = 110  # approx chars per wrapped line at default font
    QUERY_BOX_LINE_HEIGHT_PX: int = 24   # pixels per text line (Streamlit default)

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

        # Minervini A+: all 8 Trend Template conditions met + VCP coiling.
        # Highest quality setup — stock must pass every structural test.
        "Minervini A+": {
            "minervini_signal": 50,
            "vcp_signal":       30,
            "rs_signal":        20,
        },

        # Darvas Breakout: near 52w highs, box formed, breakout on volume.
        # Pure price-action momentum — no fundamental filter.
        "Darvas Breakout": {
            "darvas_signal":    60,
            "minervini_signal": 30,
            "rs_signal":        10,
        },
    }


class MinerviniConfig:
    """Minervini Trend Template — 8-condition qualification filter.

    Source: 'Trade Like a Stock Market Wizard' (Minervini, 2013).
    All thresholds are config-driven — tune in this class without touching
    any signal logic.

    Conditions mapped to our data:
      1. close > EMA150  AND  close > EMA200
      2. EMA150 > EMA200
      3. EMA200 slope positive for at least EMA200_RISING_LOOKBACK bars
      4. EMA50 > EMA150  AND  EMA50 > EMA200
      5. close > EMA50
      6. close >= 52w_low * (1 + MIN_GAIN_FROM_52W_LOW)
      7. close <= 52w_high * (1 + MAX_BELOW_52W_HIGH)  [i.e. within X% of high]
      8. rs_signal = 1  (stock outperforms Nifty 50)
    """

    # Condition 3: EMA200 must be higher than it was this many bars ago.
    # 20 bars ≈ 1 calendar month — sufficient to confirm a rising 200-day MA.
    EMA200_RISING_LOOKBACK: int = 20

    # Condition 6: minimum gain from 52-week low before a setup is valid.
    # A stock 10% above its yearly low has no institutional accumulation.
    # 0.30 = must be 30%+ above 52w low (prior meaningful uptrend exists).
    MIN_GAIN_FROM_52W_LOW: float = 0.30

    # Condition 7: maximum % below 52-week high.
    # Stocks near new highs have no overhead supply.
    # 0.25 = must be within 25% of 52w high.
    MAX_BELOW_52W_HIGH: float = 0.25

    # Condition 8 proxy: stock must outperform Nifty 50 (rs_signal = 1).
    # Set False to skip the RS check (useful for testing conditions 1-7 only).
    REQUIRE_RS_OUTPERFORMANCE: bool = True

    # rs_new_high lookback: how many bars of RS history to compare for new high.
    # 252 = 1 trading year — consistent with IBD 52-week RS Line comparison.
    # min_periods = RS_NEW_HIGH_LOOKBACK // 2 (126) so new stocks get partial coverage.
    RS_NEW_HIGH_LOOKBACK: int = 252


class DarvasConfig:
    """Darvas Box breakout — stocks near 52-week highs, box consolidation, breakout on volume.

    Source: 'How I Made $2,000,000 in the Stock Market' (Darvas, 1960).
    Key insight: stocks at/near new 52w highs have no overhead supply — every
    shareholder is sitting on a profit and has no incentive to sell at a loss.
    A tight consolidation (box) followed by a volume breakout is the entry trigger.

    Difference vs BreakoutSignal:
      - BreakoutSignal allows up to 25% below 52w high; DarvasSignal requires 10%.
      - DarvasSignal targets stocks hitting NEW highs, not recovering from lows.
      - Less restrictive on EMA stack (only requires close > EMA150).
    """

    # Maximum % below 52-week high.  Darvas specifically traded near new highs.
    # 0.10 = within 10% of the 52w high.
    NEAR_HIGH_MAX_PCT: float = 0.10

    # Box definition: (box_top - box_bottom) / box_top must be below this.
    # 0.12 = 12% max price range inside the consolidation window.
    BOX_RANGE_MAX_PCT: float = 0.12

    # Number of prior bars that define the box (consolidation window).
    # 20 bars ≈ 4 calendar weeks.
    BOX_LOOKBACK: int = 20

    # Relative volume required on the breakout day.
    # 1.5 = 1.5× the 20-day average — confirms institutional participation.
    VOLUME_MULTIPLIER: float = 1.5

    # Basic EMA prerequisite: close must be above EMA150 (intermediate uptrend).
    # Less strict than BreakoutSignal's full EMA50 > EMA150 > EMA200 stack.
    REQUIRE_ABOVE_EMA150: bool = True

    # Suppress Darvas breakout on earnings day (event-driven volume ≠ accumulation).
    FILTER_EARNINGS_DAYS: bool = True


class TargetConfig:
    """Price target and probability computation — appended to every signal row daily.

    Targets use the measured-move method (O'Neil / Minervini):
        T1 = pivot_price + base_range × T1_MULTIPLIER   (half measured move)
        T2 = pivot_price + base_range × T2_MULTIPLIER   (full measured move)

    base_range  = pivot_price - base_low_price  (20-day base width in ₹)
    pivot_price = 20-day prior rolling-max close (resistance, shift(1) — no lookahead)

    upside_prob_pct is formula-driven and updates daily because its inputs
    (composite_score, signals, stage2_days) recompute fresh on every pipeline run.
    It is NOT a backtest-calibrated statistic — show it as "Est. Prob%" in the UI.

    To recalibrate after backtest history accumulates:
        1. Run scripts/run_backtest.py over a 2-3 year window
        2. Compute actual T2 hit rates grouped by signal type
        3. Replace BASE_PROB_PCT and PROB_ADJ_* with empirical values
    """

    T1_MULTIPLIER: float = 0.50   # T1 = pivot + base_range × 0.5
    T2_MULTIPLIER: float = 1.00   # T2 = pivot + base_range × 1.0

    # Must mirror BacktestConfig.STOP_LOSS_PCT — keeps R:R denominator consistent
    STOP_LOSS_PCT: float = 0.07   # 7% below entry close

    # ── Probability model — baseline + per-signal quality adjustments ──────────
    # Baseline calibrated to our own backtest: 44.2% win rate with breakout_signal
    # (20% target, 7% stop, 60-day hold).  Adjustments are additive, capped at MAX_PROB_PCT.
    BASE_PROB_PCT: float = 40.0

    PROB_ADJ_BREAKOUT_SIGNAL:  float =  5.0  # breakout confirmed (vs setup only)
    PROB_ADJ_MINERVINI_SIGNAL: float =  8.0  # all 8 Trend Template conditions met
    PROB_ADJ_RS_NEW_HIGH:      float =  5.0  # RS line at 1-year rolling high
    PROB_ADJ_DARVAS_SIGNAL:    float =  7.0  # near 52w high — minimal overhead supply
    PROB_ADJ_HIGH_SCORE:       float =  5.0  # composite_score >= HIGH_SCORE_THRESHOLD
    PROB_ADJ_FRESH_STAGE2:     float =  3.0  # stage2_days <= FRESH_STAGE2_DAYS (early run)
    PROB_ADJ_LATE_STAGE2:      float = -5.0  # stage2_days > LATE_STAGE2_DAYS (distribution risk)

    HIGH_SCORE_THRESHOLD: float = 80.0
    FRESH_STAGE2_DAYS:    int   = 20
    LATE_STAGE2_DAYS:     int   = 60

    MAX_PROB_PCT: float = 75.0   # cap — markets are never certain
    MIN_PROB_PCT: float = 20.0   # floor — even weak setups carry some base probability


class ScannerQueryConfig:
    """Built-in sample queries seeded into saved_queries (is_sample = 1).

    Each entry covers a distinct SQL syntax feature so users can discover
    the query language by example.  Update this list to change what appears
    in the "Sample queries" expander — no UI code changes required.

    Keys per dict:
        query_name  : unique identifier (max 100 chars) — shown as the button label.
        description : one-line explanation shown as tooltip.
        sql_text    : full WHERE … ORDER BY string accepted by the scanner parser.

    Syntax coverage:
        Boolean flags       — IC, Trend, Stage2, VCP, Breakout, "BO Ready",
                              Liquid, Quality, RS  (= 1 or = 0)
        Numeric comparisons — Score, "Dist Pivot%", "Dist 52w%",
                              "Fund Score", ROE%, ROCE%, Promoter%
        BETWEEN             — "S2 Age(d)" BETWEEN x AND y
        Negative range      — "Dist 52w%" BETWEEN -25 AND -5
        Tick alias          — Liquid = tick  (same as Liquid = 1)
        OR group            — (VCP = 1 OR Breakout = 1 OR "BO Ready" = 1)
        Multiple ORDER BY   — Score DESC / "S2 Age(d)" ASC / ROE% DESC / etc.
    """

    SAMPLE_QUERIES: list[dict] = [
        # ── 1. BETWEEN + boolean + ASC sort ──────────────────────────────────
        {
            "query_name": "01 · Fresh Stage 2 + RS leaders",
            "description": (
                "Early Stage 2 stocks (1–15 days in) outperforming Nifty 50. "
                "Lower S2 Age = fresher entry. Demonstrates BETWEEN syntax."
            ),
            "sql_text": (
                'WHERE Stage2 = 1 AND "S2 Age(d)" BETWEEN 1 AND 15 '
                'AND RS = 1 AND Liquid = 1 ORDER BY "S2 Age(d)" ASC'
            ),
        },
        # ── 2. Dist Pivot% + IC + numeric sort ───────────────────────────────
        {
            "query_name": "02 · IC candidates near pivot (≤ 2%)",
            "description": (
                "Institutional-candidate stocks within 2 % of resistance pivot. "
                "Demonstrates Dist Pivot% numeric filter + ASC sort."
            ),
            "sql_text": (
                'WHERE IC = 1 AND "Dist Pivot%" <= 2 AND Liquid = 1 '
                'ORDER BY "Dist Pivot%" ASC'
            ),
        },
        # ── 3. ROE / ROCE / Promoter% ─────────────────────────────────────────
        {
            "query_name": "03 · Quality compounders (ROE + ROCE)",
            "description": (
                "Strong return ratios AND high promoter holding in an uptrend. "
                "Demonstrates ROE%, ROCE%, Promoter% numeric filters."
            ),
            "sql_text": (
                "WHERE Quality = 1 AND ROE% >= 20 AND ROCE% >= 15 "
                "AND Promoter% >= 40 AND Trend = 1 ORDER BY ROE% DESC"
            ),
        },
        # ── 4. Breakout + Dist 52w% ───────────────────────────────────────────
        {
            "query_name": "04 · Fresh breakouts near 52-week high",
            "description": (
                "Breakout-day signals within 3 % of 52-week high — minimal overhead supply. "
                "Demonstrates Breakout flag + Dist 52w% negative threshold."
            ),
            "sql_text": (
                'WHERE Breakout = 1 AND "Dist 52w%" >= -3 AND Liquid = 1 '
                'ORDER BY "Dist Pivot%" ASC'
            ),
        },
        # ── 5. VCP + Score numeric ────────────────────────────────────────────
        {
            "query_name": "05 · VCP coiling — high composite score",
            "description": (
                "VCP pattern stocks in Stage 2 uptrend with Score ≥ 70. "
                "Demonstrates VCP boolean + Score numeric filter."
            ),
            "sql_text": (
                "WHERE VCP = 1 AND Stage2 = 1 AND Trend = 1 AND Score >= 70 "
                "ORDER BY Score DESC"
            ),
        },
        # ── 6. BO Ready + Promoter% ───────────────────────────────────────────
        {
            "query_name": "06 · BO Ready + promoter conviction (≥ 50 %)",
            "description": (
                "Breakout-ready setups backed by promoters holding ≥ 50 %. "
                "Demonstrates 'BO Ready' (quoted field) + Promoter% filter."
            ),
            "sql_text": (
                'WHERE "BO Ready" = 1 AND Promoter% >= 50 AND Quality = 1 '
                "ORDER BY Score DESC"
            ),
        },
        # ── 7. Fund Score + ROE ────────────────────────────────────────────────
        {
            "query_name": "07 · Strong fundamentals in Stage 2",
            "description": (
                "Fund Score ≥ 7 AND ROE ≥ 15 % in active Stage 2. "
                "Demonstrates Fund Score numeric filter + DESC sort."
            ),
            "sql_text": (
                'WHERE Stage2 = 1 AND "Fund Score" >= 7 AND ROE% >= 15 '
                'AND Liquid = 1 ORDER BY "Fund Score" DESC'
            ),
        },
        # ── 8. OR group (parenthesised) ───────────────────────────────────────
        {
            "query_name": "08 · Multi-signal entry — OR trigger group",
            "description": (
                "IC + trend aligned, RS leading, with at least one entry trigger (VCP OR Breakout OR BO Ready). "
                "Demonstrates parenthesised OR group syntax."
            ),
            "sql_text": (
                "WHERE IC = 1 AND Trend = 1 AND Stage2 = 1 AND RS = 1 "
                'AND (VCP = 1 OR Breakout = 1 OR "BO Ready" = 1) '
                "ORDER BY Score DESC"
            ),
        },
        # ── 9. Mid Stage 2 BETWEEN ────────────────────────────────────────────
        {
            "query_name": "09 · Mid Stage 2 sweet spot (10 – 30 days)",
            "description": (
                "Stage 2 stocks 10–30 trading days old — past the early rush, "
                "before late-stage extension risk. Demonstrates BETWEEN on "
                "S2 Age(d) with a wider range."
            ),
            "sql_text": (
                'WHERE Stage2 = 1 AND "S2 Age(d)" BETWEEN 10 AND 30 '
                "AND Liquid = 1 AND RS = 1 ORDER BY Score DESC"
            ),
        },
        # ── 10. Dist 52w% negative BETWEEN ───────────────────────────────────
        {
            "query_name": "10 · Deep pullback base builders (−25 % to −15 %)",
            "description": (
                "IC-qualified stocks 15–25 % below 52-week high — potential base "
                "formation zone. Demonstrates BETWEEN with negative bounds."
            ),
            "sql_text": (
                'WHERE IC = 1 AND "Dist 52w%" BETWEEN -25 AND -15 '
                "AND Liquid = 1 AND Quality = 1 "
                'ORDER BY "Dist 52w%" DESC'
            ),
        },
        # ── 11. Promoter% + RS + Trend ────────────────────────────────────────
        {
            "query_name": "11 · Promoter-backed RS leaders (≥ 60 %)",
            "description": (
                "High promoter holding + relative strength + liquid. "
                "Demonstrates Promoter% >= 60 sort."
            ),
            "sql_text": (
                "WHERE RS = 1 AND Promoter% >= 60 AND Liquid = 1 AND Trend = 1 "
                "ORDER BY Promoter% DESC"
            ),
        },
        # ── 12. tick alias ────────────────────────────────────────────────────
        {
            "query_name": "12 · Top score — tick alias (= tick / = true / = 1)",
            "description": (
                "Score ≥ 80 with all quality signals using 'tick' alias for boolean 1. "
                "Demonstrates: Liquid = tick, Quality = tick, RS = tick (all equivalent to = 1)."
            ),
            "sql_text": (
                "WHERE Score >= 80 AND Liquid = tick AND Quality = tick "
                "AND RS = tick ORDER BY Score DESC"
            ),
        },
    ]