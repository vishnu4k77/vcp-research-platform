"""Tab 5 — Backtesting: signal simulation with configurable stop/target/hold rules.

How it works
------------
1. User selects: entry signal, date range, stop %, target %, max holding days.
2. BacktestEngine.run() pulls entry signals from stock_signals, loads forward
   price data from daily_price_data, and simulates each trade in memory.
3. Results are cached in st.session_state for the current browser session.
4. Three panels: KPI metrics, equity curve chart, per-trade table.

No DB writes happen here — the tab is entirely read-only.
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.backtesting.backtest_engine import BacktestEngine, BacktestResult
from app.backtesting.backtest_service import BacktestService
from app.config.strategy_config import BacktestConfig, DashboardConfig
from app.dashboard.styles import (
    BG_APP, BG_CARD, BORDER, BORDER_MED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    GREEN, RED, ORANGE, BLUE,
    section_title, metric_card,
)

# Session-state key for caching the last result within a browser session
_RESULT_KEY = "backtest_result"


def _rgba(hex_color: str, alpha: float) -> str:
    """Convert a '#rrggbb' hex colour to a Plotly-compatible 'rgba(r,g,b,a)' string.

    Plotly rejects 8-digit CSS hex (#rrggbbaa) for properties like fillcolor.
    This helper produces the rgba() form that Plotly accepts for all colour fields.

    Args:
        hex_color: 6-digit hex colour string (e.g. '#58a6ff'). Leading '#' optional.
        alpha: Opacity in [0.0, 1.0].

    Returns:
        rgba string, e.g. 'rgba(88, 166, 255, 0.09)'.
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ── Cached helpers ────────────────────────────────────────────────────────────

@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_date_range() -> tuple[Optional[date], Optional[date]]:
    """Cached min/max signal dates from stock_signals."""
    return BacktestService.get_date_range()


# ── Chart builders ────────────────────────────────────────────────────────────

def _build_equity_curve(trades: pd.DataFrame) -> go.Figure:
    """Build a Plotly cumulative-return curve from the trades DataFrame.

    Uses cumulative SUM (not compounded product) — correct for signal analysis
    where each trade represents an equal-sized independent position.  Compounding
    4000+ trades via cumprod() overflows to absurd numbers when avg return > 0.

    The y-axis shows cumulative % gain/loss if every signal was traded with the
    same fixed position size (e.g. always 10% of portfolio).

    Args:
        trades: BacktestEngine output DataFrame with return_pct column.

    Returns:
        Plotly Figure showing cumulative return % per sequential trade.
    """
    returns    = pd.to_numeric(trades["return_pct"], errors="coerce").fillna(0.0)
    cumulative = returns.cumsum()
    trade_nums = list(range(1, len(cumulative) + 1))

    zero_color = _rgba(GREEN, 0.4) if cumulative.iloc[-1] >= 0 else _rgba(RED, 0.4)
    line_color = GREEN if cumulative.iloc[-1] >= 0 else RED

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=trade_nums,
        y=cumulative,
        mode="lines",
        name="Cumulative Return",
        line=dict(color=line_color, width=2),
        fill="tozeroy",
        fillcolor=_rgba(BLUE, 0.07),
        hovertemplate="Trade %{x}<br>Cumulative: %{y:.1f}%<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color=TEXT_MUTED, line_width=1)

    fig.update_layout(
        title=dict(text="Cumulative Return % (equal position size per trade)", font=dict(color=TEXT_PRIMARY, size=13)),
        xaxis=dict(title="Trade #", gridcolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        yaxis=dict(title="Cumulative Return %", gridcolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        height=300,
        margin=dict(l=0, r=0, t=50, b=30),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY, size=12),
        showlegend=False,
    )
    return fig


def _build_return_distribution(trades: pd.DataFrame) -> go.Figure:
    """Build a histogram of per-trade returns.

    Args:
        trades: BacktestEngine output DataFrame with return_pct column.

    Returns:
        Plotly Figure with positive/negative bars coloured green/red.
    """
    returns = pd.to_numeric(trades["return_pct"], errors="coerce").dropna()

    fig = go.Figure(go.Histogram(
        x=returns,
        nbinsx=30,
        marker_color=[GREEN if r > 0 else RED for r in returns],
        opacity=0.8,
        hovertemplate="Return: %{x:.1f}%<br>Count: %{y}<extra></extra>",
    ))
    fig.add_vline(x=0, line_color=TEXT_MUTED, line_width=1)

    fig.update_layout(
        title=dict(text="Return Distribution (%)", font=dict(color=TEXT_PRIMARY, size=13)),
        xaxis=dict(title="Return %", gridcolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        yaxis=dict(title="Trades",   gridcolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        height=300,
        margin=dict(l=0, r=0, t=50, b=30),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY, size=12),
        showlegend=False,
    )
    return fig


# ── KPI row helper ────────────────────────────────────────────────────────────

def _kpi(label: str, value: str, colour: str = TEXT_PRIMARY) -> str:
    """Single KPI card HTML.

    Args:
        label: Short uppercase label.
        value: Formatted value string.
        colour: Hex colour for the value text.

    Returns:
        Single-line HTML string.
    """
    return (
        f"<div style='background:{BG_CARD};border:1px solid {BORDER};"
        f"border-radius:10px;padding:14px 16px;text-align:center'>"
        f"<div style='font-size:10px;font-weight:600;color:{TEXT_MUTED};"
        f"text-transform:uppercase;letter-spacing:0.08em;"
        f"margin-bottom:8px;font-family:Inter,sans-serif'>{label}</div>"
        f"<div style='font-size:22px;font-weight:700;color:{colour};"
        f"font-family:Inter,sans-serif;line-height:1'>{value}</div>"
        f"</div>"
    )


# ── Main render ───────────────────────────────────────────────────────────────

def render_backtest_tab() -> None:
    """Render the Backtesting tab.

    Layout:
      1. Configuration row  — entry signal, dates, stop/target/hold controls
      2. Run button
      3. KPI metrics row    — win rate, avg return, sharpe, drawdown, trades
      4. Equity curve + return distribution side by side
      5. Per-trade table    — sortable, exportable as CSV
    """
    st.markdown(section_title("Signal Backtest"), unsafe_allow_html=True)
    st.caption(
        "Simulates trades using historical stock_signals entries and daily_price_data "
        "forward prices. Stop is checked before target within the same bar (conservative)."
    )

    # ── Date range from DB ────────────────────────────────────────────────────
    min_date, max_date = _load_date_range()

    if min_date is None or max_date is None:
        st.warning("No signal data found. Run `python run.py` to populate stock_signals first.")
        return

    # ── Configuration controls — Row 1: signal + dates + numeric rules ───────
    # 7 equal-width columns so labels don't wrap
    c1, c2, c3, c4, c5, c6, c7 = st.columns([2, 1.4, 1.4, 1, 1, 1, 1])

    with c1:
        entry_signal = st.selectbox(
            "Entry signal",
            options=BacktestConfig.ENTRY_SIGNAL_OPTIONS,
            index=BacktestConfig.ENTRY_SIGNAL_OPTIONS.index(
                BacktestConfig.DEFAULT_ENTRY_SIGNAL
            ),
            key="bt_entry_signal",
            help="Signal column in stock_signals that triggers a trade entry.",
        )

    with c2:
        bt_start = st.date_input(
            "From",
            value=max(min_date, max_date - timedelta(days=3 * 365)),
            min_value=min_date,
            max_value=max_date,
            key="bt_start_date",
        )

    with c3:
        bt_end = st.date_input(
            "To",
            value=max_date,
            min_value=min_date,
            max_value=max_date,
            key="bt_end_date",
        )

    with c4:
        stop_pct = st.number_input(
            "Stop %",
            min_value=1.0,
            max_value=30.0,
            value=float(BacktestConfig.STOP_LOSS_PCT * 100),
            step=0.5,
            key="bt_stop_pct",
            help="% below entry close that triggers stop exit. Range: 1–30%. Step: 0.5%.",
        )

    with c5:
        target_pct = st.number_input(
            "Target %",
            min_value=1.0,
            max_value=100.0,
            value=float(BacktestConfig.TARGET_PCT * 100),
            step=1.0,
            key="bt_target_pct",
            help="% above entry close that triggers take-profit exit. Range: 1–100%. Step: 1%.",
        )

    with c6:
        max_hold = st.number_input(
            "Max hold",
            min_value=5,
            max_value=365,
            value=BacktestConfig.MAX_HOLDING_DAYS,
            step=5,
            key="bt_max_hold",
            help=(
                "Maximum calendar days to hold before forced exit at last available close. "
                "Range: 5–365. Step: 5. 60 days = ~43 trading sessions."
            ),
        )

    with c7:
        min_score = st.number_input(
            "Min score",
            min_value=0,
            max_value=100,
            value=int(BacktestConfig.MIN_COMPOSITE_SCORE),
            step=5,
            key="bt_min_score",
            help="Only entries with composite_score >= this value are included. Range: 0–100.",
        )

    # ── Stage 2 age filter ────────────────────────────────────────────────────
    # Independent of the scanner sidebar — this filter is specific to the backtest.
    # Maps the same Early/Mid/Advanced bands as the scanner (from StrategyConfig).
    from app.config.strategy_config import StrategyConfig as _SC

    _S2_EARLY = _SC.STAGE2_EARLY_MAX_DAYS
    _S2_MID   = _SC.STAGE2_MID_MAX_DAYS

    _STAGE2_OPTIONS: dict[str, tuple[Optional[int], Optional[int]]] = {
        "All":                           (None, None),
        f"Early  (1–{_S2_EARLY}d)":      (1,    _S2_EARLY),
        f"Mid  ({_S2_EARLY+1}–{_S2_MID}d)": (_S2_EARLY + 1, _S2_MID),
        f"Advanced  (>{_S2_MID}d)":      (_S2_MID + 1, None),
    }

    stage2_label = st.selectbox(
        "Stage 2 maturity filter",
        options=list(_STAGE2_OPTIONS.keys()),
        index=0,
        key="bt_stage2_filter",
        help=(
            f"Filter entries by how long the stock had been in Stage 2 on the entry date. "
            f"Early ≤ {_S2_EARLY}d = fresh breakout window. "
            f"Requires running `python run.py --skip-ingestion` after Stage 2 age was added."
        ),
    )

    min_stage2_days, max_stage2_days = _STAGE2_OPTIONS[stage2_label]

    # ── Cross-field validation ────────────────────────────────────────────────
    validation_errors: list[str] = []

    if target_pct <= stop_pct:
        validation_errors.append(
            f"Target ({target_pct:.1f}%) must be greater than Stop ({stop_pct:.1f}%). "
            f"Current risk/reward = {target_pct / stop_pct:.2f} — must be > 1."
        )

    date_span_days = (bt_end - bt_start).days
    if date_span_days < max_hold:
        validation_errors.append(
            f"Date range ({date_span_days}d) is shorter than Max hold ({max_hold}d). "
            "Most trades will have no forward data and be skipped."
        )

    if bt_start >= bt_end:
        validation_errors.append("'From' date must be before 'To' date.")

    for err in validation_errors:
        st.warning(err)

    run_btn = st.button(
        "▶ Run Backtest",
        type="primary",
        key="bt_run",
        disabled=bool(validation_errors),
    )

    # ── Run or retrieve cached result ─────────────────────────────────────────
    result: Optional[BacktestResult] = st.session_state.get(_RESULT_KEY)

    if run_btn:
        with st.spinner("Simulating trades…"):
            result = BacktestEngine.run(
                start_date          = bt_start,
                end_date            = bt_end,
                entry_signal        = entry_signal,
                stop_loss_pct       = stop_pct / 100.0,
                target_pct          = target_pct / 100.0,
                max_holding_days    = int(max_hold),
                min_composite_score = float(min_score),
                min_stage2_days     = min_stage2_days,
                max_stage2_days     = max_stage2_days,
            )
        st.session_state[_RESULT_KEY] = result

    if result is None or result.is_empty:
        # If stage2 filter was active and returned nothing, explain why
        if (min_stage2_days is not None or max_stage2_days is not None) and result is not None:
            st.warning(
                f"**0 trades** matched the Stage 2 filter **'{stage2_label}'**. "
                "This filter requires `stage2_days` to be populated in `stock_signals`. "
                "Run `python run.py --skip-ingestion` to reprocess signals and populate it, "
                "then re-run the backtest."
            )
        else:
            st.info(
                "Configure parameters above and click **▶ Run Backtest** to simulate trades. "
                "No results yet — or the selected parameters produced no matching entries."
            )
        return

    m  = result.metrics
    tr = result.trades

    # ── KPI metrics ───────────────────────────────────────────────────────────
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    params = result.params
    s2_label_str = (
        f" | Stage 2: {stage2_label}"
        if (params.get("min_stage2_days") is not None or params.get("max_stage2_days") is not None)
        else ""
    )
    st.caption(
        f"**{m['total_trades']} trades** | "
        f"Signal: `{params['entry_signal']}` | "
        f"Stop: {float(params['stop_loss_pct'])*100:.0f}% | "
        f"Target: {float(params['target_pct'])*100:.0f}% | "
        f"Hold ≤ {params['max_holding_days']}d | "
        f"Score ≥ {params['min_composite_score']}"
        f"{s2_label_str}"
    )

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    wr_col   = GREEN if m["win_rate"]        >= 50  else RED
    ret_col  = GREEN if m["avg_return_pct"]  >= 0   else RED
    dd_col   = RED   if m["max_drawdown_pct"] < -15 else (ORANGE if m["max_drawdown_pct"] < -8 else GREEN)
    sh_col   = GREEN if m["sharpe_ratio"]    >= 1   else (ORANGE if m["sharpe_ratio"] >= 0 else RED)
    pf_col   = GREEN if m["profit_factor"]   >= 1.5 else (ORANGE if m["profit_factor"] >= 1 else RED)

    with k1:
        st.markdown(_kpi("Win Rate",      f"{m['win_rate']:.1f}%",        wr_col),  unsafe_allow_html=True)
    with k2:
        st.markdown(_kpi("Avg Return",    f"{m['avg_return_pct']:.2f}%",  ret_col), unsafe_allow_html=True)
    with k3:
        st.markdown(_kpi("Profit Factor", f"{m['profit_factor']:.2f}",    pf_col),  unsafe_allow_html=True)
    with k4:
        st.markdown(_kpi("Max Drawdown",  f"{m['max_drawdown_pct']:.1f}%", dd_col), unsafe_allow_html=True)
    with k5:
        st.markdown(_kpi("Sharpe",        f"{m['sharpe_ratio']:.2f}",     sh_col),  unsafe_allow_html=True)
    with k6:
        st.markdown(_kpi("Total Return",  f"{m['total_return_pct']:.1f}%", ret_col), unsafe_allow_html=True)

    # Exit reason breakdown
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Stops",        m["stop_count"])
    e2.metric("Targets hit",  m["target_count"])
    e3.metric("Timeouts",     m["timeout_count"])
    e4.metric("Avg hold",     f"{m['avg_holding_days']:.0f}d")
    e5.metric("Best / Worst", f"{m['best_trade_pct']:.1f}% / {m['worst_trade_pct']:.1f}%")

    # ── Charts ────────────────────────────────────────────────────────────────
    st.divider()
    ch1, ch2 = st.columns(2)
    with ch1:
        st.plotly_chart(_build_equity_curve(tr), use_container_width=True)
    with ch2:
        st.plotly_chart(_build_return_distribution(tr), use_container_width=True)

    # ── Per-regime breakdown ──────────────────────────────────────────────────
    if m.get("by_regime"):
        st.markdown(section_title("Results by Market Regime"), unsafe_allow_html=True)
        # market_regime is only populated from the date python run.py was first executed.
        # Historical backtest dates before that first run will show as UNKNOWN because
        # the LEFT JOIN finds no matching row. Run: python run.py --skip-ingestion
        # for each historical date to populate regime data further back.
        has_only_unknown = list(m["by_regime"].keys()) == ["UNKNOWN"]
        if has_only_unknown:
            st.caption(
                "All trades show **UNKNOWN** regime — `market_regime` table only has data "
                "from the date you first ran `python run.py`. Regime breakdown will populate "
                "once the pipeline has run across your full backtest period."
            )
        regime_df = pd.DataFrame(m["by_regime"]).T.reset_index()
        regime_df.columns = ["Regime", "Trades", "Win Rate %", "Avg Return %"]
        st.dataframe(regime_df, use_container_width=True, hide_index=True)

    # ── Trade table ───────────────────────────────────────────────────────────
    st.divider()
    st.markdown(section_title("Trade Log"), unsafe_allow_html=True)

    display = tr.copy()
    display["return_pct"]      = display["return_pct"].map(lambda x: f"{x:+.2f}%")
    display["composite_score"] = display["composite_score"].map(lambda x: f"{x:.0f}")
    display.columns = [
        c.replace("_", " ").title() for c in display.columns
    ]

    st.dataframe(display, use_container_width=True, hide_index=True, height=400)

    csv = tr.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download trades CSV",
        data=csv,
        file_name=f"backtest_{entry_signal}_{bt_start}_{bt_end}.csv",
        mime="text/csv",
    )
