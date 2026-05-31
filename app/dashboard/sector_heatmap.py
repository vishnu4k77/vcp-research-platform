"""Tab 2 — Sector Heatmap: card grid, bar chart, or treemap with sector drill-down."""

import logging
from datetime import date
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.config.strategy_config import DashboardConfig
from app.services.scanner_service import ScannerService
from app.dashboard.styles import (
    BG_APP, BORDER, BORDER_MED,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    GREEN, RED, ORANGE, BLUE,
    section_title, metric_card,
)

logger = logging.getLogger(__name__)

# ── Session-state keys ────────────────────────────────────────────────────────
_SK_DRILL     = "sector_drill_select"
_SK_LASTDATE  = "heatmap_last_date"
_SK_LASTINDEX = "heatmap_last_index"
_NO_SECTOR    = "— Select a sector to view stocks —"

# Cards-per-row in the grid view
_CARD_COLS = 5


# ── Cached loaders ────────────────────────────────────────────────────────────

@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_dates() -> list[date]:
    """Cached list of available trade dates.

    Returns:
        List of datetime.date values, descending.
    """
    return ScannerService.get_available_trade_dates()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_index_list() -> list[dict]:
    """Cached index options from nse_index_ref.

    Returns:
        List of dicts with index_code and index_name keys.
    """
    return ScannerService.get_index_list()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_sector_summary(trade_date: date, index_code: Optional[str] = None) -> pd.DataFrame:
    """Cached sector summary for one trade date, optionally filtered to an index.

    Args:
        trade_date: The date to aggregate.
        index_code: Optional NSE index code (e.g. 'NIFTY50'). None = all stocks.

    Returns:
        DataFrame with sector, total_stocks, signal counts, avg_composite_score.
    """
    return ScannerService.get_sector_summary(trade_date, index_code=index_code)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_sector_stocks(
    trade_date: date,
    sector: str,
    index_code: Optional[str] = None,
) -> pd.DataFrame:
    """Cached stock list for a specific sector on a given date.

    Args:
        trade_date: The date to query.
        sector: Sector name (matches nse_universe.sector).
        index_code: Optional NSE index code to restrict results.

    Returns:
        DataFrame with symbol, signals, and composite_score.
    """
    return ScannerService.get_sector_stocks(trade_date, sector, index_code=index_code)


# ── Derived column computation ────────────────────────────────────────────────

