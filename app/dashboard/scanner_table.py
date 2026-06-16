"""Tab 3 — Scanner: ranked candidates table with sidebar filters and scanner presets.

Scanner presets let the user switch between different signal-weight strategies
(VCP Setup, Breakout Day, RS Leaders, etc.) without re-running the pipeline.
The composite score is recomputed in-memory from raw signal columns using the
selected preset's weights — zero DB changes required to add or tune a preset.
"""

from datetime import date
from html import escape as _html_escape
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st

from app.config.strategy_config import DashboardConfig, FundamentalsConfig, StrategyConfig
from app.services.scanner_service import ScannerService
from app.dashboard.styles import GREEN, RED, ORANGE, BLUE, TEXT_MUTED, section_title
import app.dashboard.scanner_filters as _scanner_filters


# ── Calendar enhancements (JS template — min_date injected at render time) ────

def _inject_calendar_js(min_date: date) -> None:
    """Inject calendar JS via st.markdown img-onerror — runs in the parent page context.

    Why img onerror, not components.html:
        components.html() creates a blob-URL iframe which is cross-origin relative
        to the Streamlit app. window.parent.document raises SecurityError, so all
        previous component-based JS silently fell back to the empty iframe document
        and never found the calendar.

        st.markdown(unsafe_allow_html=True) renders HTML directly into the Streamlit
        page DOM. An <img> with a broken src fires onerror in the SAME JavaScript
        context as the parent page — giving direct access to the calendar elements.

    Three behaviours:
        1. Dim dates before min_date using aria-label date parsing (reliable;
           aria-disabled is set lazily by React and may not be present yet).
        2. Remove the 6-row height cap on the month dropdown so July-Dec are visible.
        3. Mouse wheel over the calendar scrolls months.

    Args:
        min_date: Earliest date with signal data in stock_signals.
    """
    iso = min_date.isoformat()

    # Build JS as a string — no .format() or f-string braces needed
    js = (
        "if(!window._vcpCal){"
        "window._vcpCal=true;"
        "var MIN=new Date('" + iso + "');"
        "MIN.setHours(0,0,0,0);"

        # dim one element via inline style (beats CSS !important)
        "function dim(el){"
        "el.style.setProperty('opacity','0.2','important');"
        "el.style.setProperty('text-decoration','line-through','important');"
        "el.style.setProperty('pointer-events','none','important');"
        "el.style.setProperty('cursor','not-allowed','important');"
        "el.style.setProperty('color','#4a5568','important');}"

        # restore an element to full brightness
        "function bright(el){"
        "el.style.removeProperty('opacity');"
        "el.style.removeProperty('text-decoration');"
        "el.style.removeProperty('pointer-events');"
        "el.style.removeProperty('cursor');"
        "el.style.removeProperty('color');}"

        # apply disabled/enabled styling to every gridcell in the calendar
        "function style(cal){"
        "cal.querySelectorAll('[role=\"gridcell\"]').forEach(function(c){"
        # find the element with an aria-label (cell itself or child)
        "var el=c.hasAttribute('aria-label')?c:c.querySelector('[aria-label]');"
        "if(!el)return;"
        "var d=new Date(el.getAttribute('aria-label'));"
        "if(isNaN(d.getTime()))return;"
        "d.setHours(0,0,0,0);"
        "if(d<MIN){dim(c);if(el!==c)dim(el);}"
        "else{bright(c);if(el!==c)bright(el);}"
        "});}"

        # fix month dropdown height so all 12 months are visible
        "function fixDD(){"
        "document.querySelectorAll('[data-baseweb=\"menu\"],[role=\"listbox\"]').forEach(function(el){"
        "el.style.setProperty('max-height','320px','important');"
        "el.style.setProperty('overflow-y','auto','important');});}"

        # wheel on calendar → prev/next month
        "document.addEventListener('wheel',function(e){"
        "var c=document.querySelector('[data-baseweb=\"calendar\"]');"
        "if(!c)return;"
        "var r=c.getBoundingClientRect();"
        "if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)return;"
        "e.preventDefault();e.stopPropagation();"
        "var h=c.querySelector('div>div:first-child');"
        "var b=h?Array.from(h.querySelectorAll('button')):[];"
        "if(b.length>=2)(e.deltaY>0?b[b.length-1]:b[0]).click();"
        "},{passive:false,capture:true});"

        # MutationObserver: re-apply on every DOM change with 50ms React flush delay
        "function run(){"
        "var c=document.querySelector('[data-baseweb=\"calendar\"]');"
        "if(c){style(c);fixDD();}}"
        "new MutationObserver(function(){setTimeout(run,50);})"
        ".observe(document.body,{subtree:true,childList:true});"
        "run();setTimeout(run,150);"
        "}"  # end if(!window._vcpCal)
    )

    # html.escape() converts " → &quot;, ' → &#x27; so the JS is safe inside
    # onerror="...". The browser decodes entities before executing the handler,
    # so the JS receives the original characters unchanged.
    safe_js = _html_escape(js, quote=True)

    # st.markdown renders directly into the Streamlit page DOM (not in an iframe),
    # so the onerror handler runs in the main page JS context with full access to
    # the calendar elements.  display:none keeps the img invisible.
    st.markdown(
        f'<img src="x" style="display:none" onerror="{safe_js}">',
        unsafe_allow_html=True,
    )


