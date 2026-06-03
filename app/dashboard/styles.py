"""Dashboard design system — CSS injection and HTML component helpers.

Single source of truth for colours, typography, and reusable card HTML.

IMPORTANT: All HTML builder functions must return single-line strings with NO
internal newlines. Streamlit's markdown parser treats indented lines as code
blocks, which splits multi-line HTML and renders stray </div> tags as text.
"""

import streamlit as st

# ── Colour tokens ─────────────────────────────────────────────────────────────

BG_APP      = "#070b14"
BG_SURFACE  = "#0d1220"
BG_CARD     = "#101623"
BG_ELEVATED = "#18203a"
BG_INPUT    = "#1a2235"

BORDER     = "rgba(255,255,255,0.08)"
BORDER_MED = "rgba(255,255,255,0.14)"

TEXT_PRIMARY   = "#e6edf3"
TEXT_SECONDARY = "#8b949e"
TEXT_MUTED     = "#4a5568"

GREEN  = "#3fb950"
RED    = "#f85149"
ORANGE = "#d29922"
BLUE   = "#58a6ff"
PURPLE = "#bc8cff"

STATE_ACCENT: dict[str, str] = {
    "STRONG_BULL": GREEN,
    "BULL":        GREEN,
    "NEUTRAL":     BLUE,
    "DEFENSIVE":   ORANGE,
    "CORRECTION":  ORANGE,
    "BEAR":        RED,
}

RUN_STATUS_COLOUR: dict[str, str] = {
    "SUCCESS": GREEN,
    "RUNNING": BLUE,
    "FAILED":  RED,
}

# ── Global CSS ────────────────────────────────────────────────────────────────

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,300..700;1,14..32,300..700&display=swap');

html, body, * {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }}

[data-testid="stAppViewContainer"] > .main {{
    background: {BG_APP} !important;
}}
[data-testid="stAppViewContainer"] > .main > .block-container {{
    padding: 2rem 2.5rem 4rem !important;
    max-width: 1400px;
}}

header[data-testid="stHeader"], footer, #MainMenu,
[data-testid="stToolbar"], [data-testid="stDecoration"] {{
    display: none !important;
}}

/* ── Sidebar ── */
[data-testid="stSidebar"] {{
    background: {BG_SURFACE} !important;
    border-right: 1px solid {BORDER} !important;
}}
[data-testid="stSidebarContent"] {{ padding: 1.5rem 1.2rem !important; }}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label {{ color: {TEXT_SECONDARY} !important; font-size: 13px !important; }}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {{
    color: {TEXT_PRIMARY} !important; font-size: 13px !important;
    font-weight: 600 !important; text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
}}

/* ── Global text ── */
p, span {{ color: {TEXT_SECONDARY}; }}
h1 {{
    color: {TEXT_PRIMARY} !important; font-size: 1.65rem !important;
    font-weight: 700 !important; letter-spacing: -0.03em !important;
    line-height: 1.2 !important;
}}
h2, h3 {{ color: {TEXT_PRIMARY} !important; font-weight: 600 !important; }}
[data-testid="stCaptionContainer"] p {{
    color: {TEXT_MUTED} !important; font-size: 12px !important;
}}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {{
    background: transparent !important;
    border-bottom: 1px solid {BORDER} !important;
    gap: 0 !important; padding: 0 !important;
    margin-bottom: 2rem !important;
}}
.stTabs [data-baseweb="tab"] {{
    background: transparent !important;
    color: {TEXT_MUTED} !important;
    font-size: 13px !important; font-weight: 500 !important;
    padding: 10px 22px !important;
    border: none !important; border-radius: 0 !important;
    letter-spacing: 0.01em !important;
    transition: color 0.15s ease !important;
}}
.stTabs [data-baseweb="tab"]:hover {{ color: {TEXT_SECONDARY} !important; }}
.stTabs [aria-selected="true"] {{ color: {TEXT_PRIMARY} !important; font-weight: 600 !important; }}
.stTabs [data-baseweb="tab-highlight"] {{
    background-color: {BLUE} !important; height: 2px !important;
}}

