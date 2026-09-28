from datetime import datetime, timezone
from pydantic import BaseModel, Field

class SourceAsset(BaseModel):
    path: str
    original_name: str
    media_type: str | None = None
    size_bytes: int = Field(ge=0)
    language: str = "ja"

class Project(BaseModel):
    schema_version: str = "1.0"
    id: str
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: SourceAsset
    preset: str = "mock"
    metadata: dict[str, object] = Field(default_factory=dict)