# ── Cached data loaders ───────────────────────────────────────────────────────

@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_date_range() -> tuple[date, date] | tuple[None, None]:
    """Cached (min_date, max_date) of all signal data — bounds for the calendar picker."""
    return ScannerService.get_signal_date_range()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_index_list() -> list[dict]:
    """Cached index list from nse_index_ref."""
    return ScannerService.get_index_list()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_scanner(trade_date: date, index_code) -> pd.DataFrame:
    """Cached full-universe scanner rows for a date + index combination.

    Loads with min_score=0 and SCANNER_FETCH_N rows so that preset reranking
    in the caller can score the FULL universe before applying the display TOP N.
    The min_score display filter is applied after reranking in render_scanner_table().
    """
    return ScannerService.get_scanner_data(
        trade_date=trade_date,
        index_code=index_code or None,
        min_score=0.0,
    )


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_last_signal_date() -> Optional[date]:
    """Cached most-recent date with at least one active directional signal."""
    return ScannerService.get_last_signal_date()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_environment_for_date(trade_date: date) -> dict:
    """Cached market_environment row for the selected date.

    Returns empty dict when no row exists (holiday, pre-pipeline run).
    """
    return ScannerService.get_environment_for_date(trade_date)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_last_signal_date_before(trade_date: date) -> Optional[date]:
    """Cached last active signal date on or before trade_date.

    Used for the bear watchlist so historical bear periods resolve the correct
    signal snapshot — not the global most-recent active date.
    """
    return ScannerService.get_last_signal_date(before_date=trade_date)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_bear_watchlist(last_active_date: date, current_date: date) -> pd.DataFrame:
    """Cached bear-mode setup health for (last_active_date, current_date) pair.

    Both dates are cache-key components so the result refreshes automatically
    when either date changes — no manual invalidation needed.
    """
    return ScannerService.get_bear_mode_watchlist(last_active_date, current_date)


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_scanner_presets() -> dict[str, dict[str, int]]:
    """Cached scanner presets loaded from SQL (falls back to Python config).

    Returns:
        Dict mapping preset_name → {signal_name: weight}.
    """
    return ScannerService.get_scanner_presets()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_default_preset_name() -> str:
    """Cached default preset name from SQL config."""
    return ScannerService.get_default_preset_name()


@st.cache_data(ttl=DashboardConfig.CACHE_TTL_SECONDS)
def _load_search_options(trade_date: date) -> list[str]:
    """Return formatted 'Company Name  (SYMBOL)' strings for the search selectbox.

    Scoped to the selected trade_date so only symbols present in that day's
    pipeline output appear as options.  Formatting delegated to the shared
    ScannerService.format_symbol_option() to avoid duplication with other tabs.

    Args:
        trade_date: The date to resolve company names from stock_signals.

    Returns:
        List of canonical display strings, sorted by company name.
    """
    df = ScannerService.get_all_symbols_with_names(trade_date)
    if df.empty:
        return []
    return [
        ScannerService.format_symbol_option(row["company_name"], row["symbol"])
        for _, row in df.iterrows()
    ]


# ── Score recomputation ───────────────────────────────────────────────────────

def _apply_preset_weights(df: pd.DataFrame, weights: dict[str, int]) -> pd.DataFrame:
    """Recompute composite_score using preset weights applied to raw signal columns.

    Preset weights override the pipeline's stored composite_score so the user
    can rank the same signal data under different strategy lenses without
    touching the DB or re-running the pipeline.

    Args:
        df: Scanner DataFrame with raw signal columns.
        weights: Dict mapping signal column name → weight (any positive int).
                 Columns not present in df are silently skipped.

    Returns:
        df with composite_score and composite_rank replaced in-place.
    """
    available = {sig: w for sig, w in weights.items() if sig in df.columns and w > 0}
    if not available:
        return df

    df = df.copy()
    total_weight = sum(available.values())

    score = sum(
        pd.to_numeric(df[sig], errors="coerce").fillna(0).clip(0, 1) * w
        for sig, w in available.items()
    )

    df["composite_score"]  = (score / total_weight * 100).round(1)
    df["composite_rank"]   = (
        df["composite_score"].rank(ascending=False, method="dense").astype(int)
    )
    df["institutional_candidate"] = np.where(df["composite_score"] >= 70, 1, 0)

    # Sort by EV% first (highest expected value = best trade), then composite score
    if "ev_score" in df.columns:
        return df.sort_values(
            ["ev_score", "composite_score"],
            ascending=[False, False],
            na_position="last",
        )
    return df.sort_values("composite_score", ascending=False)


