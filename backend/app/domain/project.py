import re
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schema import require_schema_version


def _validate_origin_component(value: str) -> str:
    if (
        not value
        or len(value) > 255
        or value != value.strip()
        or value in {".", ".."}
        or value.endswith(".")
        or any(char in '/\\<>:"|?*' or unicodedata.category(char) == "Cc" for char in value)
        or value.split(".", 1)[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise ValueError("origin names must be safe single directory or file names")
    return value


class SourceOrigin(BaseModel):
    """Validated display metadata, never a local filesystem location."""

    model_config = ConfigDict(extra="forbid", strict=True)

    root_id: str = Field(min_length=1, max_length=128)
    root_name: str = Field(min_length=1, max_length=255)
    relative_path: str = Field(min_length=1, max_length=4096)

    @field_validator("root_id")
    @classmethod
    def validate_root_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
            raise ValueError("origin root_id must be a safe identifier")
        return value

    @field_validator("root_name")
    @classmethod
    def validate_root_name(cls, value: str) -> str:
        return _validate_origin_component(value)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        # Split before PurePosixPath can normalize away empty and dot segments.
        for part in value.split("/"):
            _validate_origin_component(part)
        return value


class SourceAsset(BaseModel):
    path: str
    original_name: str
    media_type: str | None = None
    size_bytes: int = Field(ge=0)
    language: str = "ja"
    origin: SourceOrigin | None = None

    @model_validator(mode="after")
    def validate_origin_filename(self):
        if self.origin is not None and (
            PurePosixPath(self.origin.relative_path).name != self.original_name
        ):
            raise ValueError("origin relative_path basename must match original_name")
        return self

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
    source: SourceAsset | None = None
    source_language: str | None = None
    target_language: str | None = None
    preset: str = "mock"
    metadata: dict[str, object] = Field(default_factory=dict)
    audios: list[AudioAsset] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return require_schema_version(value, "project")

    @model_validator(mode="after")
    def normalize_audios(self):
        # Old project files gain a virtual primary resource without moving files.
        if self.source is None:
            if len({audio.id for audio in self.audios}) != len(self.audios):
                raise ValueError("duplicate audio id")
            return self
        primary = AudioAsset(id=PRIMARY_AUDIO_ID, source=self.source)
        extras = [audio for audio in self.audios if audio.id != PRIMARY_AUDIO_ID]
        if len({audio.id for audio in extras}) != len(extras):
            raise ValueError("duplicate audio id")
        self.audios = [primary, *extras]
        return self
