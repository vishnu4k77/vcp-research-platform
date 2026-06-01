import sys
from typing import Optional

from app.config.logging_config import get_logger
from app.monitoring.pipeline_monitor import PipelineMonitor
from app.pipeline.pipeline_context import PipelineContext
from app.main import DataIngestionPipeline
from app.data.earnings_service import EarningsService
from app.features.feature_pipeline import FeaturePipeline
from app.regime.regime_pipeline import RegimePipeline
from app.signals.signal_pipeline import SignalPipeline
from app.sector.sector_momentum import SectorMomentum

logger = get_logger(__name__)


class MasterPipeline:
    """
    End-to-end EOD pipeline coordinator.

    Execution order:
      1. Ingestion  — pulls incremental OHLCV from Yahoo Finance
      2. Features   — computes all technical indicators
      3. Regime     — detects Nifty 50 Bull/Bear/Choppy regime
      4. Signals    — runs pattern detectors + composite ranker

    Failure policy: a failure in any step aborts the pipeline at that stage.
    PipelineContext tracks completed/failed steps and is persisted to
    pipeline_runs via PipelineMonitor for full audit trail.

    Usage:
        result = MasterPipeline.run()
        result = MasterPipeline.run(skip_ingestion=True)   # re-process only
        sys.exit(0 if result.pipeline_status == "SUCCESS" else 1)
    """

    @staticmethod
    def _run_step(
        context: PipelineContext,
        step_name: str,
        step_fn,
    ) -> bool:
        """Execute one pipeline step and update context. Returns True on success."""

        logger.info("── Step [%s] starting", step_name)

        try:
            step_fn()
            context.mark_step_completed(step_name)
            logger.info("── Step [%s] completed", step_name)
            return True

        except Exception as exc:
            logger.error("── Step [%s] failed: %s", step_name, exc, exc_info=True)
            context.mark_step_failed(step_name)
            return False

    @staticmethod
    def run(skip_ingestion: bool = False) -> PipelineContext:

        context = PipelineContext()

        logger.info(
            "═══ Master pipeline started | execution_id=%s ═══",
            context.execution_id,
        )

        # Record pipeline start in audit table (non-fatal if DB unavailable)
        PipelineMonitor.record_start(context)

        # ── Step 1: Data ingestion ────────────────────────────────────────
        if skip_ingestion:
            logger.info("── Step [ingestion] skipped by caller")
            context.mark_step_completed("ingestion")

        else:
            if not MasterPipeline._run_step(
                context, "ingestion", DataIngestionPipeline.run
            ):
                return MasterPipeline._finalize(context)

        # ── Step 2: Earnings dates refresh (non-blocking) ────────────────
        # Refreshes stock_earnings_dates for all active symbols so FeaturePipeline
        # can mark is_earnings_day correctly.  Stale-check (STALE_AFTER_DAYS)
        # means Yahoo is only called when data is > 7 days old — not every run.
        # Non-blocking: a fetch failure means is_earnings_day defaults to 0
        # (safe conservative fallback; the gap-and-fail guard still provides protection).
        logger.info("── Step [earnings_refresh] starting")
        try:
            from app.config.ticker_loader import load_tickers
            symbols = load_tickers()
            EarningsService.refresh_all(symbols)
            context.mark_step_completed("earnings_refresh")
            logger.info("── Step [earnings_refresh] completed")
        except Exception as exc:
            logger.warning(
                "── Step [earnings_refresh] failed (non-blocking): %s", exc
            )
            context.add_warning(f"Earnings refresh failed: {exc}")
            context.mark_step_completed("earnings_refresh")  # non-blocking

        # ── Step 3: Feature engineering ───────────────────────────────────
        if not MasterPipeline._run_step(
            context, "features", FeaturePipeline.run
        ):
            return MasterPipeline._finalize(context)

        # ── Step 4: Market regime detection ───────────────────────────────
        # Non-blocking: regime failure is a warning, not a hard stop.
        # Signals still run; regime metadata is simply absent for this run.
        logger.info("── Step [regime] starting")
        try:
            regime_result = RegimePipeline.run()
            if regime_result is not None:
                context.cache_object("regime", regime_result)
                context.set_metric("regime_status", regime_result.market_status)
                context.set_metric("regime_score", regime_result.regime_score)
                context.mark_step_completed("regime")
                logger.info(
                    "── Step [regime] completed | %s (score=%.0f)",
                    regime_result.market_status,
                    regime_result.regime_score,
                )
            else:
                logger.warning("── Step [regime] returned no result — continuing")
                context.add_warning("Regime detection returned no result")
                context.mark_step_completed("regime")

        except Exception as exc:
            logger.warning("── Step [regime] failed (non-blocking): %s", exc)
            context.add_warning(f"Regime step failed: {exc}")
            context.mark_step_completed("regime")   # non-blocking

        # ── Step 5: Signal generation ─────────────────────────────────────
        # Pass regime market_status so SignalPipeline can apply the regime gate
        # (suppress breakout/VCP signals in BEAR; suppress all in cash_mode).
        cached_regime = context.get_cache_object("regime")
        market_status = cached_regime.market_status if cached_regime is not None else None

        if not MasterPipeline._run_step(
            context,
            "signals",
            lambda: SignalPipeline.run(market_status=market_status),
        ):
            return MasterPipeline._finalize(context)

        # ── Step 6: Sector momentum (non-blocking) ────────────────────────
        # Consumes stock_signals produced in Step 4 — must run after signals.
        # A scoring failure never aborts the overall pipeline.
        logger.info("── Step [sector_momentum] starting")
        try:
            SectorMomentum.run()
            context.mark_step_completed("sector_momentum")
            logger.info("── Step [sector_momentum] completed")
        except Exception as exc:
            logger.warning("── Step [sector_momentum] failed (non-blocking): %s", exc)
            context.add_warning(f"Sector momentum step failed: {exc}")
            context.mark_step_completed("sector_momentum")

        return MasterPipeline._finalize(context)

    @staticmethod
    def _finalize(context: PipelineContext) -> PipelineContext:
        """Finalize context and persist the audit record."""

        context.finalize()

        logger.info(
            "═══ Master pipeline %s | execution_id=%s | steps=%s ═══",
            context.pipeline_status,
            context.execution_id,
            context.completed_steps,
        )

        PipelineMonitor.record_end(context)

        return context


if __name__ == "__main__":
    result = MasterPipeline.run()
    sys.exit(0 if result.pipeline_status == "SUCCESS" else 1)