# ── Signal / score styling ────────────────────────────────────────────────────

_SIGNAL_COLUMNS = [
    "trend_signal",
    "stage2_signal",
    "vcp_signal",
    "breakout_signal",
    "breakout_ready_signal",
    "liquidity_signal",
    "quality_signal",
    "rs_signal",
    "pa_signal",       # price action — populated by run_pa_pipeline.py
]

_DISPLAY_NAMES: dict[str, str] = {
    "symbol":                      "Symbol",
    "company_name":                "Company",
    "sector":                      "Sector",
    "composite_score":             "Score",
    "institutional_candidate":     "IC",
    "trend_signal":                "Trend",
    "stage2_signal":               "Stage2",
    "stage2_days":                 "S2 Age(d)",
    "stage2_started_date":         "S2 Since",
    "vcp_signal":                  "VCP",
    "breakout_signal":             "Breakout",
    "breakout_ready_signal":       "BO Ready",
    "liquidity_signal":            "Liquid",
    "quality_signal":              "Quality",
    "rs_signal":                   "RS",
    "distance_from_pivot_pct":     "Dist Pivot%",
    "distance_from_52w_high_pct":  "Dist 52w%",
    "pivot_price":                 "Pivot ₹",
    "base_range_pct":              "Base %",
    "target_3_price":              "T3 ₹",
    "target_3_pct":                "T3 %",
    "confidence_t3":               "T3 Conf%",
    "target_1_price":              "T1 ₹",
    "target_1_pct":                "T1 %",
    "confidence_t1":               "T1 Conf%",
    "target_2_price":              "T2 ₹",
    "target_2_pct":                "T2 %",
    "confidence_t2":               "T2 Conf%",
    "risk_reward_t2":              "R:R",
    "upside_prob_pct":             "Est Prob%",
    "ev_score":                    "EV%",
    "pa_signal":                   "PA",
    "pa_daily_trend":              "PA Daily",
    "pa_weekly_trend":             "PA Weekly",
    "pa_score":                    "PA Score",
    "fund_quality_score":          "Fund Score",
    "fund_promoter_pct":           "Promoter%",
    "fund_roe":                    "ROE%",
    "fund_roce":                   "ROCE%",
}


def _style_signals(val) -> str:
    """Dark-theme signal cell: green highlight for 1, muted red for 0."""
    try:
        if int(val) == 1:
            return f"background-color: {GREEN}18; color: {GREEN}; font-weight: 600"
        return f"color: {RED}; opacity: 0.6"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_score(val) -> str:
    """Colour composite_score: green ≥ 70, orange ≥ 50, red otherwise."""
    try:
        v = float(val)
        if v >= 70:
            return f"color: {GREEN}; font-weight: 700"
        if v >= 50:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}"
    except (TypeError, ValueError):
        return ""


def _style_fund_score(val) -> str:
    """Colour fundamental quality score using config-driven thresholds.

    Green  ≥ QUALITY_PASS_SCORE (from FundamentalsConfig — SQL config table source of truth).
    Orange ≥ QUALITY_PASS_SCORE - 2.
    Muted  below that.
    """
    try:
        v = float(val)
        pass_score = FundamentalsConfig.QUALITY_PASS_SCORE
        if v >= pass_score:
            return f"color: {GREEN}; font-weight: 700"
        if v >= pass_score - 2:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {TEXT_MUTED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_stage2_age(val) -> str:
    """Colour Stage 2 age (days) using StrategyConfig thresholds.

    Green  ≤ STAGE2_EARLY_MAX_DAYS  — fresh entry, most opportunity.
    Orange ≤ STAGE2_MID_MAX_DAYS    — mid-run, still valid.
    Red    > STAGE2_MID_MAX_DAYS    — advanced run, near potential Stage 3 top.
    Muted  0 or NULL                — not in Stage 2.
    """
    try:
        v = int(val)
        if v <= 0:
            return f"color: {TEXT_MUTED}"
        if v <= StrategyConfig.STAGE2_EARLY_MAX_DAYS:
            return f"color: {GREEN}; font-weight: 700"
        if v <= StrategyConfig.STAGE2_MID_MAX_DAYS:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}; font-weight: 600"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_target_pct(val) -> str:
    """Color T1/T2 % upside: green ≥ 10%, orange ≥ 5%, red if negative."""
    try:
        v = float(val)
        if v >= 10.0:
            return f"color: {GREEN}; font-weight: 600"
        if v >= 5.0:
            return f"color: {ORANGE}; font-weight: 600"
        if v < 0:
            return f"color: {RED}"
        return f"color: {TEXT_MUTED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_risk_reward(val) -> str:
    """Color R:R ratio: green ≥ 2.0, orange ≥ 1.5, red < 1.5."""
    try:
        v = float(val)
        if v >= 2.0:
            return f"color: {GREEN}; font-weight: 700"
        if v >= 1.5:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_prob(val) -> str:
    """Color estimated probability: green ≥ 55%, orange ≥ 45%, red < 45%."""
    try:
        v = float(val)
        if v >= 55.0:
            return f"color: {GREEN}; font-weight: 700"
        if v >= 45.0:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_ev(val) -> str:
    """Color EV%: green ≥ 5%, orange ≥ 0%, red < 0% (negative expected value = avoid)."""
    try:
        v = float(val)
        if v >= 5.0:
            return f"color: {GREEN}; font-weight: 700"
        if v >= 0.0:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


