"""Project-local audio references and ordinary, bounded background text."""

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .context_values import validate_text
from .limits import MAX_BATCH_AUDIO_ITEMS
from .project import AudioAsset
from .schema import require_schema_version

AudioGroupMode = Literal["independent", "shared", "continuous"]


class AudioGroupValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200, strict=True)
    audio_ids: list[Annotated[str, Field(strict=True)]] = Field(
        default_factory=list, max_length=MAX_BATCH_AUDIO_ITEMS, strict=True
    )
    background: str = Field(default="", strict=True)
    mode: AudioGroupMode = "independent"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("组名称不能为空或包含控制字符")
        validate_text(value)
        return value

    @field_validator("background")
    @classmethod
    def bounded_background(cls, value):
        return validate_text(value)

    @field_validator("audio_ids")
    @classmethod
    def unique_members(cls, values):
        for value in values:
            AudioAsset.validate_id(value)
        if len(set(values)) != len(values):
            raise ValueError("组内音频不能重复")
        return values


class AudioGroup(AudioGroupValues):
    schema_version: str = "1.0"
    id: str = Field(pattern=r"^grp_[0-9a-f]{32}$", strict=True)
    revision: int = Field(ge=1, strict=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value):
        return require_schema_version(value, "audio group")

    @model_validator(mode="after")
    def valid_timestamps(self):
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.created_at, self.updated_at)
        ):
            raise ValueError("组时间必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("组修改时间不能早于创建时间")
        return self


class AudioGroupSummary(BaseModel):
    id: str
    name: str
    revision: int
    mode: AudioGroupMode
    audio_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_group(cls, group):
        return cls(
            **group.model_dump(exclude={"schema_version", "audio_ids", "background"}),
            audio_count=len(group.audio_ids),
        )