/* ── st.metric ── */
[data-testid="metric-container"] {{
    background: linear-gradient(145deg, {BG_ELEVATED} 0%, {BG_CARD} 100%) !important;
    border: 1px solid {BORDER_MED} !important;
    border-radius: 12px !important;
    padding: 20px 22px !important;
}}
[data-testid="stMetricLabel"] p {{
    color: {TEXT_MUTED} !important; font-size: 10px !important;
    font-weight: 600 !important; text-transform: uppercase !important;
    letter-spacing: 0.09em !important;
}}
[data-testid="stMetricValue"] {{
    color: {TEXT_PRIMARY} !important;
    font-size: 24px !important; font-weight: 700 !important;
}}

/* ── Divider ── */
hr {{ border: none !important; border-top: 1px solid {BORDER} !important; margin: 1.8rem 0 !important; }}

/* ── Alerts ── */
[data-testid="stAlert"] {{ border-radius: 8px !important; font-size: 13px !important; }}

/* ── Selectbox trigger ── */
[data-baseweb="select"] > div:first-child {{
    background: {BG_INPUT} !important; border: 1px solid {BORDER_MED} !important;
    border-radius: 8px !important; color: {TEXT_PRIMARY} !important;
    font-size: 13px !important; font-family: Inter, sans-serif !important;
}}

/* ── Combobox (searchable selectbox) input field — typed text ── */
/* `color` alone doesn't win for inputs; need -webkit-text-fill-color + opacity */
[data-baseweb="combobox"] input,
[data-baseweb="combobox"] input:focus,
[data-baseweb="combobox"] input:not([value=""]),
[data-baseweb="select"] input,
[data-baseweb="select"] input:focus {{
    color: {TEXT_PRIMARY} !important;
    -webkit-text-fill-color: {TEXT_PRIMARY} !important;
    opacity: 1 !important;
    background: transparent !important;
    font-size: 13px !important;
    font-family: Inter, sans-serif !important;
    caret-color: {BLUE} !important;
}}

/* Placeholder text — intentionally dimmer than typed text */
[data-baseweb="combobox"] input::placeholder,
[data-baseweb="select"] input::placeholder {{
    color: {TEXT_MUTED} !important;
    -webkit-text-fill-color: {TEXT_MUTED} !important;
    opacity: 1 !important;
}}

/* ── Dropdown popover — outer shell (border-radius clip stays, overflow hidden) ── */
[data-baseweb="popover"],
[data-baseweb="popover"] > div,
[data-baseweb="popover"] > div > div {{
    background: #1c2540 !important;
    border: 1px solid {BORDER_MED} !important;
    border-radius: 10px !important;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6) !important;
    overflow: hidden !important;
}}

/* ── Dropdown inner list — scrollable with thin accent scrollbar ─────────────
   Separate from the outer popover so border-radius still clips,
   while the list itself can scroll vertically (month picker Jan→Dec, etc.)
────────────────────────────────────────────────────────────────────────────── */
[data-baseweb="menu"],
[data-baseweb="menu"] > ul,
[data-baseweb="popover"] ul {{
    background:  #1c2540 !important;
    max-height:  300px !important;
    overflow-y:  auto !important;
    overflow-x:  hidden !important;
    scrollbar-width: thin !important;
    scrollbar-color: {BLUE} #18203a !important;
}}
/* Webkit scrollbar — thin blue thumb matching the design system */
[data-baseweb="menu"]::-webkit-scrollbar,
[data-baseweb="popover"] ul::-webkit-scrollbar {{
    width: 4px !important;
}}
[data-baseweb="menu"]::-webkit-scrollbar-track,
[data-baseweb="popover"] ul::-webkit-scrollbar-track {{
    background:    #18203a !important;
    border-radius: 2px !important;
}}
[data-baseweb="menu"]::-webkit-scrollbar-thumb,
[data-baseweb="popover"] ul::-webkit-scrollbar-thumb {{
    background:    {BLUE} !important;
    border-radius: 2px !important;
}}
[data-baseweb="menu"]::-webkit-scrollbar-thumb:hover,
[data-baseweb="popover"] ul::-webkit-scrollbar-thumb:hover {{
    background:    #79b8ff !important;
}}

/* ── Option rows — default state ── */
/* Targets both regular selectbox options and combobox search result rows */
[data-baseweb="option"],
[role="option"],
[data-baseweb="popover"] [role="option"],
[data-baseweb="menu"] [role="option"],
[data-baseweb="menu"] li {{
    background: transparent !important;
    color: {TEXT_PRIMARY} !important;
    font-size: 13px !important;
    font-family: Inter, sans-serif !important;
    border-radius: 6px !important;
    margin: 2px 6px !important;
    padding: 9px 12px !important;
    opacity: 1 !important;
    transition: background 0.1s ease !important;
}}

