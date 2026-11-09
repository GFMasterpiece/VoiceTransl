from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .node_constants import DEFAULT_FILENAME_STEM, DEFAULT_TEXT_FILENAME_STEM, DEFAULT_TEXT_FORMAT


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class MockPreprocessNodeConfig(_ConfigModel):
    provider: Literal["mock"] = "mock"


class MockASRNodeConfig(_ConfigModel):
    provider: Literal["mock"] = "mock"
    language: str = Field(
        default="ja",
        min_length=1,
        max_length=32,
        json_schema_extra={"suggestion_source": "languages"},
    )


class AudioOutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    filename_stem: str = Field(
        default=DEFAULT_FILENAME_STEM, min_length=1, max_length=128, description="名称 / 前缀"
    )
    naming_mode: Literal["exact", "prefix_sequence"] = Field(
        default="prefix_sequence",
        description="前缀模式附加固定文件序号，例如 prefix_00001；完整名称模式不附加序号",
        json_schema_extra={"creation_default": "prefix_sequence"},
    )

    @model_validator(mode="after")
    def validate_filename_stem(self):
        valid, message = valid_filename_stem(self.filename_stem)
        if not valid:
            raise ValueError(message or "filename_stem 无效")
        return self


class TextOutputConfig(AudioOutputConfig):
    filename_stem: str = Field(
        default=DEFAULT_TEXT_FILENAME_STEM, min_length=1, max_length=128, description="名称 / 前缀"
    )
    format: Literal["txt", "srt", "vtt", "lrc"] = DEFAULT_TEXT_FORMAT


def validate_asr_config(config: object) -> MockASRNodeConfig:
    """Validate only the mock ASR settings supported by workflow nodes."""

    if not isinstance(config, dict):
        raise ValueError("asr 配置必须是对象且不能为 null")
    result = MockASRNodeConfig.model_validate(config, strict=True)
    # Language is independent from the audio's language, but surrounding
    # whitespace and control characters are never valid identifiers.
    if (
        not result.language
        or result.language != result.language.strip()
        or len(result.language) > 32
        or any(ord(char) < 32 or ord(char) == 127 for char in result.language)
    ):
        raise ValueError("asr.language 必须非空、无首尾空白且不包含控制字符")
    return result


def validate_text_output_config(config: object) -> TextOutputConfig:
    if not isinstance(config, dict):
        raise ValueError("text_output 配置必须是对象且不能为 null")
    return TextOutputConfig.model_validate(config, strict=True)


def valid_filename_stem(value: object) -> tuple[bool, str | None]:
    if not isinstance(value, str):
        return False, "filename_stem 必须是字符串"
    if not value or value != value.strip():
        return False, "filename_stem 不能为空或包含首尾空白"
    if len(value) > 128:
        return False, "filename_stem 不能超过 128 个字符"
    if value in {".", ".."} or value.endswith("."):
        return False, "filename_stem 不能是 . 或 ..，也不能以点结尾"
    windows_basename = value.split(".", 1)[0].upper()
    if windows_basename in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        return False, "filename_stem 不能使用 Windows 保留名称"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False, "filename_stem 不能包含控制字符"
    if any(char in '/\\<>:"|?*' for char in value):
        return False, "filename_stem 不能包含路径分隔符或危险字符"
    return True, None


def normalize_empty_config(config: dict[str, object]) -> dict[str, object]:
    if config:
        raise ValueError("config 必须为空对象")
    return config


def normalize_input_config(config: dict[str, object]) -> dict[str, object]:
    from .limits import MAX_BATCH_AUDIO_ITEMS
    from .project import AudioAsset

    if set(config) - {"audio_id", "audio_ids"}:
        raise ValueError("audio_input 仅接受 audio_id 或 audio_ids")
    if "audio_id" in config and "audio_ids" in config:
        raise ValueError("audio_id 与 audio_ids 不能同时设置")
    if "audio_id" in config:
        value = config["audio_id"]
        if not isinstance(value, str):
            raise ValueError("audio_id 必须是字符串")
        AudioAsset.validate_id(value)
    if "audio_ids" in config:
        values = config["audio_ids"]
        if not isinstance(values, list) or len(values) > MAX_BATCH_AUDIO_ITEMS:
            raise ValueError(f"audio_ids 必须是最多 {MAX_BATCH_AUDIO_ITEMS} 项的数组")
        for value in values:
            if not isinstance(value, str):
                raise ValueError("audio_ids 中每一项必须是字符串")
            AudioAsset.validate_id(value)
        if len(set(values)) != len(values):
            raise ValueError("audio_ids 不能重复")
    return dict(config)
