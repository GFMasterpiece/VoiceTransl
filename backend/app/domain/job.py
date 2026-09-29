from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .enums import JobStatus, PipelineStageName, StageStatus
from .pipeline_config import PipelineConfig


class StageState(BaseModel):
    status: StageStatus = StageStatus.PENDING
    progress: float = Field(default=0, ge=0, le=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact: str | None = None
    error: str | None = None


class JobError(BaseModel):
    type: str
    message: str
    stage: PipelineStageName | None = None
    retryable: bool = False


def default_stages() -> dict[PipelineStageName, StageState]:
    return {stage: StageState() for stage in PipelineStageName}


class Job(BaseModel):
    schema_version: str = "1.0"
    id: str
    project_id: str
    status: JobStatus = JobStatus.PENDING
    current_stage: PipelineStageName | None = None
    progress: float = Field(default=0, ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stages: dict[PipelineStageName, StageState] = Field(default_factory=default_stages)
    cancel_requested: bool = False
    error: JobError | None = None
    # The exact non-secret configuration used for this run.  Older jobs do
    # not have this field and remain valid when loaded by Pydantic.
    config_snapshot: PipelineConfig | None = None