def _style_confidence(val) -> str:
    """Color confidence %: green ≥ 80%, orange ≥ 55%, red < 55%."""
    try:
        v = float(val)
        if v >= 80.0:
            return f"color: {GREEN}; font-weight: 700"
        if v >= 55.0:
            return f"color: {ORANGE}; font-weight: 600"
        return f"color: {RED}"
    except (TypeError, ValueError):
        return f"color: {TEXT_MUTED}"


# ── Regime badge ─────────────────────────────────────────────────────────────

_REGIME_COLORS: dict[str, str] = {
    "STRONG_BULL": GREEN,
    "BULL":        GREEN,
    "CORRECTION":  ORANGE,
    "NEUTRAL":     ORANGE,
    "DEFENSIVE":   ORANGE,
    "BEAR":        RED,
}


def _regime_badge(market_state: str) -> str:
    """Return a coloured HTML badge chip for the given market state.

    Returns empty string when market_state is blank (no data for that date)
    so the header renders cleanly without a broken badge.

    Args:
        market_state: State code from market_environment (e.g. 'BULL', 'BEAR').

    Returns:
        HTML span string, or '' if market_state is empty.
    """
    if not market_state:
        return ""
    color = _REGIME_COLORS.get(market_state.upper(), TEXT_MUTED)
    return (
        f"<span style='background:{color}20;color:{color};"
        f"border:1px solid {color}40;border-radius:5px;"
        f"padding:2px 10px;font-size:11px;font-weight:600;"
        f"font-family:Inter,sans-serif;margin-left:10px'>{market_state}</span>"
    )


# ── Bear-mode setup health panel ──────────────────────────────────────────────

_WATCHLIST_COL_NAMES: dict[str, str] = {
    "symbol":                "Symbol",
    "company_name":          "Company",
    "sector":                "Sector",
    "composite_score":       "Score",
    "stage2_days":           "S2 Age",
    "dist_pivot_signal_day": "Entry Dist%",
    "current_dist_pivot_pct":"Now Dist%",
    "pivot_price":           "Pivot ₹",
    "target_1_price":        "T1 ₹",
    "target_2_price":        "T2 ₹",
    "ev_score":              "EV%",
}


def _render_bear_watchlist(df: pd.DataFrame, signal_date: date) -> None:
    """Render the bear-mode setup health panel below the bear mode banner.

    Splits results into two groups:
      - Holding  (is_holding=1): above EMA50 and within stop buffer of pivot.
      - Breaking (is_holding=0): broke EMA50 or dropped past the stop level.

    Args:
        df: DataFrame from ScannerService.get_bear_mode_watchlist().
        signal_date: The last active signal date (used in the section label).
    """
    if df.empty:
        st.caption(f"No qualifying setups found from {signal_date}.")
        return

    holding  = df[df["is_holding"] == 1].copy()
    breaking = df[df["is_holding"] == 0].copy()

    st.markdown(
        f"**Setup Health** — {len(df)} stocks were valid on **{signal_date}** "
        f"· **{len(holding)}** holding · **{len(breaking)}** breaking down",
    )

    display_cols = [c for c in _WATCHLIST_COL_NAMES if c in df.columns]
    fmt_map = {
        "Score":       "{:.1f}",
        "S2 Age":      "{:.0f}",
        "Entry Dist%": "{:.1f}%",
        "Now Dist%":   "{:.1f}%",
        "Pivot ₹":     "{:.1f}",
        "T1 ₹":        "{:.1f}",
        "T2 ₹":        "{:.1f}",
        "EV%":         "{:.1f}%",
    }

    # "large"/"medium" string widths (same pattern as backtest tab) guarantee
    # total > container width → real draggable horizontal thumb appears.
    _wl_col_cfg = {
        "#":           st.column_config.NumberColumn(format="%d",     width="small"),
        "Symbol":      st.column_config.TextColumn(width="medium"),
        "Company":     st.column_config.TextColumn(width="large"),
        "Sector":      st.column_config.TextColumn(width="medium"),
        "Score":       st.column_config.NumberColumn(format="%.1f",   width="small"),
        "S2 Age":      st.column_config.NumberColumn(format="%d",     width="small"),
        "Entry Dist%": st.column_config.NumberColumn(format="%.1f%%", width="medium"),
        "Now Dist%":   st.column_config.NumberColumn(format="%.1f%%", width="medium"),
        "Pivot ₹":     st.column_config.NumberColumn(format="%.1f",   width="medium"),
        "T1 ₹":        st.column_config.NumberColumn(format="%.1f",   width="medium"),
        "T2 ₹":        st.column_config.NumberColumn(format="%.1f",   width="medium"),
        "EV%":         st.column_config.NumberColumn(format="%.1f%%", width="small"),
    }

    for label, subset, color in (
        ("Holding — potential recovery candidates", holding,  GREEN),
        ("Breaking down — avoid",                  breaking, RED),
    ):
        if subset.empty:
            continue
        st.markdown(
            f"<span style='color:{color};font-weight:600'>{label} "
            f"({len(subset)})</span>",
            unsafe_allow_html=True,
        )
        sub = subset[display_cols].rename(columns=_WATCHLIST_COL_NAMES).copy()

        # Sort by EV% descending so highest expected-value setups are row #1
        if "ev_score" in subset.columns:
            sub = sub.sort_values("EV%", ascending=False, na_position="last")
        sub = sub.reset_index(drop=True)
        sub.insert(0, "#", range(1, len(sub) + 1))

        styled = sub.style.format(fmt_map, na_rep="—")
        st.dataframe(
            styled,
            use_container_width=True,
            hide_index=True,
            height=min(400, 38 + len(sub) * 35),
            column_config=_wl_col_cfg,
        )


