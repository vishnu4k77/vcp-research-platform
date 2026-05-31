"""NSE VCP Scanner — Streamlit dashboard entry point.

Run from the project root:
    python -m streamlit run app/dashboard/main.py

Tab layout:
    1. Market Overview  — regime card + pipeline health
    2. Sector Heatmap   — stage2 % per sector (requires sector data in nse_universe)
    3. Scanner          — top candidates table with sidebar filters
    4. Stock Detail     — price chart + EMA overlay + signal history
"""

import sys
from pathlib import Path

# Ensure project root is importable regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="NSE VCP Scanner",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.dashboard.styles import inject_styles, TEXT_SECONDARY
from app.dashboard.market_overview import render_market_overview
from app.dashboard.sector_heatmap import render_sector_heatmap
from app.dashboard.scanner_table import render_scanner_table
from app.dashboard.stock_detail import render_stock_detail
from app.dashboard.backtest_tab import render_backtest_tab

inject_styles()

st.markdown(
    f"""
    <div style='margin-bottom:1.5rem'>
        <div style='font-size:24px;font-weight:700;color:#e6edf3;
                    letter-spacing:-0.03em;font-family:Inter,sans-serif'>
            NSE VCP Breakout Scanner
        </div>
        <div style='font-size:13px;color:{TEXT_SECONDARY};
                    margin-top:3px;font-family:Inter,sans-serif'>
            Institutional-grade signal engine for NSE equities
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Market Overview",
    "Sector Heatmap",
    "Scanner",
    "Stock Detail",
    "Backtest",
])

with tab1:
    render_market_overview()

with tab2:
    render_sector_heatmap()

with tab3:
    render_scanner_table()

with tab4:
    render_stock_detail()

with tab5:
    render_backtest_tab()