/* Override the global `p, span {{ color: TEXT_SECONDARY }}` for all option children */
[data-baseweb="option"] span,
[data-baseweb="option"] div,
[data-baseweb="option"] p,
[role="option"] span,
[role="option"] div,
[role="option"] p,
[data-baseweb="menu"] li span,
[data-baseweb="menu"] li div {{
    color: {TEXT_PRIMARY} !important;
    opacity: 1 !important;
    background: transparent !important;
}}

/* ── Hover state ── */
[data-baseweb="option"]:hover,
[role="option"]:hover,
[data-baseweb="menu"] [role="option"]:hover,
[data-baseweb="menu"] li:hover {{
    background: rgba(88,166,255,0.12) !important;
    color: {TEXT_PRIMARY} !important;
}}

/* ── Active / keyboard-focused option ── */
[data-baseweb="option"][aria-selected="true"],
[role="option"][aria-selected="true"],
[data-baseweb="menu"] [role="option"][aria-selected="true"] {{
    background: rgba(88,166,255,0.18) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
}}

[data-baseweb="option"][aria-selected="true"] span,
[role="option"][aria-selected="true"] span,
[data-baseweb="option"][aria-selected="true"] div,
[role="option"][aria-selected="true"] div {{
    color: #ffffff !important;
    opacity: 1 !important;
}}

/* ── Slider / Checkbox ── */
[data-testid="stSlider"] label p {{ color: {TEXT_SECONDARY} !important; font-size: 13px !important; }}
[data-testid="stCheckbox"] p {{ color: {TEXT_SECONDARY} !important; font-size: 13px !important; }}

/* ── Download button ── */
[data-testid="stDownloadButton"] > button {{
    background: {BG_ELEVATED} !important; border: 1px solid {BORDER_MED} !important;
    color: {TEXT_PRIMARY} !important; border-radius: 8px !important;
    font-size: 12px !important; font-weight: 500 !important; padding: 6px 16px !important;
}}
[data-testid="stDownloadButton"] > button:hover {{
    border-color: rgba(255,255,255,0.25) !important; background: {BG_INPUT} !important;
}}

/* ── DataFrame ── */
[data-testid="stDataFrame"] {{ border-radius: 10px !important; border: 1px solid {BORDER} !important; overflow: visible !important; }}

/* ── Scrollbar (global) ── */
::-webkit-scrollbar {{ width: 5px; height: 5px; }}
::-webkit-scrollbar-track {{ background: {BG_APP}; }}
::-webkit-scrollbar-thumb {{ background: {BG_ELEVATED}; border-radius: 3px; }}
::-webkit-scrollbar-thumb:hover {{ background: rgba(255,255,255,0.15); }}

