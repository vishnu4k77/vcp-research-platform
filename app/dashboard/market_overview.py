"""Tab 1 — Market Overview: regime card, Nifty conditions, pipeline health."""

import plotly.graph_objects as go
import streamlit as st

from app.config.strategy_config import DashboardConfig
from app.services.scanner_service import ScannerService
from app.dashboard.styles import (
    BG_APP, BG_CARD, BG_ELEVATED, BORDER, BORDER_MED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    GREEN, RED, ORANGE, BLUE,
    STATE_ACCENT, RUN_STATUS_COLOUR,
    metric_card, condition_card, section_title, state_badge, run_status_pill,
)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_environment() -> dict:
    """Cached fetch of latest market_environment row."""
    return ScannerService.get_latest_environment()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_regime() -> dict:
    """Cached fetch of latest market_regime row."""
    return ScannerService.get_latest_regime()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_regime_history():
    """Cached fetch of historical market_environment rows, ascending by date."""
    return ScannerService.get_regime_history()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_pipeline_runs():
    """Cached fetch of recent pipeline_runs rows."""
    return ScannerService.get_recent_pipeline_runs()


def _exposure_colour(level: float) -> str:
    """Return accent colour for exposure level."""
    if level >= 0.8:
        return GREEN
    if level >= 0.4:
        return ORANGE
    return RED


# Regime score thresholds that define state boundaries (matches market_states seed data).
_REGIME_ZONES = [
    (80, 100, GREEN,  "Bull / Strong Bull"),
    (60,  80, BLUE,   "Neutral"),
    (40,  60, ORANGE, "Defensive"),
    (20,  40, ORANGE, "Correction"),
    (0,   20, RED,    "Bear"),
]


