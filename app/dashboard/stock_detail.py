"""Tab 4 — Stock Detail: price chart, signal card, fundamentals panel, score history."""

from datetime import date, datetime
from typing import Optional

import plotly.graph_objects as go
import streamlit as st

from app.config.strategy_config import DashboardConfig, FundamentalsConfig, StrategyConfig
from app.services.scanner_service import ScannerService
from app.dashboard.styles import (
    BG_APP, BG_CARD, BG_ELEVATED, BORDER, BORDER_MED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    GREEN, RED, ORANGE, BLUE,
    section_title, metric_card,
)

# ── Cached loaders ────────────────────────────────────────────────────────────

@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_symbol_options() -> list[str]:
    """Cached formatted display options for the Stock Detail search selectbox.

    Returns all-time processed symbols (no date filter) as
    "Company Name  (SYMBOL.NS)" strings so the user can search by
    company name or ticker in Streamlit's native selectbox filter.

    Returns:
        List of canonical display strings sorted by company name.
    """
    return ScannerService.get_symbol_display_options()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_stock(symbol: str) -> object:
    """Cached price + signal history for one symbol."""
    return ScannerService.get_stock_detail(symbol)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_fundamentals(symbol: str) -> dict:
    """Cached latest fundamental metrics for one symbol."""
    return ScannerService.get_stock_fundamentals(symbol)


# ── Chart builders ────────────────────────────────────────────────────────────

