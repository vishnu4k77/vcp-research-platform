import sys
from typing import Optional

from app.config.logging_config import get_logger
from app.monitoring.pipeline_monitor import PipelineMonitor
from app.pipeline.pipeline_context import PipelineContext
from app.main import DataIngestionPipeline
from app.data.earnings_service import EarningsService
from app.data.data_quality_service import DataQualityService
from app.features.feature_pipeline import FeaturePipeline
from app.regime.regime_pipeline import RegimePipeline
from app.signals.signal_pipeline import SignalPipeline
from app.sector.sector_momentum import SectorMomentum
from app.pipeline.pa_pipeline import PAPipeline
from app.pipeline.mtf_pipeline import MTFPipeline

logger = get_logger(__name__)


class MasterPipeline:
    """
    End-to-end EOD pipeline coordinator.

    Execution order:
      1. Ingestion         — pulls incremental OHLCV from Yahoo Finance
      2. Data quality      — stale row detection and gap remediation
      3. Earnings refresh  — refreshes is_earnings_day flag (non-blocking)
      4. Features          — computes all technical indicators → stock_features
      5. Regime            — detects Nifty 50 Bull/Bear/Choppy regime (non-blocking)
      6. Signals           — runs pattern detectors + composite ranker → stock_signals
      7. Sector momentum   — sector RS scores (non-blocking)
      8. Confidence calib  — T1/T2/T3 hit-rate model (non-blocking)
      9. PA pipeline       — price action signals → stock_pa_signals (non-blocking)
     10. MTF pipeline      — weekly/monthly EMA trends → stock_mtf_signals (non-blocking)

    Failure policy: steps 1, 4, 6 are blocking (abort on failure).
    All other steps are non-blocking — failures are logged as warnings and
    the pipeline continues to SUCCESS.

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

        # ── Step 2: Data quality checks + auto-remediation ───────────────
        # Must run BEFORE features so stale rows don't contaminate EMA/ATR.
        # Non-blocking: quality failures are logged but never abort the pipeline.
        logger.info("── Step [data_quality] starting")
        try:
            DataQualityService.run()
            context.mark_step_completed("data_quality")
            logger.info("── Step [data_quality] completed")
        except Exception as exc:
            logger.warning("── Step [data_quality] failed (non-blocking): %s", exc)
            context.add_warning(f"Data quality step failed: {exc}")
            context.mark_step_completed("data_quality")

        # ── Step 3: Earnings dates refresh (non-blocking) ────────────────
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
        logger.info("── Step [sector_momentum] starting")
        try:
            SectorMomentum.run()
            context.mark_step_completed("sector_momentum")
            logger.info("── Step [sector_momentum] completed")
        except Exception as exc:
            logger.warning("── Step [sector_momentum] failed (non-blocking): %s", exc)
            context.add_warning(f"Sector momentum step failed: {exc}")
            context.mark_step_completed("sector_momentum")

        # ── Step 7: Confidence calibration (non-blocking) ─────────────────
        # Builds or refreshes the empirical T1/T2/T3 hit-rate model from
        # 5 years of daily_price_data, then stamps confidence_t1/t2/t3 on
        # today's stock_signals rows.  Rebuilt at most once every 7 days;
        # skipped silently when model is still fresh.
        logger.info("── Step [confidence_calibration] starting")
        try:
            from app.signals.confidence_calibrator import run as _run_confidence
            _run_confidence()
            context.mark_step_completed("confidence_calibration")
            logger.info("── Step [confidence_calibration] completed")
        except Exception as exc:
            logger.warning(
                "── Step [confidence_calibration] failed (non-blocking): %s", exc
            )
            context.add_warning(f"Confidence calibration failed: {exc}")
            context.mark_step_completed("confidence_calibration")

        # ── Step 8: Price Action pipeline (non-blocking) ──────────────────
        # Reads stock_features (ema_50, relative_volume, OHLC) → computes
        # daily + weekly HH/HL structure, volume quality, momentum, extension
        # → writes stock_pa_signals.  Enables PA Trend and PA Score columns
        # in the scanner without any manual script run.
        logger.info("── Step [pa_pipeline] starting")
        try:
            PAPipeline.run()
            context.mark_step_completed("pa_pipeline")
            logger.info("── Step [pa_pipeline] completed")
        except Exception as exc:
            logger.warning("── Step [pa_pipeline] failed (non-blocking): %s", exc)
            context.add_warning(f"PA pipeline failed: {exc}")
            context.mark_step_completed("pa_pipeline")

        # ── Step 9: MTF pipeline (non-blocking) ───────────────────────────
        # Reads stock_features (close_price) → resamples to weekly (W-FRI)
        # and monthly (ME) → computes EMA pairs → writes stock_mtf_signals.
        # Enables W Trend / M Trend / MTF score columns in the scanner and
        # the min_mtf_score backtest filter without any manual script run.
        logger.info("── Step [mtf_pipeline] starting")
        try:
            MTFPipeline.run()
            context.mark_step_completed("mtf_pipeline")
            logger.info("── Step [mtf_pipeline] completed")
        except Exception as exc:
            logger.warning("── Step [mtf_pipeline] failed (non-blocking): %s", exc)
            context.add_warning(f"MTF pipeline failed: {exc}")
            context.mark_step_completed("mtf_pipeline")

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