/* ── DataFrame scrollbars — thick blue, always visible ── */
/* Use * to match every element inside stDataFrame regardless of inline style presence */
[data-testid="stDataFrame"] *::-webkit-scrollbar {{ width: 14px !important; height: 14px !important; }}
[data-testid="stDataFrame"] *::-webkit-scrollbar-track {{ background: rgba(255,255,255,0.12) !important; border-radius: 7px !important; }}
[data-testid="stDataFrame"] *::-webkit-scrollbar-thumb {{ background: {BLUE} !important; border-radius: 7px !important; }}
/* Per-axis min-size: vertical uses min-height, horizontal uses min-width */
[data-testid="stDataFrame"] *::-webkit-scrollbar-thumb:vertical {{ min-height: 30px !important; }}
[data-testid="stDataFrame"] *::-webkit-scrollbar-thumb:horizontal {{ min-width: 30px !important; }}
[data-testid="stDataFrame"] *::-webkit-scrollbar-thumb:hover {{ background: #79b8ff !important; }}
[data-testid="stDataFrame"] *::-webkit-scrollbar-corner {{ background: rgba(255,255,255,0.06) !important; }}
/* Firefox */
[data-testid="stDataFrame"] * {{ scrollbar-color: {BLUE} rgba(255,255,255,0.12) !important; scrollbar-width: auto !important; }}
/* Force horizontal scroll on GDG's scroll container */
[data-testid="stDataFrame"] .dvn-scroller,
[data-testid="stDataFrame"] [style*="overflow-y: scroll"],
[data-testid="stDataFrame"] [style*="overflow-y:scroll"],
[data-testid="stDataFrame"] [style*="overflow: scroll"],
[data-testid="stDataFrame"] [style*="overflow:scroll"] {{ overflow-x: scroll !important; }}

/* ── Date Input — text field ── */
[data-testid="stDateInputField"] input,
[data-testid="stDateInput"] input {{
    background-color: {BG_INPUT} !important;
    color:            {TEXT_PRIMARY} !important;
    border:           1px solid {BORDER_MED} !important;
    border-radius:    8px !important;
    font-size:        13px !important;
    font-family:      Inter, sans-serif !important;
    padding:          8px 12px !important;
}}
[data-testid="stDateInputField"] input:focus,
[data-testid="stDateInput"] input:focus {{
    border-color: {BLUE} !important;
    box-shadow:   0 0 0 2px {BLUE}30 !important;
    outline:      none !important;
}}

/* ── Date Input — calendar popup ─────────────────────────────────────────
   Nuclear reset strategy:
     Layer 1 — calendar shell gets the dark background.
     Layer 2 — EVERY child gets background:transparent (shorthand + longhand)
               so white-box artifacts from empty leading/trailing cells vanish.
     Layer 3 — specific overrides for header, selected day, disabled, hover.
   The two-property reset (background + background-color) is required because
   BaseUI sets the shorthand on some elements and the longhand on others.
────────────────────────────────────────────────────────────────────────── */

/* L1 — dark shell */
[data-baseweb="calendar"] {{
    background:       {BG_CARD} !important;
    background-color: {BG_CARD} !important;
    border:           1px solid {BORDER_MED} !important;
    border-radius:    14px !important;
    box-shadow:       0 20px 60px rgba(0,0,0,0.85) !important;
    overflow:         hidden !important;
    padding:          0 !important;
}}

/* L2 — nuclear transparent reset (kills white-box artifacts) */
[data-baseweb="calendar"] *,
[data-baseweb="datepicker"] * {{
    background:       transparent !important;
    background-color: transparent !important;
    color:            {TEXT_PRIMARY} !important;
    font-family:      Inter, sans-serif !important;
    box-shadow:       none !important;
    border-color:     transparent !important;
}}

/* L3a — header bar */
[data-baseweb="calendar"] > div > div:first-child {{
    background:       {BG_ELEVATED} !important;
    background-color: {BG_ELEVATED} !important;
    border-bottom:    1px solid {BORDER} !important;
    padding:          10px 14px !important;
}}

/* L3b — month/year dropdowns inside header */
[data-baseweb="calendar"] select {{
    background:       {BG_INPUT} !important;
    background-color: {BG_INPUT} !important;
    border:           1px solid {BORDER_MED} !important;
    border-radius:    6px !important;
    font-size:        13px !important;
    padding:          4px 8px !important;
    cursor:           pointer !important;
    color:            {TEXT_PRIMARY} !important;
}}

/* L3c — day-of-week column headers */
[data-baseweb="calendar"] [role="columnheader"],
[data-baseweb="calendar"] [role="columnheader"] * {{
    color:          {TEXT_MUTED} !important;
    font-size:      10px !important;
    font-weight:    700 !important;
    letter-spacing: 0.07em !important;
    text-transform: uppercase !important;
}}

/* L3d — all day cells: consistent size, white text, hover */
[data-baseweb="calendar"] [role="gridcell"] > div,
[data-baseweb="calendar"] [role="gridcell"] > div > div {{
    width:           36px !important;
    height:          36px !important;
    border-radius:   8px !important;
    display:         flex !important;
    align-items:     center !important;
    justify-content: center !important;
    font-size:       13px !important;
    font-weight:     500 !important;
    cursor:          pointer !important;
    color:           {TEXT_PRIMARY} !important;
    transition:      background 0.12s, color 0.12s !important;
}}

/* L3e — hover */
[data-baseweb="calendar"] [role="gridcell"] > div:hover,
[data-baseweb="calendar"] [role="gridcell"] > div:hover * {{
    background:       rgba(88,166,255,0.18) !important;
    background-color: rgba(88,166,255,0.18) !important;
    color:            #ffffff !important;
}}

/* L3f — selected day */
[data-baseweb="calendar"] [aria-selected="true"] > div,
[data-baseweb="calendar"] [data-selected="true"] > div {{
    background:       {BLUE} !important;
    background-color: {BLUE} !important;
    box-shadow:       0 0 0 2px {BLUE}55, 0 4px 16px rgba(88,166,255,0.45) !important;
}}
[data-baseweb="calendar"] [aria-selected="true"] > div > div,
[data-baseweb="calendar"] [data-selected="true"] > div > div {{
    color:       #ffffff !important;
    font-weight: 700 !important;
    font-size:   14px !important;
}}

/* L3g — disabled cell styling is handled by JS (inline styles override CSS) */

/* L3h — empty leading/trailing cells — fully invisible, no click target */
[data-baseweb="calendar"] [role="gridcell"]:empty,
[data-baseweb="calendar"] [role="gridcell"] > div:empty {{
    background:     transparent !important;
    pointer-events: none !important;
    visibility:     hidden !important;
}}

/* ── Pulse animation for status dots ── */
@keyframes vcp-pulse {{
    0%, 100% {{ box-shadow: 0 0 0 0 currentColor; opacity: 1; }}
    50%       {{ box-shadow: 0 0 6px 2px currentColor; opacity: 0.8; }}
}}
.status-dot-live {{ animation: vcp-pulse 2.4s ease-in-out infinite; }}
</style>
"""


_SCROLLBAR_JS = """<script>
(function(){
  if(window._vcpSB)return; window._vcpSB=true;
  var s=document.createElement('style');
  s.id='vcp-sb-css';
  s.textContent=[
    '.vcp-s::-webkit-scrollbar{height:14px!important;width:14px!important}',
    '.vcp-s::-webkit-scrollbar-track{background:rgba(255,255,255,.18)!important;border-radius:7px!important}',
    '.vcp-s::-webkit-scrollbar-thumb{background:#58a6ff!important;border-radius:7px!important}',
    '.vcp-s::-webkit-scrollbar-thumb:vertical{min-height:30px!important}',
    '.vcp-s::-webkit-scrollbar-thumb:horizontal{min-width:30px!important}',
    '.vcp-s::-webkit-scrollbar-thumb:hover{background:#79b8ff!important}'
  ].join('');
  document.head.appendChild(s);
  function tag(){
    document.querySelectorAll('[data-testid="stDataFrame"] div').forEach(function(el){
      var st=(el.getAttribute('style')||'');
      if(st.indexOf('overflow')!==-1){
        el.classList.add('vcp-s');
        el.style.setProperty('overflow-x','scroll','important');
      }
    });
  }
  tag();
  setTimeout(tag,400);setTimeout(tag,1200);setTimeout(tag,3000);
  new MutationObserver(function(){setTimeout(tag,150);}).observe(document.body,{subtree:true,childList:true});
})();
</script>"""


def inject_styles() -> None:
    """Inject global CSS. Call once in main.py, immediately after set_page_config()."""
    st.markdown(_CSS, unsafe_allow_html=True)
    st.markdown(_SCROLLBAR_JS, unsafe_allow_html=True)


# ── HTML component builders ───────────────────────────────────────────────────
# ALL functions MUST return a single-line string with zero internal newlines.
# Streamlit's markdown parser treats indented lines as code blocks.

def _s(*parts: str) -> str:
    """Join HTML parts into a single line (zero newlines)."""
    return "".join(parts)


def state_badge(state: str) -> str:
    """Coloured pill with animated dot for a market state."""
    c = STATE_ACCENT.get(state, BLUE)
    return _s(
        f"<span style='display:inline-flex;align-items:center;gap:7px;",
        f"background:{c}1c;color:{c};border:1px solid {c}45;",
        f"border-radius:7px;padding:5px 14px;font-size:13px;font-weight:700;",
        f"letter-spacing:0.07em;font-family:Inter,sans-serif;",
        f"box-shadow:0 0 16px {c}20'>",
        f"<span class='status-dot-live' style='width:7px;height:7px;border-radius:50%;",
        f"background:{c};display:inline-block;flex-shrink:0;color:{c}'></span>",
        f"{state}</span>",
    )


def metric_card(
    label: str,
    value: str,
    value_colour: str = TEXT_PRIMARY,
    sublabel: str = "",
) -> str:
    """Premium metric card — gradient background with glass top-highlight."""
    sub = _s(
        f"<div style='font-size:11px;color:{TEXT_MUTED};margin-top:7px;font-family:Inter,sans-serif'>",
        sublabel, "</div>",
    ) if sublabel else ""
    return _s(
        f"<div style='background:linear-gradient(145deg,{BG_ELEVATED} 0%,{BG_CARD} 100%);",
        f"border:1px solid {BORDER_MED};border-radius:12px;padding:20px 22px;",
        f"height:100%;position:relative;overflow:hidden'>",
        f"<div style='position:absolute;top:0;left:0;right:0;height:1px;",
        f"background:linear-gradient(90deg,rgba(255,255,255,0.10),rgba(255,255,255,0.04),transparent)'></div>",
        f"<div style='font-size:10px;font-weight:600;color:{TEXT_MUTED};text-transform:uppercase;",
        f"letter-spacing:0.09em;margin-bottom:10px;font-family:Inter,sans-serif'>{label}</div>",
        f"<div style='font-size:26px;font-weight:700;color:{value_colour};line-height:1.2;",
        f"font-family:Inter,sans-serif;letter-spacing:-0.02em'>{value}</div>",
        sub,
        "</div>",
    )


def condition_card(label: str, status: bool | None) -> str:
    """Nifty condition indicator — PASS / FAIL / N/A."""
    if status is True:
        bg, bd, dot, text, colour = f"{GREEN}0d", f"{GREEN}33", GREEN, "PASS", GREEN
    elif status is False:
        bg, bd, dot, text, colour = f"{RED}0d", f"{RED}33", RED, "FAIL", RED
    else:
        bg, bd, dot, text, colour = "rgba(255,255,255,0.02)", BORDER, TEXT_MUTED, "N / A", TEXT_MUTED

    return _s(
        f"<div style='background:{bg};border:1px solid {bd};",
        f"border-radius:10px;padding:16px 18px;height:100%'>",
        f"<div style='font-size:11px;color:{TEXT_MUTED};font-weight:500;",
        f"margin-bottom:12px;font-family:Inter,sans-serif;line-height:1.4'>{label}</div>",
        f"<div style='display:flex;align-items:center;gap:8px'>",
        f"<span style='width:8px;height:8px;border-radius:50%;background:{dot};",
        f"display:inline-block;flex-shrink:0;box-shadow:0 0 6px {dot}80'></span>",
        f"<span style='font-size:13px;font-weight:700;color:{colour};",
        f"font-family:Inter,sans-serif;letter-spacing:0.05em'>{text}</span>",
        "</div></div>",
    )


def section_title(title: str, subtitle: str = "") -> str:
    """Section header with blue left-accent bar — single-line HTML output."""
    sub = _s(
        f"<div style='font-size:12px;color:{TEXT_MUTED};",
        f"margin-top:3px;font-family:Inter,sans-serif'>", subtitle, "</div>",
    ) if subtitle else ""
    return _s(
        f"<div style='display:flex;align-items:flex-start;gap:12px;margin-bottom:18px'>",
        f"<div style='width:3px;min-height:20px;",
        f"background:linear-gradient(180deg,{BLUE},{BLUE}55);",
        f"border-radius:2px;flex-shrink:0;margin-top:2px'></div>",
        f"<div>",
        f"<div style='font-size:15px;font-weight:600;color:{TEXT_PRIMARY};",
        f"font-family:Inter,sans-serif;letter-spacing:-0.01em'>{title}</div>",
        sub,
        "</div></div>",
    )


def run_status_pill(status: str) -> str:
    """Inline pill for pipeline run status."""
    c = RUN_STATUS_COLOUR.get(status, TEXT_MUTED)
    return _s(
        f"<span style='display:inline-flex;align-items:center;gap:5px;",
        f"background:{c}15;color:{c};border:1px solid {c}30;",
        f"border-radius:4px;padding:2px 9px;font-size:11px;font-weight:600;",
        f"font-family:Inter,sans-serif;letter-spacing:0.05em'>",
        f"<span style='width:5px;height:5px;border-radius:50%;background:{c};display:inline-block'></span>",
        f"{status}</span>",
    )
