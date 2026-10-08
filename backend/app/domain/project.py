from datetime import datetime, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, Field, field_validator, model_validator


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
        if (
            parsed.is_absolute()
            or not parsed.parts
            or parsed.parts == (".",)
            or ".." in parsed.parts
        ):
            raise ValueError("source path must remain inside the project")
        return value


PRIMARY_AUDIO_ID = "aud_primary"


class AudioAsset(BaseModel):
    id: str
    source: SourceAsset
    name: str | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("invalid audio id")
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
    audios: list[AudioAsset] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_audios(self):
        # Old project files gain a virtual primary resource without moving files.
        primary = AudioAsset(id=PRIMARY_AUDIO_ID, source=self.source)
        extras = [audio for audio in self.audios if audio.id != PRIMARY_AUDIO_ID]
        if len({audio.id for audio in extras}) != len(extras):
            raise ValueError("duplicate audio id")
        self.audios = [primary, *extras]
        return self
