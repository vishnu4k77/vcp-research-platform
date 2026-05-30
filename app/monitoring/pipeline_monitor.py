from datetime import date, datetime

from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.pipeline.pipeline_context import PipelineContext

logger = get_logger(__name__)


class PipelineMonitor:
    """
    Persists pipeline execution records to the pipeline_runs table.

    Call record_start() immediately after PipelineContext is created.
    Call record_end() after context.finalize() is called.

    Failures here are non-fatal: monitoring should never abort the pipeline.
    """

    @staticmethod
    def record_start(context: PipelineContext) -> None:
        """INSERT a RUNNING record at pipeline start."""

        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO pipeline_runs (
                            run_id,
                            pipeline_name,
                            trade_date,
                            start_time,
                            end_time,
                            status,
                            rows_processed,
                            error_message
                        ) VALUES (
                            :run_id,
                            :pipeline_name,
                            :trade_date,
                            :start_time,
                            NULL,
                            'RUNNING',
                            0,
                            NULL
                        )
                    """),
                    {
                        "run_id": context.execution_id,
                        "pipeline_name": context.pipeline_name,
                        "trade_date": date.today(),
                        "start_time": context.started_at,
                    },
                )

            logger.debug("Pipeline run record created: %s", context.execution_id)

        except Exception as exc:
            logger.warning("record_start failed (non-fatal): %s", exc)

    @staticmethod
    def record_end(context: PipelineContext) -> None:
        """UPDATE the record with final status, end_time, and error info."""

        error_message = None
        if context.failed_steps:
            error_message = f"Failed steps: {', '.join(context.failed_steps)}"
            if context.warnings:
                error_message += f" | Warnings: {'; '.join(context.warnings[:5])}"

        rows_processed = int(context.metrics.get("rows_processed", 0))

        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE pipeline_runs
                        SET
                            end_time       = :end_time,
                            status         = :status,
                            rows_processed = :rows,
                            error_message  = :error_message
                        WHERE run_id = :run_id
                    """),
                    {
                        "end_time": context.completed_at or datetime.utcnow(),
                        "status": context.pipeline_status,
                        "rows": rows_processed,
                        "error_message": error_message,
                        "run_id": context.execution_id,
                    },
                )

            logger.debug(
                "Pipeline run updated: %s → %s",
                context.execution_id,
                context.pipeline_status,
            )

        except Exception as exc:
            logger.warning("record_end failed (non-fatal): %s", exc)
