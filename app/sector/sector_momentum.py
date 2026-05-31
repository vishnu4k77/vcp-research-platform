"""Sector rotation momentum engine.

Computes a quantitative momentum score per sector for every trade_date that
has signal data.  Persists results to sector_momentum (upsert-by-date+sector).

Scoring model
-------------
Each sector receives a weighted composite of four signal ratios:
  trend_pct   — % of stocks with trend_signal = 1
  rs_pct      — % of stocks with rs_signal = 1  (beating Nifty)
  stage2_pct  — % of stocks in Weinstein Stage 2
  avg_score   — average composite_score / 100  (normalised to 0-1)

Weights live in SectorConfig.MOMENTUM_WEIGHTS — zero code changes to retune.

Momentum delta
--------------
For dates where MOMENTUM_LOOKBACK_WEEKS prior data exists, the engine also
stores prev_momentum_score and momentum_delta (positive = accelerating sector).
This is the signal that separates leading from lagging sectors.
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import SectorConfig, StrategyConfig

logger = get_logger(__name__)

# Number of trading days to approximate N weeks back when querying prior score.
_TRADING_DAYS_PER_WEEK = 5


class SectorMomentum:
    """Compute and persist sector rotation momentum scores.

    Public entry point: SectorMomentum.run()
    """

    # ── Data loading ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_sector_signal_summary(trade_date: date) -> pd.DataFrame:
        """Query per-sector signal aggregates for a single trade_date.

        Joins stock_signals → nse_universe to attach sector names.
        Only sectors with at least SectorConfig.MIN_STOCKS_FOR_RANKING stocks
        are returned.

        Args:
            trade_date: The date to aggregate.

        Returns:
            DataFrame with columns:
              sector, total_stocks, avg_composite_score,
              trend_pct, stage2_pct, rs_pct, liquidity_pct.
            Empty DataFrame if no signal data for this date.
        """
        query = text(f"""
            SELECT
                ISNULL(nu.sector, 'Unknown')               AS sector,
                COUNT(*)                                    AS total_stocks,
                AVG(CAST(ss.composite_score AS FLOAT))      AS avg_composite_score,
                CAST(SUM(CAST(ss.trend_signal     AS INT)) AS FLOAT)
                    / NULLIF(COUNT(*), 0) * 100             AS trend_pct,
                CAST(SUM(CAST(ss.stage2_signal    AS INT)) AS FLOAT)
                    / NULLIF(COUNT(*), 0) * 100             AS stage2_pct,
                CAST(SUM(CAST(ss.rs_signal        AS INT)) AS FLOAT)
                    / NULLIF(COUNT(*), 0) * 100             AS rs_pct,
                CAST(SUM(CAST(ss.liquidity_signal AS INT)) AS FLOAT)
                    / NULLIF(COUNT(*), 0) * 100             AS liquidity_pct
            FROM stock_signals ss
            LEFT JOIN nse_universe nu
                ON nu.symbol = REPLACE(ss.symbol, :nse_suffix, '')
            WHERE ss.trade_date = :trade_date
            GROUP BY ISNULL(nu.sector, 'Unknown')
            HAVING COUNT(*) >= :min_stocks
        """)

        try:
            with engine.connect() as conn:
                df = pd.read_sql(
                    query,
                    conn,
                    params={
                        "trade_date": trade_date,
                        "nse_suffix": StrategyConfig.YAHOO_NSE_SUFFIX,
                        "min_stocks": SectorConfig.MIN_STOCKS_FOR_RANKING,
                    },
                )
            logger.debug(
                "Sector summary loaded | date=%s | sectors=%d", trade_date, len(df)
            )
            return df
        except Exception as exc:
            logger.error(
                "Sector signal summary failed for %s: %s", trade_date, exc, exc_info=True
            )
            return pd.DataFrame()

    @staticmethod
    def _load_prior_momentum(
        trade_date: date,
        lookback_weeks: int,
    ) -> dict[str, float]:
        """Fetch sector momentum scores from N weeks ago for delta computation.

        Uses a date window of ±2 trading days around the target date to handle
        weekends, holidays, and missing pipeline runs gracefully.

        Args:
            trade_date: Current date.
            lookback_weeks: Number of weeks to look back.

        Returns:
            Dict mapping sector name → momentum_score from the prior period.
            Empty dict if no prior data exists.
        """
        lookback_days = lookback_weeks * _TRADING_DAYS_PER_WEEK
        target_prior = trade_date - timedelta(days=lookback_days)
        window_days = 2

        query = text("""
            SELECT sector, momentum_score
            FROM sector_momentum
            WHERE trade_date BETWEEN :date_from AND :date_to
              AND momentum_score IS NOT NULL
        """)

        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    query,
                    {
                        "date_from": target_prior - timedelta(days=window_days),
                        "date_to":   target_prior + timedelta(days=window_days),
                    },
                ).fetchall()
            return {r[0]: float(r[1]) for r in rows}
        except Exception as exc:
            logger.warning("Prior momentum load failed: %s", exc)
            return {}

    # ── Score computation ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_scores(df: pd.DataFrame) -> pd.DataFrame:
        """Compute momentum_score and momentum_rank for all sectors.

        Momentum score formula (0–100):
            score = Σ(weight_i × metric_i)   where metrics are 0-100 pct values
                                               and avg_score is already 0-100.

        Weights are taken from SectorConfig.MOMENTUM_WEIGHTS. The avg_score
        component is multiplied by 1 (already 0-100 scale).

        Args:
            df: Output of _load_sector_signal_summary() with signal pct columns.

        Returns:
            df with momentum_score (float) and momentum_rank (int, 1=best) added.
        """
        weights = SectorConfig.MOMENTUM_WEIGHTS

        df = df.copy()

        # Fill NaN ratios with 0 so missing signals don't distort score
        for col in ("trend_pct", "rs_pct", "stage2_pct", "avg_composite_score"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        df["momentum_score"] = (
            df["trend_pct"]           * weights.get("trend_pct",  0.30)
            + df["rs_pct"]            * weights.get("rs_pct",     0.35)
            + df["stage2_pct"]        * weights.get("stage2_pct", 0.25)
            + df["avg_composite_score"] * weights.get("avg_score", 0.10)
        ).round(2)

        df["momentum_rank"] = (
            df["momentum_score"]
            .rank(ascending=False, method="dense")
            .astype(int)
        )

        return df

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_sector_momentum(df: pd.DataFrame, trade_date: date) -> None:
        """Upsert sector_momentum rows for one trade_date.

        Deletes existing rows for this date first (idempotent re-runs safe).

        Args:
            df: Computed DataFrame with all momentum columns.
            trade_date: Date these rows belong to.
        """
        if df.empty:
            logger.warning("No sector momentum rows to save for %s", trade_date)
            return

        # SQL Server rejects Python float('nan') — replace at dict level before binding.
        # df.where() is insufficient because to_dict() still emits nan for mixed-type frames.
        import math

        def _nan_to_none(v):
            return None if (isinstance(v, float) and math.isnan(v)) else v

        rows = [
            {k: _nan_to_none(v) for k, v in row.items()}
            for row in df.to_dict(orient="records")
        ]

        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM sector_momentum WHERE trade_date = :td"),
                    {"td": trade_date},
                )
                conn.execute(
                    text("""
                        INSERT INTO sector_momentum (
                            trade_date, sector, total_stocks,
                            avg_composite_score,
                            trend_pct, stage2_pct, rs_pct, liquidity_pct,
                            momentum_score, momentum_rank,
                            prev_momentum_score, momentum_delta
                        ) VALUES (
                            :trade_date, :sector, :total_stocks,
                            :avg_composite_score,
                            :trend_pct, :stage2_pct, :rs_pct, :liquidity_pct,
                            :momentum_score, :momentum_rank,
                            :prev_momentum_score, :momentum_delta
                        )
                    """),
                    rows,
                )
            logger.info(
                "sector_momentum saved | date=%s | sectors=%d", trade_date, len(rows)
            )
        except Exception as exc:
            logger.error(
                "sector_momentum save failed for %s: %s", trade_date, exc, exc_info=True
            )

    # ── Entry point ───────────────────────────────────────────────────────────

    @staticmethod
    def run(trade_date: Optional[date] = None) -> None:
        """Compute and persist sector momentum scores for one trade_date.

        If trade_date is None, uses the latest date present in stock_signals.

        Args:
            trade_date: Date to compute scores for.  None = latest available.
        """
        if trade_date is None:
            try:
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT MAX(trade_date) FROM stock_signals")
                    ).fetchone()
                trade_date = row[0] if row and row[0] else None
            except Exception as exc:
                logger.error("Could not determine latest signal date: %s", exc)
                return

        if trade_date is None:
            logger.warning("No signal data in stock_signals — sector momentum skipped")
            return

        logger.info("Sector momentum started | date=%s", trade_date)

        summary_df = SectorMomentum._load_sector_signal_summary(trade_date)
        if summary_df.empty:
            logger.warning("No sector summary data for %s — skipping", trade_date)
            return

        scored_df = SectorMomentum._compute_scores(summary_df)

        # Attach momentum delta from N weeks ago
        prior_scores = SectorMomentum._load_prior_momentum(
            trade_date, SectorConfig.MOMENTUM_LOOKBACK_WEEKS
        )
        scored_df["prev_momentum_score"] = scored_df["sector"].map(prior_scores)
        scored_df["momentum_delta"] = (
            scored_df["momentum_score"] - scored_df["prev_momentum_score"]
        ).round(2)

        # Rename avg_composite_score column to match table schema
        scored_df = scored_df.rename(
            columns={"avg_composite_score": "avg_composite_score"}
        )

        # Ensure all required columns are present (NaN for missing optional fields)
        scored_df["trade_date"] = trade_date

        SectorMomentum._upsert_sector_momentum(scored_df, trade_date)

        logger.info(
            "Sector momentum completed | date=%s | sectors=%d | top=%s (%.1f)",
            trade_date,
            len(scored_df),
            scored_df.sort_values("momentum_rank").iloc[0]["sector"] if not scored_df.empty else "—",
            scored_df["momentum_score"].max() if not scored_df.empty else 0.0,
        )