def _build_price_chart(df, symbol: str, show_emas: dict[str, bool]) -> go.Figure:
    """Plotly close-price chart with optional EMA overlays, breakout and VCP markers.

    Args:
        df: DataFrame from ScannerService.get_stock_detail().
        symbol: Ticker string used in the chart title.
        show_emas: Dict mapping EMA column names → bool (show/hide).

    Returns:
        Plotly Figure.
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["trade_date"],
        y=df["close_price"],
        name="Close",
        line=dict(color="#1f77b4", width=2),
        hovertemplate="<b>%{x}</b><br>Close: %{y:.2f}<extra></extra>",
    ))

    ema_colours = {
        "ema_10":  "#ff7f0e",
        "ema_21":  "#2ca02c",
        "ema_50":  "#d62728",
        "ema_150": "#9467bd",
        "ema_200": "#8c564b",
    }
    ema_labels = {
        "ema_10":  "EMA 10",
        "ema_21":  "EMA 21",
        "ema_50":  "EMA 50",
        "ema_150": "EMA 150",
        "ema_200": "EMA 200",
    }

    for col, visible in show_emas.items():
        if not visible or col not in df.columns:
            continue
        fig.add_trace(go.Scatter(
            x=df["trade_date"],
            y=df[col],
            name=ema_labels.get(col, col),
            line=dict(color=ema_colours.get(col, "grey"), width=1, dash="dot"),
            hovertemplate=f"{ema_labels.get(col, col)}: %{{y:.2f}}<extra></extra>",
        ))

    bo_rows = df[df["breakout_signal"] == 1] if "breakout_signal" in df.columns else None
    if bo_rows is not None and not bo_rows.empty:
        fig.add_trace(go.Scatter(
            x=bo_rows["trade_date"],
            y=bo_rows["close_price"],
            mode="markers",
            name="Breakout",
            marker=dict(symbol="triangle-up", color="green", size=12),
            hovertemplate="<b>BREAKOUT</b><br>%{x}<br>Close: %{y:.2f}<extra></extra>",
        ))

    vcp_rows = df[df["vcp_signal"] == 1] if "vcp_signal" in df.columns else None
    if vcp_rows is not None and not vcp_rows.empty:
        fig.add_trace(go.Scatter(
            x=vcp_rows["trade_date"],
            y=vcp_rows["close_price"],
            mode="markers",
            name="VCP",
            marker=dict(symbol="diamond", color="purple", size=10),
            hovertemplate="<b>VCP</b><br>%{x}<br>Close: %{y:.2f}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=f"{symbol} — Price & EMAs", font=dict(color=TEXT_PRIMARY, size=15)),
        xaxis_title=None,
        yaxis_title="Price (INR)",
        height=500,
        hovermode="x unified",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(color=TEXT_SECONDARY, size=11),
        ),
        margin=dict(l=0, r=0, t=60, b=40),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY, size=12),
        xaxis=dict(gridcolor=BORDER, linecolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        yaxis=dict(gridcolor=BORDER, linecolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
    )
    return fig


def _build_score_chart(df) -> go.Figure:
    """Plotly area chart of composite_score over time with IC threshold line.

    Args:
        df: DataFrame from ScannerService.get_stock_detail().

    Returns:
        Plotly Figure.
    """
    fig = go.Figure(go.Scatter(
        x=df["trade_date"],
        y=df["composite_score"],
        fill="tozeroy",
        line=dict(color="#1f77b4", width=1.5),
        hovertemplate="<b>%{x}</b><br>Score: %{y:.1f}<extra></extra>",
    ))
    fig.add_hline(
        y=70,
        line_dash="dash",
        line_color="green",
        annotation_text="IC threshold (70)",
        annotation_position="bottom right",
    )
    fig.update_layout(
        title=dict(text="Composite Score History", font=dict(color=TEXT_PRIMARY, size=14)),
        yaxis=dict(range=[0, 105], gridcolor=BORDER, linecolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        xaxis=dict(gridcolor=BORDER, linecolor=BORDER, tickfont=dict(color=TEXT_MUTED)),
        height=250,
        margin=dict(l=0, r=0, t=50, b=30),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY, size=12),
    )
    return fig


# ── Fundamentals panel ────────────────────────────────────────────────────────

def _fund_card(
    label: str,
    value: Optional[float],
    fmt: str,
    threshold: Optional[str],
    passes: Optional[bool],
) -> str:
    """Single-line HTML for one fundamental metric card.

    Shows: label, formatted value (green=pass, red=fail, muted=no data),
    threshold hint at bottom, and a ✓/✗ badge.

    Args:
        label: Short metric name (e.g. 'ROE').
        value: Numeric value, or None if not scraped.
        fmt: Python format string for value display (e.g. '{:.1f}%').
        threshold: Short threshold description (e.g. '≥ 12%') shown below value.
        passes: True=pass, False=fail, None=no data.

    Returns:
        Single-line HTML string.
    """
    if passes is True:
        bg, bdr, val_col, badge_bg, badge_col, badge_txt = (
            f"{GREEN}0d", f"{GREEN}33", GREEN, f"{GREEN}22", GREEN, "✓"
        )
    elif passes is False:
        bg, bdr, val_col, badge_bg, badge_col, badge_txt = (
            f"{RED}0a", f"{RED}2a", RED, f"{RED}22", RED, "✗"
        )
    else:
        bg, bdr, val_col, badge_bg, badge_col, badge_txt = (
            "rgba(255,255,255,0.02)", BORDER, TEXT_MUTED, "rgba(255,255,255,0.06)", TEXT_MUTED, "—"
        )

    val_str = fmt.format(value) if value is not None else "—"
    thr_str = f"<div style='font-size:10px;color:{TEXT_MUTED};margin-top:4px;font-family:Inter,sans-serif'>{threshold}</div>" if threshold else ""

    return (
        f"<div style='background:{bg};border:1px solid {bdr};border-radius:10px;"
        f"padding:14px 16px;min-height:90px;position:relative'>"
        f"<div style='position:absolute;top:8px;right:8px;background:{badge_bg};"
        f"color:{badge_col};border-radius:4px;padding:1px 6px;"
        f"font-size:11px;font-weight:700;font-family:Inter,sans-serif'>{badge_txt}</div>"
        f"<div style='font-size:10px;font-weight:600;color:{TEXT_MUTED};"
        f"text-transform:uppercase;letter-spacing:0.08em;"
        f"margin-bottom:8px;font-family:Inter,sans-serif'>{label}</div>"
        f"<div style='font-size:20px;font-weight:700;color:{val_col};"
        f"font-family:Inter,sans-serif;letter-spacing:-0.02em;line-height:1'>{val_str}</div>"
        f"{thr_str}</div>"
    )


def _render_fundamentals_panel(fund: dict) -> None:
    """Render the fundamentals scorecard section in the Stock Detail tab.

    Shows 10 fundamental metrics in two rows of 5 cards each, with
    green (pass) / red (fail) / muted (no data) styling per criterion.
    All thresholds come from StrategyConfig.FUNDAMENTAL_FILTERS — zero
    magic numbers in this function.

    Args:
        fund: Dict returned by ScannerService.get_stock_fundamentals().
              Empty dict → renders an informational "no data" state.
    """
    ff = StrategyConfig.FUNDAMENTAL_FILTERS

    # ── Section header ────────────────────────────────────────────────────────
    score      = fund.get("quality_score")
    scraped_at = fund.get("scraped_at")

    if scraped_at:
        try:
            if hasattr(scraped_at, "date"):
                days_ago = (date.today() - scraped_at.date()).days
            else:
                days_ago = (date.today() - datetime.strptime(str(scraped_at)[:10], "%Y-%m-%d").date()).days
            freshness = f"Scraped {days_ago}d ago"
        except Exception:
            freshness = "Scraped recently"
    else:
        freshness = "No data — run fetch_fundamentals.py"

    score_badge = ""
    if score is not None:
        pass_score = FundamentalsConfig.QUALITY_PASS_SCORE
        score_col  = GREEN if score >= pass_score else (ORANGE if score >= pass_score - 2 else RED)
        score_badge = (
            f"<span style='background:{score_col}18;color:{score_col};"
            f"border:1px solid {score_col}40;border-radius:5px;"
            f"padding:2px 10px;font-size:11px;font-weight:600;"
            f"font-family:Inter,sans-serif;margin-left:10px'>{score}/10 criteria</span>"
        )

    st.markdown(
        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:16px'>"
        f"<div style='font-size:15px;font-weight:600;color:{TEXT_PRIMARY};"
        f"font-family:Inter,sans-serif'>Fundamentals</div>"
        f"{score_badge}"
        f"<span style='font-size:11px;color:{TEXT_MUTED};margin-left:8px;"
        f"font-family:Inter,sans-serif'>{freshness}</span></div>",
        unsafe_allow_html=True,
    )

    if not fund:
        st.info(
            "No fundamental data for this symbol yet. "
            "Run `python scripts/fetch_fundamentals.py` to scrape Screener.in."
        )
        return

    # ── Row 1: Profitability + leverage ──────────────────────────────────────
    roe  = fund.get("roe")
    roce = fund.get("roce")
    de   = fund.get("debt_to_equity")
    prom = fund.get("promoter_holding")
    plge = fund.get("promoter_pledge_pct")

    r1 = st.columns(5)
    r1[0].markdown(_fund_card(
        "ROE", roe, "{:.1f}%",
        f"≥ {ff['roe_min']}%",
        (roe >= ff["roe_min"]) if roe is not None else None,
    ), unsafe_allow_html=True)
    r1[1].markdown(_fund_card(
        "ROCE", roce, "{:.1f}%",
        f"≥ {ff['roce_min']}%",
        (roce >= ff["roce_min"]) if roce is not None else None,
    ), unsafe_allow_html=True)
    r1[2].markdown(_fund_card(
        "Debt / Equity", de, "{:.2f}",
        f"≤ {ff['debt_to_equity_max']}",
        (de <= ff["debt_to_equity_max"]) if de is not None else None,
    ), unsafe_allow_html=True)
    r1[3].markdown(_fund_card(
        "Promoter %", prom, "{:.1f}%",
        f"≥ {ff['promoter_holding_min']}%",
        (prom >= ff["promoter_holding_min"]) if prom is not None else None,
    ), unsafe_allow_html=True)
    r1[4].markdown(_fund_card(
        "Pledge %", plge, "{:.1f}%",
        f"≤ {ff['promoter_pledge_max']}%",
        (plge <= ff["promoter_pledge_max"]) if plge is not None else None,
    ), unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Row 2: Growth + size + valuation ─────────────────────────────────────
    sg   = fund.get("sales_growth_3yr")
    pg   = fund.get("profit_growth_3yr")
    opm  = fund.get("opm")
    mcap = fund.get("market_cap_cr")
    eps  = fund.get("eps_ttm")

    r2 = st.columns(5)
    r2[0].markdown(_fund_card(
        "Sales Growth 3Y", sg, "{:.1f}%",
        f"≥ {ff['sales_growth_min']}%",
        (sg >= ff["sales_growth_min"]) if sg is not None else None,
    ), unsafe_allow_html=True)
    r2[1].markdown(_fund_card(
        "Profit Growth 3Y", pg, "{:.1f}%",
        f"≥ {ff['profit_growth_min']}%",
        (pg >= ff["profit_growth_min"]) if pg is not None else None,
    ), unsafe_allow_html=True)
    r2[2].markdown(_fund_card(
        "OPM", opm, "{:.1f}%",
        f"≥ {ff['opm_min']}%",
        (opm >= ff["opm_min"]) if opm is not None else None,
    ), unsafe_allow_html=True)
    r2[3].markdown(_fund_card(
        "Market Cap", mcap, "₹{:,.0f}Cr",
        f"≥ ₹{ff['market_cap_min_cr']:,}Cr",
        (mcap >= ff["market_cap_min_cr"]) if mcap is not None else None,
    ), unsafe_allow_html=True)
    r2[4].markdown(_fund_card(
        "EPS TTM", eps, "{:.2f}",
        "display only",
        None,
    ), unsafe_allow_html=True)

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Row 3: EPS acceleration ───────────────────────────────────────────────
    # eps_acceleration BIT: 1=accelerating, 0=not, NULL=insufficient quarterly data.
    # _fund_card's fmt string need not reference the value — plain "Yes"/"No" is valid.
    eps_accel_raw = fund.get("eps_acceleration")
    if eps_accel_raw is not None:
        eps_accel_bool = bool(int(eps_accel_raw))
        eps_accel_card = _fund_card(
            "EPS Accel (Qtly)",
            1.0 if eps_accel_bool else 0.0,
            "Yes" if eps_accel_bool else "No",
            "Q-2 < Q-1 < Q YoY growth",
            eps_accel_bool,
        )
    else:
        eps_accel_card = _fund_card(
            "EPS Accel (Qtly)", None, "—",
            "Q-2 < Q-1 < Q YoY growth", None,
        )

    r3 = st.columns(5)
    r3[0].markdown(eps_accel_card, unsafe_allow_html=True)


# ── Main render ───────────────────────────────────────────────────────────────

def render_stock_detail() -> None:
    """Render the Stock Detail tab.

    Layout:
      1. Symbol selector + EMA overlay checkboxes
      2. Price + EMA chart with breakout / VCP markers
      3. Current Signals — 8 binary signal cards
      4. Fundamentals scorecard — 10 metric cards with pass/fail
      5. Composite Score + Dist from Pivot + Dist from 52w High
      6. Composite Score history chart
    """
    options = _load_symbol_options()

    if not options:
        st.warning("No symbols found. Run the pipeline first.")
        return

    # Match a symbol navigated from Scanner tab (stored as bare ticker in session state)
    nav_symbol = st.session_state.get("detail_symbol")
    default_idx = 0
    if nav_symbol:
        for i, opt in enumerate(options):
            if ScannerService.parse_symbol_from_option(opt) == nav_symbol:
                default_idx = i
                break

    col_input, col_emas = st.columns([2, 3])

    with col_input:
        selected_option = st.selectbox(
            "Search company or symbol",
            options=options,
            index=default_idx,
            key="detail_symbol_select",
        )

    symbol = ScannerService.parse_symbol_from_option(selected_option) if selected_option else None

    with col_emas:
        st.write("EMA overlays")
        ema_cols = st.columns(5)
        show_emas = {
            "ema_10":  ema_cols[0].checkbox("EMA 10",  value=False, key="ema10"),
            "ema_21":  ema_cols[1].checkbox("EMA 21",  value=False, key="ema21"),
            "ema_50":  ema_cols[2].checkbox("EMA 50",  value=True,  key="ema50"),
            "ema_150": ema_cols[3].checkbox("EMA 150", value=False, key="ema150"),
            "ema_200": ema_cols[4].checkbox("EMA 200", value=True,  key="ema200"),
        }

    if not symbol:
        return

    df   = _load_stock(symbol)
    fund = _load_fundamentals(symbol)

    if df.empty:
        st.warning(
            f"No data found for {symbol}. "
            "The pipeline may not have processed this symbol yet."
        )
        return

    # ── 1. Price chart ────────────────────────────────────────────────────────
    fig_price = _build_price_chart(df, symbol, show_emas)
    st.plotly_chart(fig_price, use_container_width=True)

    # ── 2. Current technical signals ──────────────────────────────────────────
    st.markdown(section_title("Current Signals"), unsafe_allow_html=True)
    latest = df.iloc[-1]

    signal_map = {
        "Trend":     "trend_signal",
        "Stage 2":   "stage2_signal",
        "VCP":       "vcp_signal",
        "Breakout":  "breakout_signal",
        "BO Ready":  "breakout_ready_signal",
        "Liquidity": "liquidity_signal",
        "Quality":   "quality_signal",
        "RS":        "rs_signal",
    }

    sig_cols = st.columns(len(signal_map))
    for col, (label, field) in zip(sig_cols, signal_map.items()):
        val = latest.get(field)
        try:
            is_on   = int(val) == 1
            dot_col = GREEN if is_on else RED
            bg      = f"{GREEN}10" if is_on else f"{RED}10"
            border  = f"{GREEN}33" if is_on else f"{RED}33"
            txt     = "ON"  if is_on else "OFF"
        except (TypeError, ValueError):
            dot_col, bg, border, txt = TEXT_MUTED, "rgba(255,255,255,0.03)", BORDER, "N/A"
        with col:
            st.markdown(
                f"<div style='background:{bg};border:1px solid {border};"
                f"border-radius:8px;padding:12px 14px;text-align:center'>"
                f"<div style='font-size:10px;color:{TEXT_MUTED};font-weight:600;"
                f"text-transform:uppercase;letter-spacing:0.08em;"
                f"margin-bottom:8px;font-family:Inter,sans-serif'>{label}</div>"
                f"<span style='width:8px;height:8px;border-radius:50%;"
                f"background:{dot_col};display:inline-block;"
                f"margin-right:5px;vertical-align:middle'></span>"
                f"<span style='font-size:12px;font-weight:600;color:{dot_col};"
                f"font-family:Inter,sans-serif'>{txt}</span></div>",
                unsafe_allow_html=True,
            )

    # ── Stage 2 age context ───────────────────────────────────────────────────
    # Only show when the stock is currently in Stage 2
    s2_days  = latest.get("stage2_days")
    s2_start = latest.get("stage2_started_date")
    is_in_s2 = (latest.get("stage2_signal") == 1) or (latest.get("stage2_signal") == "1")

    if is_in_s2 and s2_days is not None:
        try:
            days = int(s2_days)
        except (TypeError, ValueError):
            days = 0

        if days > 0:
            early_max = StrategyConfig.STAGE2_EARLY_MAX_DAYS
            mid_max   = StrategyConfig.STAGE2_MID_MAX_DAYS

            if days <= early_max:
                age_col  = GREEN
                age_label = "EARLY"
                age_hint  = "Fresh entry — optimal breakout window"
            elif days <= mid_max:
                age_col  = ORANGE
                age_label = "MID"
                age_hint  = "Active trend — watch for VCP/continuation"
            else:
                age_col  = RED
                age_label = "ADVANCED"
                age_hint  = "Long-running Stage 2 — watch for Stage 3 top"

            start_str = ""
            if s2_start is not None:
                try:
                    if hasattr(s2_start, "strftime"):
                        start_str = f" · started {s2_start.strftime('%d %b %Y')}"
                    else:
                        from datetime import datetime
                        start_str = f" · started {str(s2_start)[:10]}"
                except Exception:
                    start_str = ""

            st.markdown(
                f"<div style='margin-top:8px;margin-bottom:4px;"
                f"display:flex;align-items:center;gap:10px'>"
                f"<span style='font-size:10px;font-weight:700;color:{age_col};"
                f"background:{age_col}18;border:1px solid {age_col}40;"
                f"border-radius:4px;padding:2px 8px;"
                f"font-family:Inter,sans-serif;text-transform:uppercase'>"
                f"{age_label} STAGE 2</span>"
                f"<span style='font-size:12px;color:{age_col};"
                f"font-family:Inter,sans-serif;font-weight:600'>"
                f"Day {days}{start_str}</span>"
                f"<span style='font-size:11px;color:{TEXT_MUTED};"
                f"font-family:Inter,sans-serif'>{age_hint}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── 3. Fundamentals scorecard ─────────────────────────────────────────────
    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
    st.markdown(
        f"<div style='height:1px;background:{BORDER};margin-bottom:24px'></div>",
        unsafe_allow_html=True,
    )
    _render_fundamentals_panel(fund)

    # ── 4. Composite metrics row ───────────────────────────────────────────────
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    score_val  = latest.get("composite_score")
    dist_pivot = latest.get("distance_from_pivot_pct")
    dist_52w   = latest.get("distance_from_52w_high_pct")

    score_colour = GREEN if (score_val or 0) >= 70 else (BLUE if (score_val or 0) >= 50 else RED)
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            metric_card("Composite Score", f"{score_val:.1f}" if score_val is not None else "—", score_colour),
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            metric_card("Dist from Pivot", f"{dist_pivot:.1f}%" if dist_pivot is not None else "—"),
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            metric_card("Dist from 52w High", f"{dist_52w:.1f}%" if dist_52w is not None else "—"),
            unsafe_allow_html=True,
        )

    # ── 5. Score history chart ────────────────────────────────────────────────
    if "composite_score" in df.columns and df["composite_score"].notna().any():
        st.divider()
        fig_score = _build_score_chart(df)
        st.plotly_chart(fig_score, use_container_width=True)
