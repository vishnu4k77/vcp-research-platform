"""Scanner query service — read-only data layer for the Streamlit dashboard.

All SQL lives here. Dashboard pages import this class; no raw SQL in UI code.
Follows the same architectural contract as MarketQueryService.

SQL Server note: parameterised TOP requires the parenthesised form TOP (:n),
not TOP :n.  Hardcoded TOP 1 is exempt.  All variable limits use TOP (:n).
"""

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.config.db import nolock_connect
from app.config.logging_config import get_logger
from app.config.strategy_config import DashboardConfig, StrategyConfig

logger = get_logger(__name__)


class ScannerService:
    """Read-only SQL queries for the Streamlit dashboard.

    Architectural rule: this class only reads. No writes, no indicator math.
    """

    # ── Market overview ───────────────────────────────────────────────────────

    @staticmethod
    def get_latest_environment() -> dict:
        """Return the latest row from market_environment.

        Returns:
            Dict with trade_date, market_state, regime_score, exposure_level,
            allow_breakouts, cash_mode. Empty dict on error or missing data.
        """
        query = text("""
            SELECT TOP 1
                trade_date, market_state, regime_score,
                exposure_level, allow_breakouts, cash_mode
            FROM market_environment
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            if row is None:
                return {}
            return {
                "trade_date":      row[0],
                "market_state":    row[1],
                "regime_score":    row[2],
                "exposure_level":  row[3],
                "allow_breakouts": bool(row[4]),
                "cash_mode":       bool(row[5]),
            }
        except Exception as exc:
            logger.error("get_latest_environment failed: %s", exc, exc_info=True)
            return {}

    @staticmethod
    def get_latest_regime() -> dict:
        """Return the latest row from market_regime.

        Returns:
            Dict with trade_date, market_status, regime_score,
            nifty_above_50ema, nifty_above_200ema. Empty dict on error.
        """
        query = text("""
            SELECT TOP 1
                trade_date, market_status, regime_score,
                nifty_above_50ema, nifty_above_200ema
            FROM market_regime
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            if row is None:
                return {}
            return {
                "trade_date":         row[0],
                "market_status":      row[1],
                "regime_score":       row[2],
                "nifty_above_50ema":  bool(row[3]) if row[3] is not None else None,
                "nifty_above_200ema": bool(row[4]) if row[4] is not None else None,
            }
        except Exception as exc:
            logger.error("get_latest_regime failed: %s", exc, exc_info=True)
            return {}

    @staticmethod
    def get_recent_pipeline_runs(
        limit: int = DashboardConfig.PIPELINE_RUNS_LIMIT,
    ) -> pd.DataFrame:
        """Return the N most recent rows from pipeline_runs.

        Args:
            limit: Maximum rows to return.

        Returns:
            DataFrame with run audit columns. Empty DataFrame on error.
        """
        query = text("""
            SELECT TOP (:limit)
                run_id, pipeline_name, trade_date,
                start_time, end_time, status,
                rows_processed, error_message
            FROM pipeline_runs
            ORDER BY start_time DESC
        """)
        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params={"limit": limit})
            return df
        except Exception as exc:
            logger.error("get_recent_pipeline_runs failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    # ── Scanner table ─────────────────────────────────────────────────────────

    @staticmethod
    def get_signal_date_range() -> tuple[date, date] | tuple[None, None]:
        """Return the earliest and latest trade_date present in stock_signals.

        Used to set the calendar picker bounds so the user can navigate the
        full 5-year historical range, not just the last 60 days.

        Returns:
            Tuple (min_date, max_date), or (None, None) on error / empty table.
        """
        query = text("""
            SELECT MIN(trade_date) AS min_date, MAX(trade_date) AS max_date
            FROM stock_signals
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            if row and row[0] and row[1]:
                return row[0], row[1]
            return None, None
        except Exception as exc:
            logger.error("get_signal_date_range failed: %s", exc, exc_info=True)
            return None, None

    @staticmethod
    def get_nearest_trade_date(target: date) -> Optional[date]:
        """Return the closest available trade_date on or before target.

        When the user picks a weekend or market holiday via the calendar,
        this snaps them to the most recent trading day that has signal data.

        Args:
            target: The date selected by the user.

        Returns:
            Closest trade_date <= target, or None if no dates exist.
        """
        query = text("""
            SELECT TOP 1 trade_date
            FROM stock_signals
            WHERE trade_date <= :target
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query, {"target": target}).fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.error("get_nearest_trade_date failed: %s", exc, exc_info=True)
            return None

    @staticmethod
    def get_all_trade_dates() -> list[date]:
        """Return every distinct trade_date in stock_signals, newest first.

        Used by the scanner date picker (searchable selectbox) so the user
        can type any year, month, or day fragment to filter across the full
        historical range — not just the last 60 days.

        Returns:
            List of datetime.date values descending. Empty list on error.
        """
        query = text("""
            SELECT DISTINCT trade_date
            FROM stock_signals
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                rows = conn.execute(query).fetchall()
            return [r[0] for r in rows if r[0] is not None]
        except Exception as exc:
            logger.error("get_all_trade_dates failed: %s", exc, exc_info=True)
            return []

    @staticmethod
    def get_available_trade_dates(
        limit: int = DashboardConfig.DATE_PICKER_LIMIT,
    ) -> list[date]:
        """Return the N most recent distinct trade_dates in stock_signals.

        Retained for backward compatibility (regime history chart, etc.).
        The scanner date picker uses get_all_trade_dates() instead.

        Args:
            limit: Maximum dates to return.

        Returns:
            List of datetime.date values descending. Empty list on error.
        """
        query = text("""
            SELECT DISTINCT TOP (:limit) trade_date
            FROM stock_signals
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                rows = conn.execute(query, {"limit": limit}).fetchall()
            return [r[0] for r in rows if r[0] is not None]
        except Exception as exc:
            logger.error("get_available_trade_dates failed: %s", exc, exc_info=True)
            return []

    @staticmethod
    def get_index_list() -> list[dict]:
        """Return active index codes that have at least one current member in nse_index_membership.

        Filters out any index that exists in nse_index_ref but has no membership
        data yet (e.g. NIFTY50 before fetch_nse_tickers.py downloads its constituents),
        so the UI never shows an option that always returns no results.

        Returns:
            List of dicts {index_code, index_name}. Empty list on error.
        """
        query = text("""
            SELECT nir.index_code, nir.index_name
            FROM nse_index_ref nir
            WHERE nir.is_active = 1
              AND EXISTS (
                  SELECT 1
                  FROM nse_index_membership nim
                  WHERE nim.index_code      = nir.index_code
                    AND nim.is_current_member = 1
              )
            ORDER BY nir.index_code
        """)
        try:
            with nolock_connect() as conn:
                rows = conn.execute(query).fetchall()
            return [{"index_code": r[0], "index_name": r[1]} for r in rows]
        except Exception as exc:
            logger.error("get_index_list failed: %s", exc, exc_info=True)
            return []

    @staticmethod
    def get_scanner_data(
        trade_date: date,
        index_code: Optional[str] = None,
        min_score: float = DashboardConfig.SCANNER_DEFAULT_MIN_SCORE,
        fetch_n: int = DashboardConfig.SCANNER_FETCH_N,
    ) -> pd.DataFrame:
        """Return all scanner rows for a given date so presets can rerank the full universe.

        TOP N display limiting is intentionally deferred to the caller (scanner_table.py)
        so that preset scoring can rerank the FULL universe before clipping — not just
        the top-100 by default composite_score (which would miss RS Leaders ranked 101-500
        by composite but #1 by RS weight).

        Args:
            trade_date: The date to query.
            index_code: Optional NSE index code (e.g. 'NIFTY500').
            min_score: Minimum composite_score threshold (typically 0 for full universe).
            fetch_n: Safety upper bound on rows returned (SCANNER_FETCH_N ≥ universe size).

        Returns:
            DataFrame with symbol, company_name, sector, and all signal columns.
            Empty DataFrame on error.
        """
        index_join = (
            """INNER JOIN nse_index_membership nim
                   ON REPLACE(ss.symbol, :nse_suffix, '') = nim.symbol
                   AND nim.index_code = :index_code
                   AND nim.is_current_member = 1"""
            if index_code else ""
        )

        query = text(f"""
            SELECT TOP (:fetch_n)
                ss.symbol,
                ISNULL(nu.company_name, ss.symbol)  AS company_name,
                ISNULL(nu.sector,       'Unknown')   AS sector,
                ss.composite_score,
                ss.composite_rank,
                ss.institutional_candidate,
                ss.trend_signal,
                ss.stage2_signal,
                ss.vcp_signal,
                ss.breakout_signal,
                ss.breakout_ready_signal,
                ss.liquidity_signal,
                ss.quality_signal,
                ss.rs_signal,
                ss.distance_from_pivot_pct,
                ss.distance_from_52w_high_pct,
                ss.stage2_days,
                ss.stage2_started_date,
                sf.quality_score        AS fund_quality_score,
                sf.promoter_holding     AS fund_promoter_pct,
                sf.roe                  AS fund_roe,
                sf.roce                 AS fund_roce
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            LEFT JOIN (
                SELECT symbol, quality_score, promoter_holding, roe, roce,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
                FROM stock_fundamentals
            ) sf ON sf.symbol = REPLACE(ss.symbol, :nse_suffix, '') AND sf.rn = 1
            {index_join}
            WHERE ss.trade_date    = :trade_date
              AND ss.composite_score >= :min_score
            ORDER BY ss.composite_score DESC, ss.symbol ASC
        """)

        params: dict = {
            "trade_date": trade_date,
            "min_score":  min_score,
            "fetch_n":    fetch_n,
            "nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX,
        }
        if index_code:
            params["index_code"] = index_code

        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            logger.debug(
                "scanner_data: %d rows | date=%s | index=%s | min_score=%.0f",
                len(df), trade_date, index_code or "ALL", min_score,
            )
            return df
        except Exception as exc:
            logger.error("get_scanner_data failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def get_last_signal_date() -> Optional[date]:
        """Return the most recent trade_date that has at least one active directional signal.

        Used by the scanner to suggest a useful date when the latest date is in
        BEAR / cash_mode (all directional signals suppressed).

        Directional signals checked: trend_signal, stage2_signal, vcp_signal, breakout_signal.

        Returns:
            Most recent date with trend_signal > 0, or None if none found.
        """
        query = text("""
            SELECT TOP 1 trade_date
            FROM stock_signals
            WHERE trend_signal = 1
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.error("get_last_signal_date failed: %s", exc, exc_info=True)
            return None

    # ── Sector heatmap ────────────────────────────────────────────────────────

    @staticmethod
    def get_sector_summary(
        trade_date: date,
        index_code: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return per-sector signal aggregation for the heatmap.

        Rows with NULL sector are labelled 'Unknown'. Returns empty DataFrame
        when no data exists for the given date.

        Args:
            trade_date: The date to aggregate.
            index_code: Optional NSE index code (e.g. 'NIFTY50') to restrict
                the universe.  None means all stocks.

        Returns:
            DataFrame with sector, total_stocks, signal counts,
            avg_composite_score, stage2_pct.
        """
        index_join = (
            """INNER JOIN nse_index_membership nim
                   ON REPLACE(ss.symbol, :nse_suffix, '') = nim.symbol
                   AND nim.index_code = :index_code
                   AND nim.is_current_member = 1"""
            if index_code else ""
        )

        query = text(f"""
            SELECT
                ISNULL(nu.sector, 'Unknown')            AS sector,
                COUNT(*)                                 AS total_stocks,
                SUM(CAST(ss.stage2_signal    AS INT))    AS stage2_count,
                SUM(CAST(ss.trend_signal     AS INT))    AS trend_count,
                SUM(CAST(ss.liquidity_signal AS INT))    AS liquidity_count,
                SUM(CAST(ss.rs_signal        AS INT))    AS rs_count,
                AVG(CAST(ss.composite_score  AS FLOAT))  AS avg_composite_score,
                CAST(SUM(CAST(ss.stage2_signal AS INT)) AS FLOAT)
                    / NULLIF(COUNT(*), 0) * 100          AS stage2_pct
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            {index_join}
            WHERE ss.trade_date = :trade_date
            GROUP BY ISNULL(nu.sector, 'Unknown')
            ORDER BY stage2_pct DESC
        """)

        params: dict = {
            "trade_date": trade_date,
            "nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX,
        }
        if index_code:
            params["index_code"] = index_code

        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            return df
        except Exception as exc:
            logger.error("get_sector_summary failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def get_regime_history(limit: int = DashboardConfig.DATE_PICKER_LIMIT) -> pd.DataFrame:
        """Return historical market_environment rows, ascending by date.

        Used by the regime history chart on the Market Overview tab.

        Args:
            limit: Maximum number of rows (most recent N trading days).

        Returns:
            DataFrame with trade_date, market_state, regime_score,
            exposure_level.  Rows are sorted oldest → newest.
            Empty DataFrame on error or no data.
        """
        query = text("""
            SELECT TOP (:limit)
                trade_date,
                market_state,
                regime_score,
                exposure_level
            FROM market_environment
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params={"limit": limit})
            return df.iloc[::-1].reset_index(drop=True)   # flip to ascending
        except Exception as exc:
            logger.error("get_regime_history failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    # ── Stock detail ──────────────────────────────────────────────────────────

    @staticmethod
    def get_stock_detail(
        symbol: str,
        lookback_days: int = DashboardConfig.CHART_LOOKBACK_DAYS,
    ) -> pd.DataFrame:
        """Return price + EMA + signal history for a single symbol.

        Args:
            symbol: Ticker as stored in stock_features (e.g. 'RELIANCE.NS').
            lookback_days: Number of most-recent trading days to return.

        Returns:
            DataFrame sorted ascending by trade_date. Empty on error.
        """
        query = text("""
            SELECT TOP (:lookback)
                sf.trade_date, sf.close_price,
                sf.high_52w, sf.low_52w,
                sf.ema_10, sf.ema_21, sf.ema_50, sf.ema_150, sf.ema_200,
                sf.atr_14, sf.volatility_contraction,
                ss.composite_score,
                ss.trend_signal, ss.stage2_signal, ss.vcp_signal,
                ss.breakout_signal, ss.breakout_ready_signal,
                ss.liquidity_signal, ss.quality_signal, ss.rs_signal,
                ss.distance_from_pivot_pct, ss.distance_from_52w_high_pct,
                ss.stage2_days, ss.stage2_started_date
            FROM stock_features sf
            LEFT JOIN stock_signals ss
                ON sf.symbol    = ss.symbol
                AND sf.trade_date = ss.trade_date
            WHERE sf.symbol = :symbol
            ORDER BY sf.trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                df = pd.read_sql(
                    query, conn,
                    params={"symbol": symbol, "lookback": lookback_days},
                )
            return df.iloc[::-1].reset_index(drop=True)
        except Exception as exc:
            logger.error(
                "get_stock_detail failed for %s: %s", symbol, exc, exc_info=True
            )
            return pd.DataFrame()

    @staticmethod
    def get_sector_stocks(
        trade_date: date,
        sector: str,
        index_code: Optional[str] = None,
        top_n: int = DashboardConfig.SCANNER_TOP_N,
    ) -> pd.DataFrame:
        """Return ranked scanner rows for all stocks in a given sector.

        Args:
            trade_date: The date to query.
            sector: Sector name matching nse_universe.sector (e.g. 'Capital Goods').
            index_code: Optional NSE index code to restrict the universe.
            top_n: Maximum rows returned.

        Returns:
            DataFrame with same columns as get_scanner_data(). Empty on error.
        """
        index_join = (
            """INNER JOIN nse_index_membership nim
                   ON REPLACE(ss.symbol, :nse_suffix, '') = nim.symbol
                   AND nim.index_code = :index_code
                   AND nim.is_current_member = 1"""
            if index_code else ""
        )

        query = text(f"""
            SELECT TOP (:top_n)
                ss.symbol,
                ISNULL(nu.company_name, ss.symbol) AS company_name,
                ISNULL(nu.sector,       'Unknown')  AS sector,
                ss.composite_score,
                ss.institutional_candidate,
                ss.trend_signal,
                ss.stage2_signal,
                ss.vcp_signal,
                ss.breakout_signal,
                ss.breakout_ready_signal,
                ss.liquidity_signal,
                ss.quality_signal,
                ss.rs_signal,
                ss.distance_from_pivot_pct,
                ss.distance_from_52w_high_pct
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            {index_join}
            WHERE ss.trade_date = :trade_date
              AND ISNULL(nu.sector, 'Unknown') = :sector
            ORDER BY ss.composite_score DESC, ss.symbol ASC
        """)

        params: dict = {
            "trade_date": trade_date,
            "sector":     sector,
            "nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX,
            "top_n":      top_n,
        }
        if index_code:
            params["index_code"] = index_code

        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params=params)
            logger.debug(
                "get_sector_stocks: %d rows | sector=%s | index=%s | date=%s",
                len(df), sector, index_code or "ALL", trade_date,
            )
            return df
        except Exception as exc:
            logger.error("get_sector_stocks failed for %s: %s", sector, exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def get_stock_fundamentals(symbol: str) -> dict:
        """Return the latest fundamental metrics for a single symbol.

        Queries stock_fundamentals for the most-recent trade_date row.
        Symbol is stored in stock_fundamentals without the Yahoo .NS suffix,
        so the suffix is stripped before the lookup.

        Args:
            symbol: Ticker as stored in stock_signals (Yahoo format, e.g. 'RELIANCE.NS').

        Returns:
            Dict with roe, roce, debt_to_equity, sales_growth_3yr, profit_growth_3yr,
            opm, eps_ttm, promoter_holding, promoter_pledge_pct, market_cap_cr,
            pe_ratio, quality_score, scraped_at.  Empty dict if no data exists.
        """
        bare_symbol = symbol.replace(StrategyConfig.YAHOO_NSE_SUFFIX, "")
        query = text("""
            SELECT TOP 1
                roe, roce, debt_to_equity,
                sales_growth_3yr, profit_growth_3yr, opm,
                eps_ttm, promoter_holding, promoter_pledge_pct,
                market_cap_cr, pe_ratio, quality_score,
                eps_acceleration, scraped_at, trade_date
            FROM stock_fundamentals
            WHERE symbol = :symbol
            ORDER BY trade_date DESC
        """)
        try:
            with nolock_connect() as conn:
                row = conn.execute(query, {"symbol": bare_symbol}).fetchone()
            if row is None:
                return {}
            keys = [
                "roe", "roce", "debt_to_equity",
                "sales_growth_3yr", "profit_growth_3yr", "opm",
                "eps_ttm", "promoter_holding", "promoter_pledge_pct",
                "market_cap_cr", "pe_ratio", "quality_score",
                "eps_acceleration", "scraped_at", "trade_date",
            ]
            return dict(zip(keys, row))
        except Exception as exc:
            logger.error("get_stock_fundamentals failed for %s: %s", symbol, exc, exc_info=True)
            return {}

    @staticmethod
    def get_all_symbols_with_names(trade_date: date) -> pd.DataFrame:
        """Return symbol + company_name for all stocks on a date, for search UI.

        Args:
            trade_date: The date to query (ensures only pipeline-processed symbols).

        Returns:
            DataFrame with columns [symbol, company_name], sorted by company_name.
            Empty DataFrame on error.
        """
        query = text("""
            SELECT DISTINCT
                ss.symbol,
                ISNULL(nu.company_name, ss.symbol) AS company_name
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            WHERE ss.trade_date = :trade_date
            ORDER BY company_name ASC
        """)
        try:
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params={
                    "trade_date": trade_date,
                    "nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX,
                })
            return df
        except Exception as exc:
            logger.error("get_all_symbols_with_names failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def get_symbol_list() -> list[str]:
        """Return distinct symbols that have been processed by the pipeline.

        Queries stock_signals (not nse_universe) so the returned symbols
        exactly match the ticker format used in stock_features and
        stock_signals (Yahoo Finance .NS suffix format).

        Returns:
            Sorted list of symbol strings. Empty list on error.
        """
        query = text("""
            SELECT DISTINCT symbol
            FROM stock_signals
            ORDER BY symbol ASC
        """)
        try:
            with nolock_connect() as conn:
                rows = conn.execute(query).fetchall()
            return [r[0] for r in rows]
        except Exception as exc:
            logger.error("get_symbol_list failed: %s", exc, exc_info=True)
            return []

    # ── Scanner preset config (SQL-driven) ───────────────────────────────────

    @staticmethod
    def get_scanner_presets() -> dict[str, dict[str, int]]:
        """Load scanner preset weights from scanner_preset_config SQL table.

        Returns the same structure as DashboardConfig.SCANNER_PRESETS so all
        callers are drop-in compatible. Presets are ordered by sort_order then
        preset_name. Falls back to DashboardConfig.SCANNER_PRESETS if the table
        is empty or the query fails (e.g. before setup_db.py is run).

        To add a new preset without touching Python code::

            INSERT INTO scanner_preset_config
                (preset_name, signal_name, weight, is_active, sort_order)
            VALUES ('Momentum', 'rs_signal', 50, 1, 6), ...

        Returns:
            Dict mapping preset_name → {signal_name: weight}. Ordered by
            sort_order so the UI dropdown matches the intended display sequence.
        """
        query = text("""
            SELECT preset_name, signal_name, weight
            FROM scanner_preset_config
            WHERE is_active = 1
            ORDER BY sort_order ASC, preset_name ASC, signal_name ASC
        """)
        try:
            with nolock_connect() as conn:
                rows = conn.execute(query).fetchall()

            if not rows:
                logger.debug("scanner_preset_config empty — using Python defaults")
                return DashboardConfig.SCANNER_PRESETS.copy()

            presets: dict[str, dict[str, int]] = {}
            for preset_name, signal_name, weight in rows:
                if preset_name not in presets:
                    presets[preset_name] = {}
                presets[preset_name][signal_name] = int(weight)

            logger.debug("Loaded %d presets from scanner_preset_config", len(presets))
            return presets

        except Exception as exc:
            logger.debug("scanner_preset_config unavailable — using Python defaults: %s", exc)
            return DashboardConfig.SCANNER_PRESETS.copy()

    @staticmethod
    def get_default_preset_name() -> str:
        """Return the preset_name marked is_default=1 in scanner_preset_config.

        Falls back to the first key in DashboardConfig.SCANNER_PRESETS if the
        table is unavailable or no row has is_default=1.

        Returns:
            Preset name string, e.g. "Composite (Default)".
        """
        query = text(
            "SELECT TOP 1 preset_name FROM scanner_preset_config "
            "WHERE is_default = 1 AND is_active = 1"
        )
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            if row:
                return str(row[0])
        except Exception as exc:
            logger.debug("Could not load default preset: %s", exc)
        return next(iter(DashboardConfig.SCANNER_PRESETS))

    # ── Symbol search helpers (shared across dashboard tabs) ──────────────────

    @staticmethod
    def format_symbol_option(company_name: str, symbol: str) -> str:
        """Format a company name and symbol into a searchable display string.

        Produces the canonical "Company Name  (SYMBOL.NS)" format used in every
        dashboard selectbox so Streamlit's built-in text filter can match either
        the company name or the ticker without any custom search logic.

        Args:
            company_name: Human-readable company name from nse_universe.
            symbol: Yahoo Finance ticker (e.g. 'RELIANCE.NS').

        Returns:
            Formatted string, e.g. "Reliance Industries  (RELIANCE.NS)".
        """
        return f"{company_name}  ({symbol})"

    @staticmethod
    def parse_symbol_from_option(option: str) -> str:
        """Extract the raw ticker from a format_symbol_option display string.

        Splits on the last '(' to handle company names that may themselves
        contain parentheses (e.g. "Tata Motors (DVR)  (TATAMOTORS.NS)").

        Args:
            option: Display string produced by format_symbol_option.

        Returns:
            Ticker string, e.g. "RELIANCE.NS".
        """
        return option.rsplit("(", 1)[-1].rstrip(")")

    @staticmethod
    def get_symbol_display_options() -> list[str]:
        """Return all ever-processed symbols as formatted searchable display strings.

        Unlike get_all_symbols_with_names(), this method carries no date filter —
        it returns the full universe of symbols the pipeline has ever produced.
        Intended for the Stock Detail tab where date-scoping is not appropriate.

        Returns:
            List of "Company Name  (SYMBOL.NS)" strings sorted by company name.
            Falls back to bare ticker format if nse_universe has no company name.
            Empty list on error or no data.
        """
        query = text("""
            SELECT DISTINCT
                ss.symbol,
                ISNULL(nu.company_name, ss.symbol) AS company_name
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            ORDER BY company_name ASC
        """)
        try:
            with nolock_connect() as conn:
                df = pd.read_sql(
                    query, conn, params={"nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX}
                )
            if df.empty:
                return []
            return [
                ScannerService.format_symbol_option(row["company_name"], row["symbol"])
                for _, row in df.iterrows()
            ]
        except Exception as exc:
            logger.error("get_symbol_display_options failed: %s", exc, exc_info=True)
            return []
