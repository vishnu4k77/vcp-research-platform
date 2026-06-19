from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.regime.index_regime import IndexRegime, RegimeResult
from app.services.market_query_service import MarketQueryService

logger = get_logger(__name__)


class RegimePipeline:
    """
    Orchestrates market regime detection and persists results to:
      - market_regime       raw detection data (Nifty EMA conditions)
      - market_environment  derived trading parameters (exposure level, etc.)

    Both tables use upsert-by-date semantics: if today's record already
    exists, it is overwritten (idempotent re-runs safe).
    """

    @staticmethod
    def run() -> Optional[RegimeResult]:

        logger.info("Regime pipeline started")

        result = IndexRegime.detect()

        if result is None:
            logger.error("Regime detection returned None — skipping DB writes")
            return None

        RegimePipeline._save_regime(result)
        RegimePipeline._save_environment(result)

        logger.info("Regime pipeline completed | %s", result.market_status)
        return result

    @staticmethod
    def _save_regime(result: RegimeResult) -> None:
        """Upsert today's regime row into market_regime."""

        try:
            with engine.begin() as conn:
                # Delete today's row first (idempotent)
                conn.execute(
                    text(
                        "DELETE FROM market_regime WHERE trade_date = :td"
                    ),
                    {"td": result.trade_date},
                )

                conn.execute(
                    text("""
                        INSERT INTO market_regime (
                            trade_date,
                            nifty_above_50ema,
                            nifty_above_200ema,
                            breadth_positive,
                            vix_risk_off,
                            distribution_count,
                            follow_through_day,
                            regime_score,
                            market_status
                        ) VALUES (
                            :trade_date,
                            :nifty_above_50ema,
                            :nifty_above_200ema,
                            :breadth_positive,
                            :vix_risk_off,
                            :distribution_count,
                            :follow_through_day,
                            :regime_score,
                            :market_status
                        )
                    """),
                    {
                        "trade_date": result.trade_date,
                        "nifty_above_50ema": 1 if result.nifty_above_50ema else 0,
                        "nifty_above_200ema": 1 if result.nifty_above_200ema else 0,
                        "breadth_positive": (
                            1 if result.breadth_positive else 0
                        ) if result.breadth_positive is not None else None,
                        "vix_risk_off": (
                            1 if result.vix_risk_off else 0
                        ) if result.vix_risk_off is not None else None,
                        "distribution_count": result.distribution_count,
                        "follow_through_day": (
                            1 if result.follow_through_day else 0
                        ) if result.follow_through_day is not None else None,
                        "regime_score": result.regime_score,
                        "market_status": result.market_status,
                    },
                )

            logger.info("market_regime saved for %s", result.trade_date)

        except Exception as exc:
            logger.error("market_regime save failed: %s", exc, exc_info=True)

    @staticmethod
    def _save_environment(result: RegimeResult) -> None:
        """
        Derives trading environment parameters from the regime classification
        Reads trading parameters from the market_states SQL table, then upserts into market_environment.
        """

        env = MarketQueryService.get_regime_environment(result.market_status)

        try:
            with engine.begin() as conn:
                # Delete today's row first (idempotent)
                conn.execute(
                    text(
                        "DELETE FROM market_environment WHERE trade_date = :td"
                    ),
                    {"td": result.trade_date},
                )

                conn.execute(
                    text("""
                        INSERT INTO market_environment (
                            trade_date,
                            market_state,
                            regime_score,
                            breadth_score,
                            distribution_days,
                            exposure_level,
                            max_position_size,
                            allow_breakouts,
                            allow_pyramiding,
                            cash_mode
                        ) VALUES (
                            :trade_date,
                            :market_state,
                            :regime_score,
                            :breadth_score,
                            :distribution_days,
                            :exposure_level,
                            :max_position_size,
                            :allow_breakouts,
                            :allow_pyramiding,
                            :cash_mode
                        )
                    """),
                    {
                        "trade_date": result.trade_date,
                        "market_state": result.market_status,
                        "regime_score": result.regime_score,
                        "breadth_score": None,          # Phase 2
                        "distribution_days": None,      # Phase 2
                        "exposure_level": env["exposure_level"],
                        "max_position_size": env["max_position_size"],
                        "allow_breakouts": 1 if env["allow_breakouts"] else 0,
                        "allow_pyramiding": 1 if env["allow_pyramiding"] else 0,
                        "cash_mode": 1 if env["cash_mode"] else 0,
                    },
                )

            logger.info(
                "market_environment saved | exposure=%.1f | allow_breakouts=%s | cash=%s",
                env["exposure_level"],
                env["allow_breakouts"],
                env["cash_mode"],
            )

        except Exception as exc:
            logger.error("market_environment save failed: %s", exc, exc_info=True)
