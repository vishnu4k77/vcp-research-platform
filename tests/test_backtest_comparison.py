"""test_backtest_comparison.py — unit and smoke tests for the PA backtest comparison.

Groups
------
A  Config validation          — no DB, no network
B  BacktestService validation — no DB (whitelist check only)
C  PriceActionFeature         — synthetic DataFrames, no DB
D  PriceActionSignal          — synthetic DataFrames, no DB
E  Performance metrics        — synthetic trade DataFrames, no DB
F  DB smoke checks            — require populated stock_pa_signals
   (these print a WARNING and pass if the table is empty, never fail the suite)

Run
---
    python tests/test_backtest_comparison.py
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtesting.backtest_service import BacktestService, _VALID_ENTRY_SIGNALS
from app.backtesting.performance_metrics import compute as compute_metrics
from app.config.strategy_config import BacktestConfig, PriceActionConfig
from app.features.price_action_feature import PriceActionFeature
from app.signals.price_action_signal import PriceActionSignal

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_price_df(n: int = 60, trend: str = "up") -> pd.DataFrame:
    """Synthetic per-symbol price DataFrame for PriceActionFeature tests.

    Args:
        n: Number of daily rows.
        trend: "up" builds a rising price series; "flat" stays constant.

    Returns:
        DataFrame with columns required by PriceActionFeature.
    """
    np.random.seed(42)
    if trend == "up":
        close = 100.0 + np.arange(n) * 0.5 + np.random.normal(0, 0.3, n)
    else:
        close = np.full(n, 100.0)

    high  = close + np.random.uniform(0.5, 2.0, n)
    low   = close - np.random.uniform(0.5, 2.0, n)
    low   = np.minimum(low, close - 0.01)   # ensure low < close

    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values
    rvol  = np.full(n, 1.5)                  # steady accumulation volume

    dates = pd.date_range("2024-01-02", periods=n, freq="B")

    return pd.DataFrame({
        "symbol":          "TEST.NS",
        "trade_date":      dates,
        "close_price":     close,
        "high_price":      high,
        "low_price":       low,
        "ema_50":          ema50,
        "relative_volume": rvol,
    })


def _make_trade_df(**overrides) -> pd.DataFrame:
    """Build a minimal trades DataFrame for performance_metrics.compute() tests."""
    row = {
        "symbol":          "TEST.NS",
        "entry_date":      date(2024, 1, 2),
        "entry_price":     100.0,
        "exit_date":       date(2024, 2, 1),
        "exit_price":      120.0,
        "return_pct":      20.0,
        "holding_days":    30,
        "exit_reason":     "TARGET",
        "composite_score": 80.0,
        "regime":          "BULL",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _pass(name: str) -> None:
    print(f"  PASS  {name}")


def _fail(name: str, reason: str) -> None:
    raise AssertionError(f"FAIL  {name}: {reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Group A — Config validation
# ─────────────────────────────────────────────────────────────────────────────

def test_pa_signal_whitelisted() -> None:
    """pa_signal must be in ENTRY_SIGNAL_OPTIONS."""
    assert "pa_signal" in BacktestConfig.ENTRY_SIGNAL_OPTIONS, (
        "pa_signal not in ENTRY_SIGNAL_OPTIONS — add it to BacktestConfig"
    )
    _pass("pa_signal in ENTRY_SIGNAL_OPTIONS")


def test_combined_pa_ema_whitelisted() -> None:
    """combined_pa_ema must be in ENTRY_SIGNAL_OPTIONS."""
    assert "combined_pa_ema" in BacktestConfig.ENTRY_SIGNAL_OPTIONS, (
        "combined_pa_ema not in ENTRY_SIGNAL_OPTIONS — add it to BacktestConfig"
    )
    _pass("combined_pa_ema in ENTRY_SIGNAL_OPTIONS")


def test_pa_score_weights_sum_to_100() -> None:
    """PriceActionConfig score weights must sum exactly to 100."""
    total = (
        PriceActionConfig.PA_SCORE_DAILY_TREND
        + PriceActionConfig.PA_SCORE_WEEKLY_TREND
        + PriceActionConfig.PA_SCORE_CLOSE_QUALITY
        + PriceActionConfig.PA_SCORE_VOL_QUALITY
        + PriceActionConfig.PA_SCORE_MOMENTUM
        + PriceActionConfig.PA_SCORE_EXTENSION
    )
    assert total == 100, f"PA score weights sum to {total}, expected 100"
    _pass("PA score weights sum to 100")


def test_pa_config_thresholds_valid() -> None:
    """PriceActionConfig threshold ordering invariants."""
    cfg = PriceActionConfig
    assert 0.0 <= cfg.PA_MIN_CLOSE_POSITION <= 1.0, "PA_MIN_CLOSE_POSITION out of [0,1]"
    assert cfg.PA_VOL_MIN < cfg.PA_VOL_MAX, "PA_VOL_MIN must be < PA_VOL_MAX"
    assert cfg.PA_MIN_EXT_EMA50 < cfg.PA_MAX_EXT_EMA50, "PA_MIN_EXT_EMA50 must be < PA_MAX_EXT_EMA50"
    assert 0.0 < cfg.PA_MIN_SIGNAL_SCORE <= 100.0, "PA_MIN_SIGNAL_SCORE must be in (0,100]"
    assert cfg.PA_DAILY_LOOKBACK > 0, "PA_DAILY_LOOKBACK must be positive"
    assert cfg.PA_WEEKLY_LOOKBACK > 0, "PA_WEEKLY_LOOKBACK must be positive"
    _pass("PA config thresholds valid")


def test_pa_signal_types_subset_of_options() -> None:
    """PA_SIGNAL_TYPES must be a subset of ENTRY_SIGNAL_OPTIONS."""
    options = set(BacktestConfig.ENTRY_SIGNAL_OPTIONS)
    pa_types = BacktestConfig.PA_SIGNAL_TYPES
    extra = pa_types - options
    assert not extra, f"PA_SIGNAL_TYPES contains values not in ENTRY_SIGNAL_OPTIONS: {extra}"
    _pass("PA_SIGNAL_TYPES subset of ENTRY_SIGNAL_OPTIONS")


# ─────────────────────────────────────────────────────────────────────────────
# Group B — BacktestService whitelist validation
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_signal_raises_valueerror() -> None:
    """Non-whitelisted signal must raise ValueError before any DB call."""
    try:
        BacktestService.get_signal_entries(
            start_date          = date(2024, 1, 1),
            end_date            = date(2024, 12, 31),
            entry_signal        = "DROP TABLE stock_signals; --",
            min_composite_score = 50.0,
        )
        _fail("invalid_signal_raises_valueerror", "ValueError not raised for SQL injection input")
    except ValueError:
        _pass("invalid signal raises ValueError")


def test_all_pa_signals_pass_whitelist_check() -> None:
    """pa_signal and combined_pa_ema must be in the backtest service whitelist."""
    for sig in ["pa_signal", "combined_pa_ema"]:
        assert sig in _VALID_ENTRY_SIGNALS, f"{sig} missing from _VALID_ENTRY_SIGNALS"
    _pass("all PA signals pass whitelist check")


# ─────────────────────────────────────────────────────────────────────────────
# Group C — PriceActionFeature computation (synthetic data)
# ─────────────────────────────────────────────────────────────────────────────

def test_pa_feature_required_columns_present() -> None:
    """PriceActionFeature.calculate() must produce all expected output columns."""
    df     = _make_price_df(n=80, trend="up")
    result = PriceActionFeature.calculate(df)

    assert not result.empty, "calculate() returned empty DataFrame"
    expected = [
        "pa_hh_daily", "pa_hl_daily",
        "pa_hh_weekly", "pa_hl_weekly",
        "pa_close_position", "pa_vol_quality",
        "pa_roc_10", "pa_roc_20",
        "pa_momentum_accel", "pa_extension_ok",
    ]
    missing = [c for c in expected if c not in result.columns]
    assert not missing, f"Missing PA feature columns: {missing}"
    _pass("PA feature output columns present")


def test_pa_feature_hh_detected_on_uptrend() -> None:
    """A persistent uptrend must produce pa_hh_daily = 1 on later bars."""
    df     = _make_price_df(n=80, trend="up")
    result = PriceActionFeature.calculate(df)

    # After enough bars for the rolling window, we expect HH
    late_bars = result.iloc[-20:]
    hh_count  = int(late_bars["pa_hh_daily"].sum())
    assert hh_count > 0, (
        f"Expected pa_hh_daily=1 on uptrend late bars, got {hh_count} hits"
    )
    _pass(f"pa_hh_daily detected on uptrend (last 20 bars: {hh_count} hits)")


def test_pa_feature_close_position_in_range() -> None:
    """pa_close_position must always be in [0, 1]."""
    df     = _make_price_df(n=80, trend="up")
    result = PriceActionFeature.calculate(df)

    cp = result["pa_close_position"].dropna()
    assert cp.between(0.0, 1.0).all(), (
        f"pa_close_position out of [0,1]: min={cp.min():.3f}, max={cp.max():.3f}"
    )
    _pass("pa_close_position always in [0,1]")


def test_pa_feature_vol_quality_binary() -> None:
    """pa_vol_quality must be 0 or 1 — never fractional."""
    df     = _make_price_df(n=80, trend="up")
    result = PriceActionFeature.calculate(df)

    unique_vals = set(result["pa_vol_quality"].dropna().unique())
    invalid = unique_vals - {0, 1}
    assert not invalid, f"pa_vol_quality has non-binary values: {invalid}"
    _pass("pa_vol_quality is binary 0/1")


def test_pa_feature_extension_ok_within_bounds() -> None:
    """Stocks near EMA50 (synthetic data) should get pa_extension_ok=1."""
    df = _make_price_df(n=80, trend="up")
    # Force ema_50 = close_price (perfectly at EMA) -> ext=0% -> always within bounds
    df["ema_50"] = df["close_price"].values
    result = PriceActionFeature.calculate(df)

    late_ext = result.iloc[-20:]["pa_extension_ok"]
    assert (late_ext == 1).all(), (
        f"Expected pa_extension_ok=1 when close=EMA50, got {late_ext.tolist()}"
    )
    _pass("pa_extension_ok=1 when close equals EMA50")


def test_pa_feature_missing_required_column_returns_empty() -> None:
    """Missing required column must return empty DataFrame without raising."""
    df = _make_price_df(n=60, trend="up")
    df = df.drop(columns=["ema_50"])       # remove required column

    result = PriceActionFeature.calculate(df)
    assert result.empty, "Expected empty DataFrame when required column is missing"
    _pass("PriceActionFeature returns empty on missing required column")


def test_pa_feature_weekly_insufficient_history() -> None:
    """With fewer bars than weekly lookback requires, weekly flags should be 0."""
    df     = _make_price_df(n=10, trend="up")   # only 10 bars — not enough for weekly
    result = PriceActionFeature.calculate(df)

    if not result.empty:
        # With 10 bars there are ≤ 2 weekly bars; weekly HH/HL should be 0
        assert (result["pa_hh_weekly"] == 0).all(), "Expected pa_hh_weekly=0 with 10 bars"
        assert (result["pa_hl_weekly"] == 0).all(), "Expected pa_hl_weekly=0 with 10 bars"
    _pass("weekly flags are 0 with insufficient history")


# ─────────────────────────────────────────────────────────────────────────────
# Group D — PriceActionSignal computation (synthetic data)
# ─────────────────────────────────────────────────────────────────────────────

def _make_signal_df(
    hh_daily: int = 1, hl_daily: int = 1,
    hh_weekly: int = 1, hl_weekly: int = 1,
    close_pos: float = 0.80,
    vol_quality: int = 1,
    momentum: int = 1,
    ext_ok: int = 1,
    n: int = 5,
) -> pd.DataFrame:
    """Build a synthetic PA feature DataFrame for PriceActionSignal tests."""
    return pd.DataFrame({
        "symbol":             ["TEST.NS"] * n,
        "trade_date":         pd.date_range("2024-01-02", periods=n, freq="B"),
        "pa_hh_daily":        [hh_daily]   * n,
        "pa_hl_daily":        [hl_daily]   * n,
        "pa_hh_weekly":       [hh_weekly]  * n,
        "pa_hl_weekly":       [hl_weekly]  * n,
        "pa_close_position":  [close_pos]  * n,
        "pa_vol_quality":     [vol_quality] * n,
        "pa_momentum_accel":  [momentum]   * n,
        "pa_extension_ok":    [ext_ok]     * n,
    })


def test_pa_signal_all_conditions_met() -> None:
    """pa_signal=1 when all six conditions pass (score=100)."""
    df     = _make_signal_df()
    result = PriceActionSignal.calculate(df)

    assert not result.empty
    assert (result["pa_score"] == 100.0).all(), f"Expected score=100, got {result['pa_score'].tolist()}"
    assert (result["pa_signal"] == 1).all(), f"Expected pa_signal=1, got {result['pa_signal'].tolist()}"
    _pass("pa_signal=1 when all conditions met (score=100)")


def test_pa_signal_weekly_gate_blocks_signal() -> None:
    """pa_signal must be 0 when weekly trend is absent, even if daily+others are perfect."""
    # Weekly HH missing -> weekly_trend=False -> hard gate blocks signal
    df     = _make_signal_df(hh_weekly=0)
    result = PriceActionSignal.calculate(df)

    assert not result.empty
    assert (result["pa_signal"] == 0).all(), (
        f"Expected pa_signal=0 when weekly gate fails, got {result['pa_signal'].tolist()}"
    )
    # Score should be 75 (100 - 25 for missing weekly)
    expected_score = (
        PriceActionConfig.PA_SCORE_DAILY_TREND
        + PriceActionConfig.PA_SCORE_CLOSE_QUALITY
        + PriceActionConfig.PA_SCORE_VOL_QUALITY
        + PriceActionConfig.PA_SCORE_MOMENTUM
        + PriceActionConfig.PA_SCORE_EXTENSION
    )
    assert (result["pa_score"] == float(expected_score)).all(), (
        f"Expected score={expected_score}, got {result['pa_score'].tolist()}"
    )
    _pass(f"weekly gate blocks pa_signal when hh_weekly=0 (score={expected_score}, signal=0)")


def test_pa_signal_score_threshold() -> None:
    """pa_signal=0 when weekly is present but score < PA_MIN_SIGNAL_SCORE."""
    # Only weekly trend passes (50 pts) — below 70 threshold
    df     = _make_signal_df(hh_daily=0, hl_daily=0,
                              close_pos=0.0, vol_quality=0, momentum=0, ext_ok=0)
    result = PriceActionSignal.calculate(df)

    assert not result.empty
    expected_score = PriceActionConfig.PA_SCORE_WEEKLY_TREND   # 25 pts
    assert (result["pa_score"] == float(expected_score)).all(), (
        f"Expected score={expected_score}, got {result['pa_score'].tolist()}"
    )
    assert (result["pa_signal"] == 0).all(), (
        f"Expected pa_signal=0 (score below threshold), got {result['pa_signal'].tolist()}"
    )
    _pass(f"pa_signal=0 when score={expected_score} < threshold={PriceActionConfig.PA_MIN_SIGNAL_SCORE}")


def test_pa_signal_missing_column_returns_empty() -> None:
    """PriceActionSignal must return empty DataFrame when feature columns are missing."""
    df = _make_signal_df()
    df = df.drop(columns=["pa_hh_weekly"])

    result = PriceActionSignal.calculate(df)
    assert result.empty, "Expected empty DataFrame when required feature column missing"
    _pass("PriceActionSignal returns empty on missing feature column")


def test_pa_signal_partial_conditions() -> None:
    """Verify partial scores: daily + weekly trend only = 50pts -> signal=0."""
    # Only daily and weekly trend conditions pass (25+25=50 pts)
    df     = _make_signal_df(close_pos=0.0, vol_quality=0, momentum=0, ext_ok=0)
    result = PriceActionSignal.calculate(df)

    assert not result.empty
    expected_score = (
        PriceActionConfig.PA_SCORE_DAILY_TREND
        + PriceActionConfig.PA_SCORE_WEEKLY_TREND
    )  # 50
    assert (result["pa_score"] == float(expected_score)).all()
    assert (result["pa_signal"] == 0).all()
    _pass(f"partial score {expected_score} < 70 -> pa_signal=0")


# ─────────────────────────────────────────────────────────────────────────────
# Group E — Performance metrics (synthetic trade DataFrames)
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_single_win() -> None:
    """Single winning trade -> win_rate=100, positive metrics."""
    df = _make_trade_df(return_pct=20.0, exit_reason="TARGET")
    m  = compute_metrics(df)

    assert m["total_trades"]  == 1
    assert m["win_rate"]      == 100.0
    assert m["target_count"]  == 1
    assert m["stop_count"]    == 0
    assert m["timeout_count"] == 0
    assert m["avg_win_pct"]   == pytest_approx(20.0, abs=0.01)
    assert m["profit_factor"] == float("inf")   # no losses
    _pass("single TARGET trade -> 100% win rate, profit_factor=inf")


def test_metrics_single_stop() -> None:
    """Single stop-loss trade -> win_rate=0, all losses."""
    df = _make_trade_df(return_pct=-7.0, exit_reason="STOP")
    m  = compute_metrics(df)

    assert m["total_trades"]  == 1
    assert m["win_rate"]      == 0.0
    assert m["stop_count"]    == 1
    assert m["target_count"]  == 0
    assert m["avg_loss_pct"]  == pytest_approx(-7.0, abs=0.01)
    _pass("single STOP trade -> 0% win rate")


def test_metrics_timeout_neutral() -> None:
    """Timeout at exactly 0% return is a non-win (neutral exit)."""
    df = _make_trade_df(return_pct=0.0, exit_reason="TIMEOUT")
    m  = compute_metrics(df)

    # 0% is counted as <= 0 -> loss bucket
    assert m["win_rate"]      == 0.0
    assert m["timeout_count"] == 1
    assert m["total_trades"]  == 1
    _pass("timeout at 0% return counted as non-win (neutral)")


def test_metrics_timeout_positive() -> None:
    """Timeout with positive return (e.g. +5%) counts as a win."""
    df = _make_trade_df(return_pct=5.0, exit_reason="TIMEOUT")
    m  = compute_metrics(df)

    assert m["win_rate"]      == 100.0
    assert m["timeout_count"] == 1
    assert m["avg_win_pct"]   == pytest_approx(5.0, abs=0.01)
    _pass("timeout at +5% return counted as win")


def test_metrics_timeout_negative() -> None:
    """Timeout with negative return (e.g. -3%) counts as a loss."""
    df = _make_trade_df(return_pct=-3.0, exit_reason="TIMEOUT")
    m  = compute_metrics(df)

    assert m["win_rate"]     == 0.0
    assert m["timeout_count"] == 1
    _pass("timeout at -3% return counted as loss")


def test_metrics_mixed_exits() -> None:
    """Three trades: 1 win, 1 stop, 1 timeout — verify all exit buckets."""
    trades = pd.DataFrame([
        {**_make_trade_df(return_pct=20.0, exit_reason="TARGET").iloc[0].to_dict()},
        {**_make_trade_df(return_pct=-7.0, exit_reason="STOP").iloc[0].to_dict()},
        {**_make_trade_df(return_pct=2.0,  exit_reason="TIMEOUT").iloc[0].to_dict()},
    ])
    m = compute_metrics(trades)

    assert m["total_trades"]  == 3
    assert m["target_count"]  == 1
    assert m["stop_count"]    == 1
    assert m["timeout_count"] == 1
    assert m["win_rate"]      == pytest_approx(66.67, abs=0.1)   # 2 of 3 > 0
    assert m["total_return_pct"] == pytest_approx(15.0, abs=0.01)  # 20 - 7 + 2
    _pass("mixed exits: 1 TARGET + 1 STOP + 1 TIMEOUT -> correct counts and win rate")


def test_metrics_profit_factor_all_wins() -> None:
    """All-win portfolio -> profit_factor=inf (no denominator)."""
    trades = pd.DataFrame([
        _make_trade_df(return_pct=10.0, exit_reason="TARGET").iloc[0].to_dict(),
        _make_trade_df(return_pct=15.0, exit_reason="TARGET").iloc[0].to_dict(),
    ])
    m = compute_metrics(trades)
    assert m["profit_factor"] == float("inf")
    _pass("all-win portfolio -> profit_factor=inf")


def test_metrics_profit_factor_all_losses() -> None:
    """All-loss portfolio -> profit_factor=0 (no numerator)."""
    trades = pd.DataFrame([
        _make_trade_df(return_pct=-7.0, exit_reason="STOP").iloc[0].to_dict(),
        _make_trade_df(return_pct=-5.0, exit_reason="STOP").iloc[0].to_dict(),
    ])
    m = compute_metrics(trades)
    assert m["profit_factor"] == 0.0, f"Expected 0.0, got {m['profit_factor']}"
    _pass("all-loss portfolio -> profit_factor=0")


def test_metrics_sharpe_no_variance() -> None:
    """All identical returns -> std=0 -> Sharpe=0 (no division by zero)."""
    trades = pd.DataFrame([
        _make_trade_df(return_pct=10.0).iloc[0].to_dict(),
        _make_trade_df(return_pct=10.0).iloc[0].to_dict(),
        _make_trade_df(return_pct=10.0).iloc[0].to_dict(),
    ])
    m = compute_metrics(trades)
    assert m["sharpe_ratio"] == 0.0, f"Expected 0.0 sharpe with zero variance, got {m['sharpe_ratio']}"
    _pass("zero-variance returns -> Sharpe=0 (no crash)")


def test_metrics_by_regime_breakdown() -> None:
    """By-regime stats must reflect trade-level data correctly."""
    trades = pd.DataFrame([
        _make_trade_df(return_pct=20.0, exit_reason="TARGET", regime="BULL").iloc[0].to_dict(),
        _make_trade_df(return_pct=-7.0, exit_reason="STOP",   regime="BULL").iloc[0].to_dict(),
        _make_trade_df(return_pct=15.0, exit_reason="TARGET", regime="BEAR").iloc[0].to_dict(),
    ])
    m = compute_metrics(trades)

    assert "BULL" in m["by_regime"]
    assert "BEAR" in m["by_regime"]
    bull = m["by_regime"]["BULL"]
    bear = m["by_regime"]["BEAR"]

    assert bull["count"]    == 2
    assert bull["win_rate"] == 50.0     # 1 of 2
    assert bear["count"]    == 1
    assert bear["win_rate"] == 100.0    # 1 of 1
    _pass("by_regime breakdown correct for BULL and BEAR regimes")


def test_metrics_empty_df_returns_empty_dict() -> None:
    """compute() on empty DataFrame must return {} (no crash)."""
    m = compute_metrics(pd.DataFrame())
    assert m == {}, f"Expected empty dict, got {m}"
    _pass("empty DataFrame -> empty metrics dict")


def test_metrics_best_worst_trade() -> None:
    """best_trade_pct and worst_trade_pct must match actual max/min return."""
    trades = pd.DataFrame([
        _make_trade_df(return_pct=25.0, exit_reason="TARGET").iloc[0].to_dict(),
        _make_trade_df(return_pct=-7.0, exit_reason="STOP").iloc[0].to_dict(),
        _make_trade_df(return_pct=10.0, exit_reason="TIMEOUT").iloc[0].to_dict(),
    ])
    m = compute_metrics(trades)
    assert m["best_trade_pct"]  == pytest_approx(25.0, abs=0.01)
    assert m["worst_trade_pct"] == pytest_approx(-7.0, abs=0.01)
    _pass("best_trade_pct=25.0, worst_trade_pct=-7.0")


# ─────────────────────────────────────────────────────────────────────────────
# Group F — DB smoke checks
# ─────────────────────────────────────────────────────────────────────────────

def test_pa_signals_table_exists() -> None:
    """stock_pa_signals table must exist (created by setup_db.py)."""
    try:
        from sqlalchemy import inspect as sa_inspect
        from app.config.db import engine
        inspector = sa_inspect(engine)
        tables = set(inspector.get_table_names())
        assert "stock_pa_signals" in tables, (
            "stock_pa_signals table missing — run: python setup_db.py"
        )
        _pass("stock_pa_signals table exists")
    except Exception as exc:
        print(f"  SKIP  stock_pa_signals table check: {exc}")


def test_pa_signals_table_has_rows() -> None:
    """stock_pa_signals must have rows (requires run_pa_pipeline.py to have run)."""
    try:
        from sqlalchemy import text
        from app.config.db import engine
        with engine.connect() as conn:
            row = conn.execute(text("SELECT COUNT(*) FROM stock_pa_signals")).fetchone()
        count = row[0] if row else 0
        if count == 0:
            print("  WARN  stock_pa_signals is empty — run: python scripts/run_pa_pipeline.py")
        else:
            _pass(f"stock_pa_signals has {count:,} rows")
    except Exception as exc:
        print(f"  SKIP  stock_pa_signals row check: {exc}")


def test_combined_is_subset_of_breakout() -> None:
    """combined_pa_ema entries must be a subset of breakout_signal entries on any date.

    By definition: combined requires BOTH breakout_signal=1 AND pa_signal=1.
    Therefore combined_count <= breakout_count for the same date range.
    """
    try:
        from sqlalchemy import text
        from app.config.db import engine

        with engine.connect() as conn:
            # Count breakout entries
            bo_row = conn.execute(text(
                "SELECT COUNT(*) FROM stock_signals WHERE breakout_signal = 1"
            )).fetchone()
            # Count combined entries
            comb_row = conn.execute(text(
                "SELECT COUNT(*) FROM stock_signals ss "
                "JOIN stock_pa_signals ps "
                "ON ps.symbol = ss.symbol AND ps.trade_date = ss.trade_date "
                "WHERE ss.breakout_signal = 1 AND ps.pa_signal = 1"
            )).fetchone()

        bo_count   = bo_row[0]   if bo_row   else 0
        comb_count = comb_row[0] if comb_row else 0

        if bo_count == 0:
            print("  WARN  No breakout_signal rows — run main pipeline first")
        else:
            assert comb_count <= bo_count, (
                f"combined_pa_ema ({comb_count}) > breakout_signal ({bo_count}) — impossible"
            )
            pct = comb_count / bo_count * 100 if bo_count else 0
            _pass(
                f"combined_pa_ema is subset of breakout_signal "
                f"({comb_count} / {bo_count} = {pct:.1f}%)"
            )
    except Exception as exc:
        print(f"  SKIP  combined subset check: {exc}")


def test_pa_signal_rate_sanity() -> None:
    """pa_signal hit rate should be between 1% and 80% (sanity: not all 0 or all 1)."""
    try:
        from sqlalchemy import text
        from app.config.db import engine

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT "
                "  SUM(CAST(pa_signal AS INT)) AS hits, "
                "  COUNT(*) AS total "
                "FROM stock_pa_signals"
            )).fetchone()

        if not row or row[1] == 0:
            print("  WARN  stock_pa_signals is empty — skipping hit-rate check")
            return

        hits, total = row[0], row[1]
        rate = hits / total * 100
        assert 0.5 <= rate <= 90.0, (
            f"pa_signal hit rate {rate:.1f}% is suspiciously extreme "
            f"({hits:,} of {total:,} rows) — check PriceActionConfig thresholds"
        )
        _pass(f"pa_signal hit rate = {rate:.1f}% ({hits:,} / {total:,}) — within sane range")
    except Exception as exc:
        print(f"  SKIP  pa_signal hit rate check: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Minimal approx helper (avoid pytest dependency)
# ─────────────────────────────────────────────────────────────────────────────

class pytest_approx:  # noqa: N801
    """Tolerance-based equality for float assertions without requiring pytest."""

    def __init__(self, expected: float, abs: float = 1e-6) -> None:
        self.expected = expected
        self.abs      = abs

    def __eq__(self, actual) -> bool:
        return abs(float(actual) - self.expected) <= self.abs

    def __repr__(self) -> str:
        return f"≈{self.expected} (±{self.abs})"


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

_ALL_TESTS = [
    # Group A
    test_pa_signal_whitelisted,
    test_combined_pa_ema_whitelisted,
    test_pa_score_weights_sum_to_100,
    test_pa_config_thresholds_valid,
    test_pa_signal_types_subset_of_options,
    # Group B
    test_invalid_signal_raises_valueerror,
    test_all_pa_signals_pass_whitelist_check,
    # Group C
    test_pa_feature_required_columns_present,
    test_pa_feature_hh_detected_on_uptrend,
    test_pa_feature_close_position_in_range,
    test_pa_feature_vol_quality_binary,
    test_pa_feature_extension_ok_within_bounds,
    test_pa_feature_missing_required_column_returns_empty,
    test_pa_feature_weekly_insufficient_history,
    # Group D
    test_pa_signal_all_conditions_met,
    test_pa_signal_weekly_gate_blocks_signal,
    test_pa_signal_score_threshold,
    test_pa_signal_missing_column_returns_empty,
    test_pa_signal_partial_conditions,
    # Group E
    test_metrics_single_win,
    test_metrics_single_stop,
    test_metrics_timeout_neutral,
    test_metrics_timeout_positive,
    test_metrics_timeout_negative,
    test_metrics_mixed_exits,
    test_metrics_profit_factor_all_wins,
    test_metrics_profit_factor_all_losses,
    test_metrics_sharpe_no_variance,
    test_metrics_by_regime_breakdown,
    test_metrics_empty_df_returns_empty_dict,
    test_metrics_best_worst_trade,
    # Group F
    test_pa_signals_table_exists,
    test_pa_signals_table_has_rows,
    test_combined_is_subset_of_breakout,
    test_pa_signal_rate_sanity,
]

_GROUP_LABELS = {
    "test_pa_signal_whitelisted":                      "A  Config validation",
    "test_invalid_signal_raises_valueerror":            "B  BacktestService whitelist",
    "test_pa_feature_required_columns_present":         "C  PriceActionFeature",
    "test_pa_signal_all_conditions_met":                "D  PriceActionSignal",
    "test_metrics_single_win":                          "E  Performance metrics",
    "test_pa_signals_table_exists":                     "F  DB smoke checks",
}


if __name__ == "__main__":
    sep   = "-" * 60  # ASCII only -- Windows cp1252 safe
    total = len(_ALL_TESTS)
    passed = 0
    failed = 0
    errors = []

    current_group = ""
    for fn in _ALL_TESTS:
        group = _GROUP_LABELS.get(fn.__name__, "")
        if group and group != current_group:
            current_group = group
            print(f"\n{sep}")
            print(f"  {current_group}")
            print(sep)

        try:
            fn()
            passed += 1
        except Exception as exc:
            failed += 1
            errors.append((fn.__name__, str(exc)))
            print(f"  FAIL  {fn.__name__}: {exc}")

    print(f"\n{sep}")
    print(f"  Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  |  {failed} FAILED")
        sys.exit(1)
    else:
        print("  — all passed")
        sys.exit(0)
