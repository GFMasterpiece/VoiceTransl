from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from .enums import JobStatus, PipelineStageName, StageStatus
from .legacy_config import LegacyPipelineConfig
from .project import PRIMARY_AUDIO_ID
from .provider_profile import ProviderProfile
from .schema import require_schema_version


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
    audio_id: str = PRIMARY_AUDIO_ID
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
    config_snapshot: LegacyPipelineConfig | None = None
    # Resolved, secret-free provider profiles used by this run.
    provider_profiles_snapshot: dict[str, ProviderProfile] = Field(default_factory=dict)
    # Local capability identities are captured at queue time. Runtime paths
    # remain external settings and are deliberately not persisted here.
    asr_backend_ids: list[str] = Field(default_factory=list)
    asr_capabilities: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return require_schema_version(value, "job")
