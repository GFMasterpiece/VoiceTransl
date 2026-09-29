from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreprocessConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    sample_rate: int = Field(default=16_000, ge=8_000, le=192_000)
    channels: int = Field(default=1, ge=1, le=2)
    preserve_original: bool = True


class ASRConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    # CrispASR backend is a safe identifier selected from the server's
    # supported capability list. It is never interpreted as a command or path.
    backend: str = Field(default="qwen3-1.7b", min_length=1, max_length=64)
    model: str = Field(default="mock", min_length=1, max_length=128)
    language: str = Field(default="ja", min_length=1, max_length=32)
    vad_enabled: bool = True
    chunk_seconds: int = Field(default=30, ge=1, le=600)


class AlignmentConfig(_ConfigModel):
    enabled: bool = True
    provider: str = Field(default="mock", min_length=1, max_length=64)
    model: str = Field(default="mock", min_length=1, max_length=128)
    fallback: str = Field(default="keep_asr_timestamps", min_length=1, max_length=64)


class TranslationConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    model: str = Field(default="mock", min_length=1, max_length=128)
    target_language: str = Field(default="zh-CN", min_length=1, max_length=32)
    batch_size: int = Field(default=8, ge=1, le=128)
    temperature: float = Field(default=0.2, ge=0, le=2)
    glossary: dict[str, str] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, max_length=4_000)


class ExportConfig(_ConfigModel):
    formats: list[str] = Field(default_factory=lambda: ["srt", "lrc"], min_length=1)
    bilingual: bool = True
    naming: str = Field(default="language", min_length=1, max_length=64)

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, value: list[str]) -> list[str]:
        supported = {"srt", "vtt", "lrc"}
        normalized = [item.lower().lstrip(".") for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("export formats must not contain duplicates")
        unsupported = sorted(set(normalized) - supported)
        if unsupported:
            raise ValueError(f"unsupported export format: {unsupported[0]}")
        return normalized


class PipelineConfig(_ConfigModel):
    schema_version: str = "1.0"
    source_language: str = Field(default="ja", min_length=1, max_length=32)
    target_language: str = Field(default="zh-CN", min_length=1, max_length=32)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    @model_validator(mode="after")
    def validate_languages(self) -> Self:
        if self.preprocess.provider not in {"mock", "ffmpeg"}:
            raise ValueError("unsupported preprocess provider")
        if self.asr.provider not in {"mock", "crispasr"}:
            raise ValueError("unsupported asr provider")
        if self.asr.provider == "crispasr" and self.preprocess.provider != "ffmpeg":
            raise ValueError("crispasr ASR requires ffmpeg preprocessing for normalized WAV input")
        if self.source_language.casefold() == self.target_language.casefold():
            raise ValueError("source and target languages must differ")
        if self.asr.language != self.source_language:
            raise ValueError("asr language must match source language")
        if self.translation.target_language != self.target_language:
            raise ValueError("translation target language must match target language")
        return self


def default_pipeline_config(source_language: str = "ja") -> PipelineConfig:
    """Return a safe mock configuration for new and legacy projects."""

    return PipelineConfig(
        source_language=source_language,
        target_language="zh-CN" if source_language.casefold() != "zh" else "en",
        asr=ASRConfig(language=source_language),
        translation=TranslationConfig(
            target_language="zh-CN" if source_language.casefold() != "zh" else "en"
        ),
    )


__all__ = [
    "ASRConfig",
    "AlignmentConfig",
    "ExportConfig",
    "PipelineConfig",
    "PreprocessConfig",
    "TranslationConfig",
    "default_pipeline_config",
]
