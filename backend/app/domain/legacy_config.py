import json
import math
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schema import require_schema_version


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class LegacyPreprocessConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    sample_rate: int = Field(default=16_000, ge=8_000, le=192_000)
    channels: int = Field(default=1, ge=1, le=2)
    preserve_original: bool = True


class LegacyASRConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    provider_profile_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$",
    )
    backend: str = Field(default="mock", min_length=1, max_length=64)
    model: str = Field(default="mock", min_length=1, max_length=128)
    language: str = Field(default="ja", min_length=1, max_length=32)
    vad_enabled: bool = True
    chunk_seconds: int = Field(default=30, ge=1, le=600)


class LegacyAlignmentConfig(_ConfigModel):
    enabled: bool = True
    provider: str = Field(default="mock", min_length=1, max_length=64)
    model: str = Field(default="mock", min_length=1, max_length=128)
    fallback: str = Field(default="keep_asr_timestamps", min_length=1, max_length=64)


MAX_ADDITIONAL_PARAMETER_DEPTH = 4
MAX_ADDITIONAL_PARAMETER_ITEMS = 256
MAX_ADDITIONAL_PARAMETER_BYTES = 65_536
PROTECTED_TRANSLATION_PARAMETERS = frozenset(
    {
        "model",
        "messages",
        "input",
        "system",
        "url",
        "auth",
        "authorization",
        "api_key",
        "api-key",
        "x-api-key",
        "content-type",
        "anthropic-version",
        "base_url",
        "api_key_env",
        "headers",
        "endpoint",
        "token",
    }
)


def _validate_additional_parameter(
    value: object, *, depth: int = 0, items: list[int] | None = None
) -> None:
    if items is None:
        items = [0]
    if depth > MAX_ADDITIONAL_PARAMETER_DEPTH:
        raise ValueError("translation additional_parameters nesting is too deep")
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("translation additional_parameters must contain finite numbers")
        return
    if isinstance(value, list):
        for item in value:
            items[0] += 1
            if items[0] > MAX_ADDITIONAL_PARAMETER_ITEMS:
                raise ValueError("translation additional_parameters is too large")
            _validate_additional_parameter(item, depth=depth + 1, items=items)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("translation additional_parameters keys must be non-empty strings")
            items[0] += 1
            if items[0] > MAX_ADDITIONAL_PARAMETER_ITEMS:
                raise ValueError("translation additional_parameters is too large")
            _validate_additional_parameter(item, depth=depth + 1, items=items)
        return
    raise ValueError("translation additional_parameters must be JSON-compatible")


