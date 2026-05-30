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

    # Fundamental filters — Phase 2, when Screener.in scraper is built
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