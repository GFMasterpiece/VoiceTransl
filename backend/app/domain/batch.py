from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .limits import MAX_BATCH_AUDIO_ITEMS
from .project import AudioAsset, SourceOrigin
from .schema import CONFIGURATION_SCHEMA_VERSION as SCHEMA_VERSION
from .schema import require_schema_version
from .workflow import WorkflowRunError, WorkflowSnapshot

ItemStatus = Literal[
    "queued", "running", "completed", "partial", "failed", "cancelled", "interrupted"
]
BatchStatus = Literal[
    "queued", "running", "cancelling", "completed", "partial", "failed", "cancelled", "interrupted"
]
ACTIVE_BATCH_STATUSES = {"queued", "running", "cancelling"}
ACTIVE_ITEM_STATUSES = {"queued", "running"}
ITEM_STATUSES = ("queued", "running", "completed", "partial", "failed", "cancelled", "interrupted")


def item_counts(items):
    counts = dict.fromkeys(ITEM_STATUSES, 0)
    for item in items:
        counts[item.status] += 1
    return counts


def now():
    return datetime.now(timezone.utc)


class BatchItem(BaseModel):
    index: int = Field(ge=0)
    sequence: int = Field(ge=1, le=MAX_BATCH_AUDIO_ITEMS, strict=True)
    audio_id: str
    audio_name: str
    source_language: str
    origin: SourceOrigin | None = None
    status: ItemStatus = "queued"
    run_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: WorkflowRunError | None = None

    @field_validator("audio_id")
    @classmethod
    def valid_audio(cls, value):
        return AudioAsset.validate_id(value)

    @model_validator(mode="after")
    def metadata(self):
        if self.origin and PurePosixPath(self.origin.relative_path).name != self.audio_name:
            raise ValueError("origin filename does not match frozen audio name")
        if self.run_id is not None:
            import re

            if not re.fullmatch(r"wfr_[0-9a-f]{32}", self.run_id):
                raise ValueError("invalid batch workflow run id")
        if self.status == "queued" and self.run_id is not None:
            raise ValueError("queued batch item cannot have a run")
        if self.status in {"completed", "partial"} and self.run_id is None:
            raise ValueError("successful batch item requires a run")
        return self


class BatchSummary(BaseModel):
    id: str
    project_id: str
    status: BatchStatus = "queued"
    workflow_revision: int
    created_at: datetime = Field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    retry_of: str | None = None
    total: int = Field(ge=1, le=MAX_BATCH_AUDIO_ITEMS)
    counts: dict[str, int]
    error: WorkflowRunError | None = None


class BatchDetail(BatchSummary):
    workflow: WorkflowSnapshot


class BatchRecord(BatchDetail):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    request_digest: str
    items: list[BatchItem]

    @field_validator("schema_version")
    @classmethod
    def schema(cls, value):
        return require_schema_version(value, "batch")

    @model_validator(mode="after")
    def consistency(self):
        if self.total != len(self.items) or not self.items:
            raise ValueError("invalid batch total")
        if [item.index for item in self.items] != list(range(self.total)):
            raise ValueError("invalid batch indexes")
        if len({item.audio_id for item in self.items}) != self.total:
            raise ValueError("duplicate batch audio")
        if len({item.sequence for item in self.items}) != self.total:
            raise ValueError("duplicate batch sequence")
        if self.counts != item_counts(self.items):
            raise ValueError("invalid batch counts")
        if self.workflow_revision != self.workflow.workflow_revision:
            raise ValueError("invalid batch workflow revision")
        active = any(item.status in ACTIVE_ITEM_STATUSES for item in self.items)
        if (self.status in ACTIVE_BATCH_STATUSES) != active:
            raise ValueError("batch status does not match active items")
        return self

    def summary(self):
        return BatchSummary(**{name: getattr(self, name) for name in BatchSummary.model_fields})

    def detail(self):
        return BatchDetail(**{name: getattr(self, name) for name in BatchDetail.model_fields})

    def refresh_counts(self):
        self.counts = item_counts(self.items)


class BatchList(BaseModel):
    items: list[BatchSummary]
    total: int
    limit: int
    offset: int


class BatchItemList(BaseModel):
    items: list[BatchItem]
    total: int
    limit: int
    offset: int
