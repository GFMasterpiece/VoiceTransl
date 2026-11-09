"""Immutable, bounded values for composition and processor execution."""

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..adapters.llm_translation_protocol import validate_glossary
from .node_execution import NodeExecutionError

MAX_JSON_BYTES = 16 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_INSTRUCTIONS_CHARACTERS = 8192


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


class TranslationContext(_Packet):
    source_language: str = ""
    target_language: str = ""
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARACTERS)
    background: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARACTERS)
    glossary_json: str = "{}"

    @field_validator("source_language", "target_language")
    @classmethod
    def validate_languages(cls, value):
        return validate_language(value)

    @field_validator("glossary_json")
    @classmethod
    def validate_terms(cls, value):
        encoded = _canonical_encoded(value)
        validate_glossary(json.loads(encoded))
        return encoded

    @model_validator(mode="after")
    def validate_rendered(self):
        rendered = self.render_instructions()
        validate_text(rendered, limit=32 * 1024)
        if len(rendered) > MAX_INSTRUCTIONS_CHARACTERS:
            raise ValueError("背景与翻译要求合成后不能超过8192字符")
        return self

    @classmethod
    def from_values(cls, *, glossary=None, **values):
        return cls(glossary_json=canonical_object({} if glossary is None else glossary), **values)

    @property
    def glossary(self):
        return json.loads(self.glossary_json)

    def render_instructions(self):
        if not self.background:
            return self.instructions
        parts = ["[Background]\n" + self.background]
        if self.instructions:
            parts.append("[Translation instructions]\n" + self.instructions)
        return "\n\n".join(parts)


class ASRContext(_Packet):
    language: str = ""

    @field_validator("language")
    @classmethod
    def validate_language_code(cls, value):
        return validate_language(value)


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
    "translation_context": TranslationContext,
    "asr_context": ASRContext,
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


def resolve_audio_language(context, packet):
    audio = next((item for item in context.project.audios if item.id == context.run.audio_id), None)
    language = packet.language or (audio.source.language if audio else "")
    try:
        if not language:
            raise ValueError
        return validate_language(language)
    except ValueError:
        raise NodeExecutionError(
            "ASR_LANGUAGE_REQUIRED", "请在识别上下文或当前音频中声明语言"
        ) from None