def _add_pct_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute percentage columns from raw signal counts.

    Adds rs_pct, liquidity_pct, trend_pct (stage2_pct already present from SQL).

    Args:
        df: Raw DataFrame from ScannerService.get_sector_summary().

    Returns:
        df with additional pct columns appended in-place.
    """
    denom = df["total_stocks"].replace(0, pd.NA)
    df["rs_pct"]        = (df["rs_count"]       / denom * 100).fillna(0.0)
    df["liquidity_pct"] = (df["liquidity_count"] / denom * 100).fillna(0.0)
    df["trend_pct"]     = (df["trend_count"]     / denom * 100).fillna(0.0)
    return df


def _color_bounds(series: pd.Series) -> tuple[float, float]:
    """Return cmin/cmax with a minimum 20-point spread for readable colorscales.

    Enforcing a minimum spread prevents all tiles rendering the same colour
    when sector values cluster in a narrow band.

    Args:
        series: Numeric metric column (0-100 range expected).

    Returns:
        (cmin, cmax) clipped to [0, 100].
    """
    lo, hi = float(series.min()), float(series.max())
    if hi - lo < 20.0:
        mid = (lo + hi) / 2.0
        lo  = max(0.0,   mid - 10.0)
        hi  = min(100.0, mid + 10.0)
    return lo, hi


# ── Card grid helpers ─────────────────────────────────────────────────────────

def _hue(value: float, cmin: float, cmax: float) -> int:
    """Map a metric value to an HSL hue: 0° (red) → 120° (green).

    Args:
        value: Single sector metric value.
        cmin: Colorscale lower bound.
        cmax: Colorscale upper bound.

    Returns:
        Integer hue in [0, 120].
    """
    t = max(0.0, min(1.0, (value - cmin) / max(cmax - cmin, 1.0)))
    return int(t * 120)


def _sector_card(
    sector: str,
    metric_val: float,
    stock_count: int,
    avg_score: float,
    hue_deg: int,
) -> str:
    """Single-line HTML for one sector card in the grid.

    Must be a single line — Streamlit's markdown parser treats indented
    lines as code blocks and breaks multi-line HTML.

    Args:
        sector: Sector display name.
        metric_val: Metric percentage for this sector.
        stock_count: Total stocks in the sector.
        avg_score: Average composite score.
        hue_deg: HSL hue angle (0=red, 120=green).

    Returns:
        Single-line HTML string.
    """
    bg  = f"hsl({hue_deg},38%,10%)"
    bdr = f"hsl({hue_deg},60%,38%)"
    val = f"hsl({hue_deg},75%,60%)"
    return (
        f"<div style='background:{bg};border:1px solid {bdr};"
        f"border-top:3px solid {bdr};border-radius:10px;"
        f"padding:14px 16px;min-height:100px'>"
        f"<div style='font-size:12px;font-weight:600;color:{TEXT_PRIMARY};"
        f"line-height:1.35;font-family:Inter,sans-serif;margin-bottom:8px'>{sector}</div>"
        f"<div style='font-size:22px;font-weight:700;color:{val};"
        f"font-family:Inter,sans-serif;letter-spacing:-0.02em;line-height:1'>{metric_val:.1f}%</div>"
        f"<div style='font-size:11px;color:{TEXT_MUTED};margin-top:6px;"
        f"font-family:Inter,sans-serif'>{stock_count} stocks · avg {avg_score:.0f}</div>"
        f"</div>"
    )


def _render_card_grid(
    df: pd.DataFrame,
    metric_col: str,
) -> None:
    """Render all sectors as an equal-width card grid, sorted by metric descending.

    Every card is the same width so all sector names are always fully readable,
    unlike a treemap where small sectors get unreadably tiny tiles.

    Args:
        df: Sector summary DataFrame with pct columns already added.
        metric_col: Column name to colour and sort by.
    """
    sorted_df = df.sort_values(metric_col, ascending=False).reset_index(drop=True)
    cmin, cmax = _color_bounds(sorted_df[metric_col])

    for row_start in range(0, len(sorted_df), _CARD_COLS):
        chunk = sorted_df.iloc[row_start : row_start + _CARD_COLS]
        cols  = st.columns(_CARD_COLS)
        for col_widget, (_, row) in zip(cols, chunk.iterrows()):
            h = _hue(float(row[metric_col]), cmin, cmax)
            with col_widget:
                st.markdown(
                    _sector_card(
                        sector=str(row["sector"]),
                        metric_val=float(row[metric_col]),
                        stock_count=int(row["total_stocks"]),
                        avg_score=float(row["avg_composite_score"]),
                        hue_deg=h,
                    ),
                    unsafe_allow_html=True,
                )
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)


# ── Plotly chart builders ─────────────────────────────────────────────────────

def _build_bar_chart(
    df: pd.DataFrame,
    metric_col: str,
    metric_label: str,
) -> go.Figure:
    """Horizontal bar chart sorted by metric %, coloured by value.

    Args:
        df: Sector summary DataFrame with pct columns already added.
        metric_col: Column to plot on the x-axis.
        metric_label: Axis label.

    Returns:
        Plotly Figure.
    """
    df_sorted    = df.sort_values(metric_col, ascending=True)
    cmin, cmax   = _color_bounds(df_sorted[metric_col])

    fig = go.Figure(go.Bar(
        x=df_sorted[metric_col].round(1),
        y=df_sorted["sector"],
        orientation="h",
        text=df_sorted[metric_col].round(1).astype(str) + "%",
        textposition="outside",
        marker=dict(
            color=df_sorted[metric_col],
            colorscale="RdYlGn",
            cmin=cmin,
            cmax=cmax,
            showscale=False,
        ),
        customdata=df_sorted[["total_stocks", "avg_composite_score"]].values,
        hovertemplate=(
            "<b>%{y}</b><br>"
            f"{metric_label}: %{{x:.1f}}%<br>"
            "Stocks: %{customdata[0]:.0f}<br>"
            "Avg Score: %{customdata[1]:.1f}<extra></extra>"
        ),
    ))

    fig.update_layout(
        xaxis=dict(
            range=[0, 115],
            gridcolor=BORDER,
            linecolor=BORDER,
            tickfont=dict(color=TEXT_MUTED),
        ),
        yaxis=dict(
            gridcolor=BORDER,
            linecolor=BORDER,
            tickfont=dict(color=TEXT_SECONDARY, size=11),
        ),
        xaxis_title=metric_label,
        yaxis_title=None,
        height=max(420, len(df_sorted) * 28),
        margin=dict(l=0, r=60, t=10, b=40),
        paper_bgcolor=BG_APP,
        plot_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY, size=12),
    )
    return fig


def _build_treemap(
    df: pd.DataFrame,
    metric_col: str,
    metric_label: str,
) -> go.Figure:
    """Sector treemap sized by stock count, coloured by metric %.

    Small tiles (< ~5 stocks) will naturally clip text — hover for full details.
    uniformtext hides labels that would overlap, keeping the chart readable.

    Args:
        df: Sector summary DataFrame with pct columns already added.
        metric_col: Column name to use for tile colour.
        metric_label: Human-readable label shown on the colour bar.

    Returns:
        Plotly Figure.
    """
    cmin, cmax = _color_bounds(df[metric_col])

    fig = go.Figure(go.Treemap(
        labels=df["sector"].tolist(),
        values=df["total_stocks"].tolist(),
        parents=[""] * len(df),
        customdata=df[[metric_col, "total_stocks", "avg_composite_score"]].values,
        marker=dict(
            colors=df[metric_col].tolist(),
            colorscale="RdYlGn",
            cmin=cmin,
            cmax=cmax,
            showscale=True,
            colorbar=dict(
                title=dict(text=metric_label, font=dict(color=TEXT_SECONDARY, size=11)),
                thickness=14,
                tickfont=dict(color=TEXT_MUTED, size=10),
                outlinewidth=0,
            ),
        ),
        textfont=dict(family="Inter, sans-serif", color=TEXT_PRIMARY, size=13),
        texttemplate="<b>%{label}</b><br>%{customdata[0]:.1f}%<br>%{value} stocks",
        hovertemplate=(
            "<b>%{label}</b><br>"
            f"{metric_label}: %{{customdata[0]:.1f}}%<br>"
            "Stocks: %{customdata[1]:.0f}<br>"
            "Avg Score: %{customdata[2]:.1f}<extra></extra>"
        ),
    ))

    fig.update_traces(
        # Hide text that would be smaller than 11px — tiny tiles show colour only.
        # This prevents garbled/clipped labels on small-sector tiles.
        marker_line_width=1,
        marker_line_color="rgba(0,0,0,0.4)",
    )

    fig.update_layout(
        uniformtext=dict(minsize=11, mode="hide"),
        height=500,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor=BG_APP,
        font=dict(family="Inter, sans-serif", color=TEXT_SECONDARY),
    )
    return fig


# ── Sector drill-down panel ───────────────────────────────────────────────────

def _render_sector_drilldown(
    trade_date: date,
    sector: str,
    index_code: Optional[str] = None,
) -> None:
    """Render the full stock list for the selected sector.

    Shows summary cards (total, Stage 2, RS+, avg score), filter toggles,
    and a styled dataframe of all stocks.

    Args:
        trade_date: Date to query (pulled fresh from cache).
        sector: Sector name to drill into.
        index_code: Optional NSE index code to restrict results.
    """
    df_all = _load_sector_stocks(trade_date, sector, index_code=index_code)

    stage2_n = int(df_all["stage2_signal"].sum())    if "stage2_signal"    in df_all.columns else 0
    rs_n     = int(df_all["rs_signal"].sum())         if "rs_signal"        in df_all.columns else 0
    liquid_n = int(df_all["liquidity_signal"].sum())  if "liquidity_signal" in df_all.columns else 0

    st.markdown(
        section_title(
            sector,
            f"{len(df_all)} stocks  |  Stage 2: {stage2_n}  |  RS+: {rs_n}  |  Liquid: {liquid_n}",
        ),
        unsafe_allow_html=True,
    )

    if df_all.empty:
        st.info(f"No stocks found for {sector} on {trade_date}.")
        return

    # ── Summary cards ─────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    avg_score = df_all["composite_score"].mean() if "composite_score" in df_all.columns else 0.0
    with c1:
        st.markdown(metric_card("Total Stocks", str(len(df_all))), unsafe_allow_html=True)
    with c2:
        st.markdown(metric_card("Stage 2", str(stage2_n), GREEN if stage2_n > 0 else RED), unsafe_allow_html=True)
    with c3:
        st.markdown(metric_card("RS Positive", str(rs_n), GREEN if rs_n > 0 else RED), unsafe_allow_html=True)
    with c4:
        st.markdown(metric_card("Avg Score", f"{avg_score:.1f}"), unsafe_allow_html=True)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # ── Filter toggles ─────────────────────────────────────────────────────────
    f1, f2, _ = st.columns([1, 1, 4])
    with f1:
        stage2_only = st.toggle("Stage 2 only", key="drill_stage2", value=False)
    with f2:
        rs_only = st.toggle("RS positive", key="drill_rs", value=False)

    df = df_all.copy()
    if stage2_only and "stage2_signal" in df.columns:
        df = df[df["stage2_signal"] == 1]
    if rs_only and "rs_signal" in df.columns:
        df = df[df["rs_signal"] == 1]

    if df.empty:
        st.info("No stocks match the active filters.")
        return

    # ── Stock table ───────────────────────────────────────────────────────────
    disp_cols = [c for c in [
        "symbol", "company_name", "composite_score",
        "trend_signal", "stage2_signal", "vcp_signal",
        "breakout_signal", "liquidity_signal", "rs_signal",
        "distance_from_52w_high_pct",
    ] if c in df.columns]

    display = df[disp_cols].copy()
    display.rename(columns={
        "symbol":                     "Symbol",
        "company_name":               "Company",
        "composite_score":            "Score",
        "trend_signal":               "Trend",
        "stage2_signal":              "Stage2",
        "vcp_signal":                 "VCP",
        "breakout_signal":            "Breakout",
        "liquidity_signal":           "Liquid",
        "rs_signal":                  "RS",
        "distance_from_52w_high_pct": "Dist 52w%",
    }, inplace=True)
    display.insert(0, "#", range(1, len(display) + 1))

    def _style_sig(v) -> str:
        try:
            return (
                f"background:{GREEN}18;color:{GREEN};font-weight:600"
                if int(v) == 1
                else f"color:{RED};opacity:0.5"
            )
        except (TypeError, ValueError):
            return f"color:{TEXT_MUTED}"

    def _style_score(v) -> str:
        try:
            f = float(v)
            if f >= 70:
                return f"color:{GREEN};font-weight:700"
            if f >= 50:
                return f"color:{ORANGE};font-weight:600"
            return f"color:{RED}"
        except (TypeError, ValueError):
            return ""

    sig_cols = [c for c in ["Trend", "Stage2", "VCP", "Breakout", "Liquid", "RS"] if c in display.columns]
    styled = (
        display.style
        .map(_style_sig, subset=sig_cols)
        .map(_style_score, subset=["Score"] if "Score" in display.columns else [])
        .format({"Score": "{:.1f}", "Dist 52w%": "{:.1f}%"}, na_rep="—")
    )

    # Same pattern as the working Scanner tab:
    #   explicit text/number cols = 1080 px
    #   6 auto boolean cols       =  300 px minimum (GDG's fixed checkbox min)
    #   total                     = 1380 px >> ~1180 px container → real overflow
    _drill_col_cfg: dict = {
        "#":         st.column_config.NumberColumn(format="%d", width=45),
        "Symbol":    st.column_config.TextColumn(width=250),
        "Company":   st.column_config.TextColumn(width=500),
        "Score":     st.column_config.NumberColumn(format="%.1f", width=140),
        "Dist 52w%": st.column_config.NumberColumn(format="%.1f%%", width=190),
    }
    st.dataframe(styled, use_container_width=True, hide_index=True, height=420, column_config=_drill_col_cfg)
    st.caption("Open the Stock Detail tab and enter a symbol to view its full price chart.")


# ── Main render ───────────────────────────────────────────────────────────────

def render_sector_heatmap() -> None:
    """Render the Sector Heatmap tab.

    Layout:
      1. Date / metric / view-type controls
      2. Card grid (default) or bar chart or treemap
      3. Summary metric row
      4. Sector drill-down selectbox → full stock table
      5. All-sectors summary table
    """
    dates   = _load_dates()
    indices = _load_index_list()

    if not dates:
        st.warning("No signal data found. Run the pipeline first.")
        return

    # ── Controls row ──────────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 2])

    with ctrl1:
        selected_date = st.selectbox(
            "Trade date", options=dates, format_func=str, key="heatmap_date"
        )

    with ctrl2:
        metric_options = list(DashboardConfig.HEATMAP_METRICS.keys())
        default_idx = next(
            (i for i, k in enumerate(DashboardConfig.HEATMAP_METRICS)
             if DashboardConfig.HEATMAP_METRICS[k] == DashboardConfig.HEATMAP_DEFAULT_METRIC),
            0,
        )
        selected_label = st.selectbox(
            "Metric", options=metric_options, index=default_idx, key="heatmap_metric"
        )
        metric_col = DashboardConfig.HEATMAP_METRICS[selected_label]

    with ctrl3:
        index_options: dict[str, Optional[str]] = {"All indices": None}
        for idx in indices:
            index_options[f"{idx['index_name']} ({idx['index_code']})"] = idx["index_code"]
        selected_index_label = st.selectbox(
            "NSE Index", options=list(index_options.keys()), key="heatmap_index"
        )
        index_code: Optional[str] = index_options[selected_index_label]

    viz_type = st.radio(
        "View",
        options=["Grid", "Bars", "Treemap"],
        horizontal=True,
        key="heatmap_viz",
    )

    # ── Load + enrich data ────────────────────────────────────────────────────
    df = _load_sector_summary(selected_date, index_code=index_code)

    if df.empty:
        st.info("No signal data for this date.")
        return

    if df["sector"].nunique() == 1 and df["sector"].iloc[0] == "Unknown":
        st.info("Sector names not yet available. Run `python scripts/fetch_nse_tickers.py`.")
        return

    df = _add_pct_columns(df)

    if metric_col in ("stage2_pct", "trend_pct") and df[metric_col].max() == 0.0:
        st.warning(
            f"All **{selected_label}** values are 0% — signals suppressed in BEAR / cash mode.  "
            "Switch metric to **RS vs Nifty %** to see meaningful data."
        )

    # ── Chart section ─────────────────────────────────────────────────────────
    index_subtitle = f"  |  {selected_index_label}" if index_code else ""
    st.markdown(
        section_title(
            f"Sector Heatmap — {selected_date}",
            f"{len(df)} sectors{index_subtitle}  |  Sorted by {selected_label}",
        ),
        unsafe_allow_html=True,
    )

    # Key includes date + metric + index so Plotly mounts a fresh chart whenever
    # any of the three controls change — clears zoom state from treemap clicks.
    chart_key = f"{selected_date}_{metric_col}_{index_code}"

    if viz_type == "Grid":
        _render_card_grid(df, metric_col)
    elif viz_type == "Bars":
        fig = _build_bar_chart(df, metric_col, selected_label)
        st.plotly_chart(fig, use_container_width=True, key=f"bar_{chart_key}")
    else:
        fig = _build_treemap(df, metric_col, selected_label)
        st.plotly_chart(fig, use_container_width=True, key=f"treemap_{chart_key}")
        st.caption("Hover tiles for details. Small tiles show colour only — use the dropdown below for stock lists.")

    # ── Summary metric row ────────────────────────────────────────────────────
    top_row = df.sort_values(metric_col, ascending=False).iloc[0]
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Stocks", f"{int(df['total_stocks'].sum())}")
    m2.metric("Sectors", f"{len(df)}")
    m3.metric("Top Sector", top_row["sector"])
    m4.metric(f"Top {selected_label}", f"{top_row[metric_col]:.1f}%")

    # ── Sector drill-down selector ────────────────────────────────────────────
    # Reset the sector selectbox when either date or index changes, so stale
    # drill-down data from a previous filter combination is never shown.
    _reset_key = f"{selected_date}|{index_code}"
    if st.session_state.get(_SK_LASTDATE) != _reset_key:
        st.session_state[_SK_LASTDATE] = _reset_key
        st.session_state[_SK_DRILL]    = _NO_SECTOR

    st.divider()
    sector_options = [_NO_SECTOR] + df.sort_values(metric_col, ascending=False)["sector"].tolist()
    drill_sector   = st.selectbox(
        "View stocks in sector",
        options=sector_options,
        key=_SK_DRILL,
    )

    if drill_sector != _NO_SECTOR:
        _render_sector_drilldown(selected_date, drill_sector, index_code=index_code)

    # ── All-sectors summary table ─────────────────────────────────────────────
    st.divider()
    st.markdown(section_title("All Sectors"), unsafe_allow_html=True)

    display = df[[
        "sector", "total_stocks",
        "stage2_pct", "trend_pct", "trend_count",
        "rs_pct", "rs_count",
        "liquidity_pct", "avg_composite_score",
    ]].copy().sort_values(metric_col, ascending=False)

    display.columns = [
        "Sector", "Stocks",
        "Stage 2 %", "Trend %", "Trend #",
        "RS %", "RS #",
        "Liquidity %", "Avg Score",
    ]
    for col in ("Stage 2 %", "Trend %", "RS %", "Liquidity %", "Avg Score"):
        display[col] = display[col].round(1)
    display.insert(0, "#", range(1, len(display) + 1))

    # Boolean indicator columns — NO explicit width so GDG uses its fixed
    # checkbox minimum (~50 px each × 3 = 150 px).  Combined with 8 explicit
    # numeric columns (1000 px) and Sector auto (~180 px), total minimum
    # ≈ 1330 px >> ~1180 px container → real overflow → horizontal scrollbar
    # (same mechanism as the working Scanner and drill-down tables).
    display["Has Stage2"] = display["Stage 2 %"] > 0
    display["Has RS"]     = display["RS %"] > 0
    display["Has Trend"]  = display["Trend %"] > 0

    _sector_col_cfg: dict = {
        "#":           st.column_config.NumberColumn(format="%d", width=45),
        "Stocks":      st.column_config.NumberColumn(width=125),
        "Stage 2 %":   st.column_config.NumberColumn(width=125),
        "Trend %":     st.column_config.NumberColumn(width=125),
        "Trend #":     st.column_config.NumberColumn(width=125),
        "RS %":        st.column_config.NumberColumn(width=125),
        "RS #":        st.column_config.NumberColumn(width=125),
        "Liquidity %": st.column_config.NumberColumn(width=125),
        "Avg Score":   st.column_config.NumberColumn(width=125),
    }
    st.dataframe(display, use_container_width=True, hide_index=True, column_config=_sector_col_cfg)
