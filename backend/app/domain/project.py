from datetime import datetime, timezone
from pathlib import PurePosixPath
from pydantic import BaseModel, Field
from pydantic import field_validator

class SourceAsset(BaseModel):
    path: str
    original_name: str
    media_type: str | None = None
    size_bytes: int = Field(ge=0)
    language: str = "ja"

    @field_validator("path")
    @classmethod
    def validate_project_relative_path(cls, value: str) -> str:
        # Persisted asset paths are always POSIX paths relative to the
        # project directory.  Reject Windows separators and traversal before
        # a pipeline stage combines the value with a local filesystem path.
        if not value or "\\" in value:
            raise ValueError("source path must be a relative POSIX path")
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or not parsed.parts or parsed.parts == (".",) or ".." in parsed.parts:
            raise ValueError("source path must remain inside the project")
        return value

class Project(BaseModel):
    schema_version: str = "1.0"
    id: str
    name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: SourceAsset
    preset: str = "mock"
    metadata: dict[str, object] = Field(default_factory=dict)