# ── Main render ───────────────────────────────────────────────────────────────

def render_scanner_table() -> None:
    """Render the Scanner tab with sidebar filters, preset selector, and results table.

    Sidebar controls:
      - Trade date selector
      - NSE index filter
      - Scanner preset (signal-weight strategy)
      - Min composite score slider
      - Signal must-have checkboxes
    """
    min_date, max_date = _load_date_range()
    if min_date is None:
        st.warning("No signal data found. Run the pipeline first.")
        return

    indices      = _load_index_list()
    index_options = {"All indices": ""} | {
        f"{i['index_name']} ({i['index_code']})": i["index_code"]
        for i in indices
    }

    # Presets from SQL (with Python fallback) — no code change needed to add new presets
    scanner_presets  = _load_scanner_presets()
    preset_names     = list(scanner_presets.keys())
    default_preset   = _load_default_preset_name()
    default_preset_idx = preset_names.index(default_preset) if default_preset in preset_names else 0

    # Default to last date with active directional signals, not the latest (may be BEAR).
    last_active  = _load_last_signal_date()
    default_date = last_active if last_active else max_date

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Scanner Filters")

        raw_date = st.date_input(
            "Trade date",
            value=default_date,
            min_value=min_date,
            max_value=max_date,
            key="scanner_date",
            help=(
                f"5 years of history ({min_date} to {max_date}). "
                "Weekends and holidays auto-snap to the nearest prior trading day. "
                "Scroll the calendar to change months."
            ),
        )
        # Snap weekends / holidays → nearest prior trading day with signal data
        snapped_date = ScannerService.get_nearest_trade_date(raw_date)
        selected_date = snapped_date if snapped_date else default_date

        # Always visible data-range label — works regardless of calendar CSS/JS
        st.caption(
            f"Data: {min_date.strftime('%d %b %Y')} to {max_date.strftime('%d %b %Y')}  "
            f"({(max_date - min_date).days // 365}y history) — "
            "greyed dates have no data"
        )

        selected_index_label = st.selectbox(
            "NSE Index",
            options=list(index_options.keys()),
            key="scanner_index",
        )
        selected_index_code = index_options[selected_index_label]

        selected_preset = st.selectbox(
            "Scanner strategy",
            options=preset_names,
            index=default_preset_idx,
            key="scanner_preset",
            help=(
                "Choose a signal-weight strategy. Score is recomputed from raw signals "
                "using the preset's weights — no pipeline re-run needed."
            ),
        )

        min_score = st.slider(
            "Min score (post-preset)",
            min_value=0,
            max_value=100,
            value=int(DashboardConfig.SCANNER_DEFAULT_MIN_SCORE),
            step=5,
            key="scanner_min_score",
        )

        # Inject calendar JS inside the sidebar so the hidden <img> renders there.
        # Must be inside `with st.sidebar` — if called in the main area the img
        # HTML appears as visible text even with display:none.
        _inject_calendar_js(min_date)

    # ── Search bar ────────────────────────────────────────────────────────────
    search_options = _load_search_options(selected_date)
    sc1, sc2 = st.columns([3, 1])
    with sc1:
        selected_search_option = st.selectbox(
            "Search company or ticker",
            options=search_options,
            index=None,
            placeholder="e.g. Tata Motors, HDFC, RELIANCE...",
            key="scanner_search_select",
        )
    with sc2:
        st.markdown("<div style='padding-top:28px'></div>", unsafe_allow_html=True)
        if selected_search_option and st.button("Clear search", key="scanner_search_clear"):
            st.session_state.pop("scanner_search_select", None)
            st.rerun()

    selected_search_symbol: Optional[str] = None
    if selected_search_option:
        selected_search_symbol = ScannerService.parse_symbol_from_option(selected_search_option)
        st.session_state["detail_symbol"] = selected_search_symbol

    # ── Date-snap notice (shown in main area so user can see it) ─────────────
    # Tells the user exactly why the date changed — not a silent redirect.
    if snapped_date is None:
        # raw_date is before the earliest pipeline data
        st.warning(
            f"**{raw_date.strftime('%a, %d %b %Y')}** is before the earliest pipeline data "
            f"({min_date}). Showing the last active trading day instead: "
            f"**{default_date.strftime('%a, %d %b %Y')}**."
        )
    elif raw_date != selected_date:
        # Weekend or market holiday — snapped to nearest prior trading day
        st.info(
            f"**{raw_date.strftime('%a, %d %b %Y')}** is not a trading day — "
            f"showing nearest trading day: **{selected_date.strftime('%a, %d %b %Y')}**."
        )

    # ── Regime for selected date (badge shown next to header) ────────────────
    env          = _load_environment_for_date(selected_date)
    market_state = env.get("market_state", "")

    # ── Load full universe for this date ─────────────────────────────────────
    df_raw = _load_scanner(selected_date, selected_index_code)

    if df_raw.empty:
        st.warning(
            f"No signal data found for **{selected_date.strftime('%a, %d %b %Y')}**. "
            "The pipeline may not have processed this date yet. "
            f"Try a nearby date or run the pipeline to regenerate signals."
        )
        return

    # ── Bear / cash-mode banner + setup health watchlist ─────────────────────
    _directional_cols = ("trend_signal", "stage2_signal", "vcp_signal", "breakout_signal")
    bear_mode = all(
        df_raw[col].sum() == 0
        for col in _directional_cols
        if col in df_raw.columns
    )

    if bear_mode:
        last_active = _load_last_signal_date()
        suggestion = (
            f"  \nSwitch to **{last_active}** (last date with active signals) "
            "using the date picker in the sidebar."
            if last_active and last_active != selected_date
            else ""
        )
        st.warning(
            f"**BEAR / cash mode — {selected_date}**: all directional signals are suppressed "
            f"by the regime gate (trend, stage2, VCP, breakout = 0 for all {len(df_raw)} stocks). "
            f"Liquidity, Quality and RS signals are still active.{suggestion}"
        )
        # Use date-relative lookup so historical bear periods get the correct
        # signal snapshot (not the global most-recent active date).
        last_active_before = _load_last_signal_date_before(selected_date)
        if last_active_before and last_active_before != selected_date:
            wl = _load_bear_watchlist(last_active_before, selected_date)
            _render_bear_watchlist(wl, last_active_before)

    # ── Apply preset scoring to full universe, then clip to display TOP N ────
    preset_weights    = scanner_presets.get(selected_preset, {})
    is_default_preset = selected_preset == default_preset

    if is_default_preset:
        df = df_raw.copy()
    else:
        df = _apply_preset_weights(df_raw, preset_weights)

    # Apply min_score filter AFTER preset recomputation, then cap display rows
    df = df[df["composite_score"] >= min_score]
    df = df.head(DashboardConfig.SCANNER_TOP_N)

    # ── Symbol search filter ──────────────────────────────────────────────────
    if selected_search_symbol:
        df_search = df_raw[df_raw["symbol"] == selected_search_symbol]
        if df_search.empty:
            st.info(
                f"{selected_search_option} — not found for this date. "
                "It may not have been processed by the pipeline on this date."
            )
            return
        df = df_search

    # ── Header metrics ────────────────────────────────────────────────────────
    preset_badge = (
        f"<span style='background:{BLUE}20;color:{BLUE};border:1px solid {BLUE}40;"
        f"border-radius:5px;padding:2px 10px;font-size:11px;font-weight:600;"
        f"font-family:Inter,sans-serif;margin-left:10px'>{selected_preset}</span>"
        if not is_default_preset else ""
    )
    st.markdown(
        section_title(f"Scanner — {selected_date}")
        + _regime_badge(market_state)
        + preset_badge,
        unsafe_allow_html=True,
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Stocks shown",  len(df))
    m2.metric("IC candidates", int(df["institutional_candidate"].sum()) if "institutional_candidate" in df.columns else "—")
    m3.metric("VCP setups",    int(df["vcp_signal"].sum())      if "vcp_signal"      in df.columns else "—")
    m4.metric("Breakouts",     int(df["breakout_signal"].sum()) if "breakout_signal" in df.columns else "—")
    m5.metric("BO Ready",      int(df["breakout_ready_signal"].sum()) if "breakout_ready_signal" in df.columns else "—")

    if bear_mode:
        st.caption(
            "BEAR mode: trend, Stage2, VCP and breakout are all 0. "
            "Scores below reflect Liquidity + Quality only (max ~30). "
            "Use the Setup Health watchlist above for actionable setups."
        )

    # Show active preset weights when non-default preset is selected
    if not is_default_preset:
        active_w = {k: v for k, v in preset_weights.items() if v > 0}
        weights_str = "  ·  ".join(
            f"**{k.replace('_signal','')}** {v}pt" for k, v in active_w.items()
        )
        st.caption(f"Preset weights: {weights_str}")

    # ── Build display DataFrame ───────────────────────────────────────────────
    # Core columns always shown (stage2_started_date excluded — too wide for table view)
    # fundamental columns appended when data exists
    _EXCLUDED_FROM_TABLE = {"stage2_started_date"}
    core_cols = [
        c for c in _DISPLAY_NAMES
        if c in df.columns and not c.startswith("fund_") and c not in _EXCLUDED_FROM_TABLE
    ]
    fund_cols = [c for c in ("fund_quality_score", "fund_promoter_pct", "fund_roe", "fund_roce")
                 if c in df.columns and df[c].notna().any()]

    display_cols = core_cols + fund_cols
    display = df[display_cols].copy()
    display.rename(columns=_DISPLAY_NAMES, inplace=True)

    # ── Smart Filter bar ──────────────────────────────────────────────────────
    # Applied to the display DataFrame (after column rename) so filter labels
    # match exactly what the user sees in the table.
    display = _scanner_filters.render_filter_bar(display)

    if display.empty:
        if bear_mode:
            st.info(
                "No stocks match the active filters on this BEAR date. "
                "Trend / Stage2 / VCP / Breakout are all 0 — "
                "clear signal filters or switch to a date with active signals."
            )
        else:
            st.info("No stocks match the active filters. Adjust or clear filters above.")
        return

    st.divider()

    # ── Table ─────────────────────────────────────────────────────────────────
    display.insert(0, "#", range(1, len(display) + 1))

    sig_display_names = [_DISPLAY_NAMES[c] for c in _SIGNAL_COLUMNS if c in df.columns]
    score_col  = _DISPLAY_NAMES.get("composite_score", "Score")
    fscore_col = _DISPLAY_NAMES.get("fund_quality_score", "Fund Score")

    fmt: dict = {
        score_col:                                       "{:.1f}",
        _DISPLAY_NAMES.get("distance_from_pivot_pct",  "Dist Pivot%"): "{:.1f}%",
        _DISPLAY_NAMES.get("distance_from_52w_high_pct","Dist 52w%"):  "{:.1f}%",
        _DISPLAY_NAMES.get("pivot_price",    "Pivot ₹"):  "{:.1f}",
        _DISPLAY_NAMES.get("base_range_pct", "Base %"):  "{:.1f}%",
        _DISPLAY_NAMES.get("target_3_price", "T3 ₹"):    "{:.1f}",
        _DISPLAY_NAMES.get("target_3_pct",   "T3 %"):    "{:.1f}%",
        _DISPLAY_NAMES.get("confidence_t3",  "T3 Conf%"): "{:.0f}%",
        _DISPLAY_NAMES.get("target_1_price", "T1 ₹"):    "{:.1f}",
        _DISPLAY_NAMES.get("target_1_pct",   "T1 %"):    "{:.1f}%",
        _DISPLAY_NAMES.get("confidence_t1",  "T1 Conf%"): "{:.0f}%",
        _DISPLAY_NAMES.get("target_2_price", "T2 ₹"):    "{:.1f}",
        _DISPLAY_NAMES.get("target_2_pct",   "T2 %"):    "{:.1f}%",
        _DISPLAY_NAMES.get("confidence_t2",  "T2 Conf%"): "{:.0f}%",
        _DISPLAY_NAMES.get("risk_reward_t2","R:R"):      "{:.2f}",
        _DISPLAY_NAMES.get("upside_prob_pct","Est Prob%"): "{:.1f}%",
        _DISPLAY_NAMES.get("ev_score",       "EV%"):       "{:.1f}%",
    }
    if "fund_promoter_pct" in df.columns:
        fmt[_DISPLAY_NAMES["fund_promoter_pct"]] = "{:.1f}%"
    if "fund_roe" in df.columns:
        fmt[_DISPLAY_NAMES["fund_roe"]]  = "{:.1f}%"
    if "fund_roce" in df.columns:
        fmt[_DISPLAY_NAMES["fund_roce"]] = "{:.1f}%"

    s2age_col = _DISPLAY_NAMES.get("stage2_days",    "S2 Age(d)")
    t1_col    = _DISPLAY_NAMES.get("target_1_pct",   "T1 %")
    t2_col    = _DISPLAY_NAMES.get("target_2_pct",   "T2 %")
    t3_col    = _DISPLAY_NAMES.get("target_3_pct",   "T3 %")
    c1_col    = _DISPLAY_NAMES.get("confidence_t1",  "T1 Conf%")
    c2_col    = _DISPLAY_NAMES.get("confidence_t2",  "T2 Conf%")
    c3_col    = _DISPLAY_NAMES.get("confidence_t3",  "T3 Conf%")
    rr_col    = _DISPLAY_NAMES.get("risk_reward_t2", "R:R")
    prob_col  = _DISPLAY_NAMES.get("upside_prob_pct","Est Prob%")
    ev_col    = _DISPLAY_NAMES.get("ev_score",       "EV%")

    styled = display.style.map(_style_signals, subset=sig_display_names)
    if score_col in display.columns:
        styled = styled.map(_style_score, subset=[score_col])
    if s2age_col in display.columns:
        styled = styled.map(_style_stage2_age, subset=[s2age_col])
    if fscore_col in display.columns:
        styled = styled.map(_style_fund_score, subset=[fscore_col])
    for col in (t1_col, t2_col, t3_col):
        if col in display.columns:
            styled = styled.map(_style_target_pct, subset=[col])
    for col in (c1_col, c2_col, c3_col):
        if col in display.columns:
            styled = styled.map(_style_confidence, subset=[col])
    if rr_col in display.columns:
        styled = styled.map(_style_risk_reward, subset=[rr_col])
    if prob_col in display.columns:
        styled = styled.map(_style_prob, subset=[prob_col])
    if ev_col in display.columns:
        styled = styled.map(_style_ev, subset=[ev_col])
    styled = styled.format(fmt, na_rep="—")

    # Fixed widths force overflow → draggable horizontal scrollbar.
    _col_cfg: dict = {
        "#":           st.column_config.NumberColumn(format="%d",      width=42),
        "Symbol":      st.column_config.TextColumn(width=115),
        "Company":     st.column_config.TextColumn(width=175),
        "Sector":      st.column_config.TextColumn(width=140),
        "Score":       st.column_config.NumberColumn(format="%.1f",    width=62),
        "S2 Age(d)":   st.column_config.NumberColumn(format="%d",      width=72),
        "Dist Pivot%": st.column_config.NumberColumn(format="%.1f%%",  width=92),
        "Dist 52w%":   st.column_config.NumberColumn(format="%.1f%%",  width=85),
        "Pivot ₹":     st.column_config.NumberColumn(format="%.1f",    width=78),
        "Base %":      st.column_config.NumberColumn(format="%.1f%%",  width=68),
        "T3 ₹":        st.column_config.NumberColumn(format="%.1f",    width=75),
        "T3 %":        st.column_config.NumberColumn(format="%.1f%%",  width=60),
        "T3 Conf%":    st.column_config.NumberColumn(format="%.0f%%",  width=78),
        "T1 ₹":        st.column_config.NumberColumn(format="%.1f",    width=78),
        "T1 %":        st.column_config.NumberColumn(format="%.1f%%",  width=62),
        "T1 Conf%":    st.column_config.NumberColumn(format="%.0f%%",  width=78),
        "T2 ₹":        st.column_config.NumberColumn(format="%.1f",    width=78),
        "T2 %":        st.column_config.NumberColumn(format="%.1f%%",  width=62),
        "T2 Conf%":    st.column_config.NumberColumn(format="%.0f%%",  width=78),
        "R:R":         st.column_config.NumberColumn(format="%.2f",    width=58),
        "Est Prob%":   st.column_config.NumberColumn(format="%.1f%%",  width=82),
        "EV%":         st.column_config.NumberColumn(format="%.1f%%",  width=68),
        "Fund Score":  st.column_config.NumberColumn(format="%.1f",    width=82),
        "Promoter%":   st.column_config.NumberColumn(format="%.1f%%",  width=88),
        "ROE%":        st.column_config.NumberColumn(format="%.1f%%",  width=65),
        "ROCE%":       st.column_config.NumberColumn(format="%.1f%%",  width=68),
    }
    st.dataframe(styled, use_container_width=True, hide_index=True, height=600, column_config=_col_cfg)

    # ── Export ────────────────────────────────────────────────────────────────
    csv = display.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"Download CSV ({selected_preset})",
        data=csv,
        file_name=f"scanner_{selected_date}_{selected_preset.lower().replace(' ', '_')}.csv",
        mime="text/csv",
    )