def _build_regime_chart(df) -> go.Figure:
    """Plotly line chart of regime_score over time with state-zone shading.

    Each coloured band corresponds to a market state (Bear/Correction/…/Bull).
    Individual points are coloured by score using the same RdYlGn scale used
    in the heatmap so the colours are visually consistent.

    Args:
        df: DataFrame with columns trade_date, regime_score, market_state.

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()

    # Shaded state zones (drawn below the data line)
    for lo, hi, color, label in _REGIME_ZONES:
        fig.add_hrect(
            y0=lo, y1=hi,
            fillcolor=color,
            opacity=0.07,
            layer="below",
            line_width=0,
            annotation_text=label,
            annotation_position="right",
            annotation=dict(
                font=dict(color=TEXT_MUTED, size=9, family="Inter, sans-serif"),
                xanchor="left",
            ),
        )

    # Thin horizontal boundary lines at each state threshold
    for threshold, color in [(80, GREEN), (60, BLUE), (40, ORANGE), (20, RED)]:
        fig.add_hline(
            y=threshold,
            line_dash="dot",
            line_color=color,
            opacity=0.25,
            line_width=1,
        )

    # Regime score line — points coloured green→red by score value
    fig.add_trace(go.Scatter(
        x=df["trade_date"],
        y=df["regime_score"],
        mode="lines+markers",
        line=dict(color=BLUE, width=2.5),
        marker=dict(
            size=7,
            color=df["regime_score"],
            colorscale="RdYlGn",
            cmin=0,
            cmax=100,
            line=dict(width=1, color="rgba(0,0,0,0.4)"),
        ),
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Score: %{y}/100<br>"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(
        height=220,
        margin=dict(l=0, r=90, t=8, b=0),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        xaxis=dict(
            showgrid=False,
            linecolor=BORDER,
            tickfont=dict(color=TEXT_MUTED, size=10),
        ),
        yaxis=dict(
            range=[-2, 107],
            gridcolor=BORDER,
            tickfont=dict(color=TEXT_MUTED, size=10),
            tickvals=[0, 20, 40, 60, 80, 100],
        ),
        showlegend=False,
        hovermode="x unified",
    )
    return fig


def render_market_overview() -> None:
    """Render the Market Overview tab.

    Layout:
      1. Regime card — state badge + date + 4 metric cards
      2. Nifty 50 condition grid — 5 pass/fail/na indicators
      3. Pipeline health table — last N pipeline runs
    """
    env     = _load_environment()
    reg     = _load_regime()
    history = _load_regime_history()
    runs    = _load_pipeline_runs()

    if not env:
        st.warning("No market environment data found. Run the pipeline first.")
        return

    market_state = env.get("market_state",   "—")
    regime_score = float(env.get("regime_score",  0) or 0)
    exposure     = float(env.get("exposure_level", 0) or 0)
    allow_bo     = bool(env.get("allow_breakouts", False))
    cash_mode    = bool(env.get("cash_mode", True))
    env_date     = env.get("trade_date", "—")
    accent       = STATE_ACCENT.get(market_state, BLUE)

    # ── Regime header ─────────────────────────────────────────────────────────
    st.markdown(section_title("Market Regime"), unsafe_allow_html=True)

    st.markdown(
        f"""
        <div style='
            background: linear-gradient(120deg, {accent}12 0%, {BG_CARD} 55%);
            border: 1px solid {accent}35;
            border-radius: 14px;
            padding: 22px 28px;
            margin-bottom: 1.2rem;
            display: flex;
            align-items: center;
            gap: 16px;
            position: relative;
            overflow: hidden;
        '>
            <div style='
                position:absolute;top:-30px;right:-30px;
                width:140px;height:140px;border-radius:50%;
                background:{accent}0a;filter:blur(30px);
                pointer-events:none;
            '></div>
            {state_badge(market_state)}
            <span style='
                font-size:13px;
                color:{TEXT_MUTED};
                font-family:Inter,sans-serif;
            '>As of <b style='color:{TEXT_SECONDARY};font-weight:600'>{env_date}</b></span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 4 metric cards ────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)

    score_colour = GREEN if regime_score >= 60 else (ORANGE if regime_score >= 40 else RED)
    exp_colour   = _exposure_colour(exposure)
    bo_colour    = GREEN if allow_bo  else RED
    cash_colour  = RED   if cash_mode else GREEN

    with c1:
        st.markdown(
            metric_card("Regime Score", f"{int(regime_score)} / 100", score_colour),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            metric_card("Exposure Level", f"{exposure:.0%}", exp_colour),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            metric_card("Allow Breakouts", "Yes" if allow_bo else "No", bo_colour),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            metric_card("Cash Mode", "Yes" if cash_mode else "No", cash_colour),
            unsafe_allow_html=True,
        )

    # ── Regime history chart ──────────────────────────────────────────────────
    if not history.empty and len(history) > 1:
        st.markdown(
            section_title(
                "Regime Score History",
                f"{len(history)} trading days stored — score 0-100, shaded by market state",
            ),
            unsafe_allow_html=True,
        )
        st.plotly_chart(
            _build_regime_chart(history),
            use_container_width=True,
            key="regime_history_chart",
        )
    elif not history.empty and len(history) == 1:
        st.info(
            "Only one day of regime data in the database. "
            "The history chart will appear after the pipeline runs on more trading days."
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Nifty 50 conditions ───────────────────────────────────────────────────
    above_50  = reg.get("nifty_above_50ema")
    above_200 = reg.get("nifty_above_200ema")

    st.markdown(
        section_title(
            "Nifty 50 Conditions",
            "5 conditions × 20 pts each — EMA-50/200 alignment, slope & higher highs stored in Phase 2",
        ),
        unsafe_allow_html=True,
    )

    cc1, cc2, cc3, cc4, cc5 = st.columns(5)
    conditions = [
        (cc1, "Close > EMA 50",   above_50),
        (cc2, "Close > EMA 200",  above_200),
        (cc3, "EMA 50 > EMA 200", None),
        (cc4, "EMA 200 Slope +",  None),
        (cc5, "Higher Highs",     None),
    ]
    for col, label, value in conditions:
        with col:
            st.markdown(condition_card(label, value), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Pipeline health ───────────────────────────────────────────────────────
    st.markdown(section_title("Pipeline Health"), unsafe_allow_html=True)

    if runs.empty:
        st.info("No pipeline runs recorded yet.")
        return

    # Build a styled HTML table instead of st.dataframe for richer control
    rows_html = ""
    for _, row in runs.iterrows():
        status   = str(row.get("status", ""))
        pill     = run_status_pill(status)
        trade_dt = str(row.get("trade_date", ""))
        start    = str(row.get("start_time", ""))[:19]
        n_rows   = f"{int(row.get('rows_processed', 0)):,}"
        error    = str(row.get("error_message", "") or "")
        error_td = (
            f"<td style='color:{RED};font-size:11px;max-width:260px;"
            f"overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{error}</td>"
            if error and error != "None" else
            f"<td style='color:{TEXT_MUTED}'>—</td>"
        )
        rows_html += f"""
        <tr style='border-top:1px solid {BORDER}'>
            <td style='padding:10px 12px;font-size:12px;color:{TEXT_SECONDARY}'>{trade_dt}</td>
            <td style='padding:10px 12px;font-size:12px;color:{TEXT_MUTED}'>{start}</td>
            <td style='padding:10px 12px'>{pill}</td>
            <td style='padding:10px 12px;font-size:12px;color:{TEXT_SECONDARY};
                       font-variant-numeric:tabular-nums'>{n_rows}</td>
            {error_td}
        </tr>"""

    st.markdown(
        f"""
        <div style='
            background:{BG_CARD};
            border:1px solid {BORDER};
            border-radius:10px;
            overflow:hidden;
            font-family:Inter,sans-serif;
        '>
            <table style='width:100%;border-collapse:collapse'>
                <thead>
                    <tr style='background:{BG_ELEVATED}'>
                        <th style='padding:10px 12px;text-align:left;font-size:10px;
                                   font-weight:600;color:{TEXT_MUTED};
                                   text-transform:uppercase;letter-spacing:0.08em'>
                            Trade Date</th>
                        <th style='padding:10px 12px;text-align:left;font-size:10px;
                                   font-weight:600;color:{TEXT_MUTED};
                                   text-transform:uppercase;letter-spacing:0.08em'>
                            Started</th>
                        <th style='padding:10px 12px;text-align:left;font-size:10px;
                                   font-weight:600;color:{TEXT_MUTED};
                                   text-transform:uppercase;letter-spacing:0.08em'>
                            Status</th>
                        <th style='padding:10px 12px;text-align:left;font-size:10px;
                                   font-weight:600;color:{TEXT_MUTED};
                                   text-transform:uppercase;letter-spacing:0.08em'>
                            Rows</th>
                        <th style='padding:10px 12px;text-align:left;font-size:10px;
                                   font-weight:600;color:{TEXT_MUTED};
                                   text-transform:uppercase;letter-spacing:0.08em'>
                            Error</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )
