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