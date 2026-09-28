from datetime import datetime, timezone
from pydantic import BaseModel, Field, model_validator

class Segment(BaseModel):
    id: str
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)
    source_text: str
    normalized_text: str | None = None
    translated_text: str | None = None
    language: str = "ja"
    channel: str | None = None
    speaker: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.start is not None and self.end is not None and self.end < self.start: raise ValueError("segment end must be >= start")
        return self

class Transcript(BaseModel):
    schema_version: str = "1.0"
    project_id: str
    stage: str
    language: str
    duration: float | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    segments: list[Segment] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)