def validate_translation_parameters(value: dict[str, object]) -> None:
    if not isinstance(value, dict):
        raise ValueError("translation additional_parameters must be an object")
    _validate_additional_parameter(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if len(encoded) > MAX_ADDITIONAL_PARAMETER_BYTES:
        raise ValueError("translation additional_parameters is too large")
    for key in value:
        folded = key.casefold()
        sensitive = {"secret", "password", "credential", "api_key", "access_key", "private_key"}
        if folded in PROTECTED_TRANSLATION_PARAMETERS or folded in sensitive:
            raise ValueError(f"translation additional_parameters cannot set protected field: {key}")


def merge_translation_parameters(core: dict, parameters: dict[str, object]) -> dict:
    validate_translation_parameters(parameters)
    merged = {**parameters, **core}
    validate_translation_parameters(
        {key: value for key, value in merged.items() if key not in core}
    )
    return merged


class LegacyTranslationConfig(_ConfigModel):
    provider: str = Field(default="mock", min_length=1, max_length=64)
    base_url: str | None = Field(default=None, max_length=2_048)
    api_key_env: str | None = Field(default=None, min_length=1, max_length=128)
    model: str = Field(default="mock", min_length=1, max_length=128)
    target_language: str = Field(default="zh-CN", min_length=1, max_length=32)
    batch_size: int = Field(default=8, ge=1, le=128)
    additional_parameters: dict[str, object] = Field(default_factory=dict)
    temperature: float | None = Field(default=None, ge=0, le=2)
    glossary: dict[str, str] = Field(default_factory=dict)
    instructions: str | None = Field(default=None, max_length=4_000)
    timeout_seconds: int = Field(default=120, ge=1, le=3_600)

    @field_validator("additional_parameters")
    @classmethod
    def validate_additional_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        _validate_additional_parameter(value)
        validate_translation_parameters(value)
        return value


EXPORT_FORMATS = frozenset({"srt", "vtt", "lrc"})
EXPORT_NAMING_MODES = ("language", "original")
EXPORT_TEMPLATE_VARIABLES = frozenset({"source_language", "target_language", "kind", "format"})


class LegacyExportConfig(_ConfigModel):
    formats: list[str] = Field(default_factory=lambda: ["srt", "lrc"], min_length=1)
    bilingual: bool = True
    naming: Literal["language", "original"] = "language"
    filename_template: str | None = Field(default=None, min_length=1, max_length=128)
    filename_stem: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, value: list[str]) -> list[str]:
        normalized = [item.lower().lstrip(".") for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("export formats must not contain duplicates")
        unsupported = sorted(set(normalized) - EXPORT_FORMATS)
        if unsupported:
            raise ValueError(f"unsupported export format: {unsupported[0]}")
        return normalized

    @field_validator("filename_template")
    @classmethod
    def validate_filename_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or "/" in value or "\\" in value:
            raise ValueError("export filename_template must be a safe filename")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("export filename_template contains control characters")
        for match in re.finditer(r"\{([^{}]+)\}", value):
            if match.group(1) not in EXPORT_TEMPLATE_VARIABLES:
                raise ValueError(
                    f"export filename_template has unsupported variable: {match.group(1)}"
                )
        if re.search(r"[{}]", re.sub(r"\{[^{}]+\}", "", value)):
            raise ValueError("export filename_template has malformed placeholders")
        if "{source_language}" not in value and "{target_language}" not in value:
            raise ValueError("export filename_template must include a language variable")
        if "{kind}" not in value:
            raise ValueError("export filename_template must include {kind} to separate outputs")
        return value

    @field_validator("filename_stem")
    @classmethod
    def validate_filename_stem(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if (
            value != value.strip()
            or value in {".", ".."}
            or value.endswith(".")
            or "/" in value
            or "\\" in value
            or any(char in '<>:"|?*' for char in value)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("export filename_stem is unsafe")
        return value


class LegacyPipelineConfig(_ConfigModel):
    schema_version: str = "1.0"
    source_language: str = Field(default="ja", min_length=1, max_length=32)
    target_language: str = Field(default="zh-CN", min_length=1, max_length=32)
    preprocess: LegacyPreprocessConfig = Field(default_factory=LegacyPreprocessConfig)
    asr: LegacyASRConfig = Field(default_factory=LegacyASRConfig)
    alignment: LegacyAlignmentConfig = Field(default_factory=LegacyAlignmentConfig)
    translation: LegacyTranslationConfig = Field(default_factory=LegacyTranslationConfig)
    export: LegacyExportConfig = Field(default_factory=LegacyExportConfig)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return require_schema_version(value, "pipeline config")

    @model_validator(mode="after")
    def validate_languages(self) -> Self:
        if self.source_language.casefold() == self.target_language.casefold():
            raise ValueError("source and target languages must differ")
        if self.asr.language != self.source_language:
            raise ValueError("asr language must match source language")
        if self.translation.target_language != self.target_language:
            raise ValueError("translation target language must match target language")
        if self.translation.api_key_env and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", self.translation.api_key_env
        ):
            raise ValueError("translation api_key_env must be an environment variable name")
        return self


def default_legacy_pipeline_config(
    source_language: str = "ja", *, target_language: str | None = None
) -> LegacyPipelineConfig:
    target = target_language or ("zh-CN" if source_language.casefold() != "zh" else "en")
    return LegacyPipelineConfig(
        source_language=source_language,
        target_language=target,
        asr=LegacyASRConfig(language=source_language),
        translation=LegacyTranslationConfig(target_language=target),
    )


__all__ = [
    "EXPORT_FORMATS",
    "EXPORT_NAMING_MODES",
    "EXPORT_TEMPLATE_VARIABLES",
    "LegacyASRConfig",
    "LegacyAlignmentConfig",
    "LegacyExportConfig",
    "LegacyPipelineConfig",
    "LegacyPreprocessConfig",
    "LegacyTranslationConfig",
    "default_legacy_pipeline_config",
]
