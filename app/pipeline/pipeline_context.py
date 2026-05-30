from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Any
import uuid


@dataclass
class PipelineContext:

    execution_id: str = field(
        default_factory=lambda: str(uuid.uuid4())
    )

    pipeline_name: str = "master_pipeline"

    started_at: datetime = field(
        default_factory=datetime.utcnow
    )

    completed_at: datetime = None

    pipeline_status: str = "RUNNING"

    completed_steps: List[str] = field(
        default_factory=list
    )

    failed_steps: List[str] = field(
        default_factory=list
    )

    warnings: List[str] = field(
        default_factory=list
    )

    metrics: Dict[str, Any] = field(
        default_factory=dict
    )

    runtime_cache: Dict[str, Any] = field(
        default_factory=dict
    )

    def mark_step_completed(self, step_name: str):

        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)

    def mark_step_failed(self, step_name: str):

        if step_name not in self.failed_steps:
            self.failed_steps.append(step_name)

        self.pipeline_status = "FAILED"

    def add_warning(self, warning: str):

        self.warnings.append(warning)

    def set_metric(self, key: str, value: Any):

        self.metrics[key] = value

    def cache_object(self, key: str, value: Any):

        self.runtime_cache[key] = value

    def get_cache_object(self, key: str):

        return self.runtime_cache.get(key)

    def finalize(self):

        self.completed_at = datetime.utcnow()

        if not self.failed_steps:
            self.pipeline_status = "SUCCESS"

    def to_dict(self):

        return {
            "execution_id": self.execution_id,
            "pipeline_name": self.pipeline_name,
            "started_at": str(self.started_at),
            "completed_at": str(self.completed_at),
            "pipeline_status": self.pipeline_status,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "warnings": self.warnings,
            "metrics": self.metrics
        }