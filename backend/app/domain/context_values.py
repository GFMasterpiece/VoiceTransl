"""Immutable, bounded values for composition and processor execution."""

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .node_execution import NodeExecutionError

MAX_JSON_BYTES = 16 * 1024
MAX_TEXT_BYTES = 64 * 1024


def validate_text(value, *, limit=MAX_TEXT_BYTES):
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("文本超出大小限制或不是合法Unicode文本") from None
    return value


def validate_language(value):
    if (
        not isinstance(value, str)
        or len(value) > 32
        or value != value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("语言代码无效")
    validate_text(value)
    return value


def canonical_object(value):
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 8 or count > 2048:
            raise ValueError("JSON对象过深或包含过多内容")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON对象键必须是字符串")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError("JSON对象包含不支持的值")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("JSON对象不能包含非有限数值")

    if not isinstance(value, dict):
        raise ValueError("需要JSON对象")
    visit(value, 0)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("JSON对象超出16 KiB限制或不是合法JSON") from None
    return encoded


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON对象包含重复键")
        result[key] = value
    return result


def _canonical_encoded(value):
    if not isinstance(value, str) or len(value) > MAX_JSON_BYTES:
        raise ValueError("JSON对象超出大小限制")
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_pairs)
        return canonical_object(decoded)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("JSON对象无效") from None


class _Packet(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )
    version: Literal["1"] = "1"


class JSONValue(_Packet):
    value_json: str

    @field_validator("value_json")
    @classmethod
    def validate_encoded(cls, value):
        return _canonical_encoded(value)

    @classmethod
    def from_object(cls, value):
        return cls(value_json=canonical_object(value))

    @property
    def value(self):
        return json.loads(self.value_json)


class TranslationOptionsFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    batch_size: int = Field(default=20, ge=1, le=100)
    max_batch_characters: int = Field(default=6000, ge=256, le=30000)
    context_segments: int = Field(default=2, ge=0, le=10)
    max_attempts: int = Field(default=2, ge=1, le=5)


class TranslationOptions(TranslationOptionsFields, _Packet):
    pass


class ASROptionsFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    timeout_seconds: int = Field(default=3600, ge=1, le=3600)


class ASROptions(ASROptionsFields, _Packet):
    pass


CONFIGURATION_TYPES = {
    "json_object": JSONValue,
    "translation_options": TranslationOptions,
    "asr_options": ASROptions,
}


def normalize_processor_config(config):
    if not isinstance(config, dict) or config:
        raise ValueError("处理节点不接受内联配置，请连接独立的配置节点")
    return {}


def optional_packet(context, port, packet_type):
    if port not in context.inputs:
        return packet_type()
    value = context.input(port).value
    if not isinstance(value, packet_type):
        raise NodeExecutionError("CONFIGURATION_VALUE_INVALID", "配置输入类型无效")
    try:
        return packet_type.model_validate(value)
    except ValueError:
        raise NodeExecutionError("CONFIGURATION_VALUE_INVALID", "配置输入内容无效") from None


def resolve_audio_language(context):
    audio = next((item for item in context.project.audios if item.id == context.run.audio_id), None)
    language = audio.source.language if audio else ""
    try:
        if not language:
            raise ValueError
        return validate_language(language)
    except ValueError:
        raise NodeExecutionError("ASR_LANGUAGE_REQUIRED", "请在当前音频中声明语言") from None
