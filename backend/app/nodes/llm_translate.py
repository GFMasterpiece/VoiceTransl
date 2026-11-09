"""Node-owned, deterministic API translation of the upstream current text."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..adapters.llm_translation_protocol import (
    validate_additional_parameters,
    validate_base_url,
    validate_glossary,
)
from ..domain.inline_connection import LegacyConnectionConfigError
from ..domain.node_categories import TRANSLATION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult


class LLMTranslateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())
    base_url: str = Field(default="", max_length=2048)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    model: str = Field(default="", max_length=128)
    source_language: str = Field(
        default="", max_length=32, json_schema_extra={"suggestion_source": "languages"}
    )
    target_language: str = Field(
        default="", max_length=32, json_schema_extra={"suggestion_source": "languages"}
    )
    instructions: str = Field(default="", max_length=8192)
    glossary: dict[str, str] = Field(default_factory=dict)
    batch_size: int = Field(default=20, ge=1, le=100)
    max_batch_characters: int = Field(default=6000, ge=256, le=30000)
    context_segments: int = Field(default=2, ge=0, le=10)
    max_attempts: int = Field(default=2, ge=1, le=5)
    additional_parameters: dict[str, object] = Field(default_factory=dict)
    credential_ref: str | None = Field(default=None, max_length=128)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        return validate_base_url(value).rstrip("/") if value else value

    @field_validator("model", "source_language", "target_language")
    @classmethod
    def validate_string(cls, value):
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("模型及语言字段不能包含首尾空白或控制字符")
        return value

    @field_validator("glossary", mode="before")
    @classmethod
    def validate_terms(cls, value):
        return validate_glossary(value)

    @field_validator("additional_parameters", mode="before")
    @classmethod
    def validate_parameters(cls, value):
        return validate_additional_parameters(value)


def normalize(config):
    if isinstance(config, dict) and {"provider_profile_id", "connection_mode", "api_key_env"} & set(
        config
    ):
        raise LegacyConnectionConfigError()
    try:
        return LLMTranslateConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        raise ValueError("LLM翻译配置无效，请检查连接、模型、语言及参数") from None


def validate_execution_config(config, connected_fields):
    for field, code, message in (
        ("base_url", "LLM_BASE_URL_REQUIRED", "请填写翻译服务地址"),
        ("model", "LLM_MODEL_REQUIRED", "请填写翻译模型"),
        ("target_language", "LLM_TARGET_LANGUAGE_REQUIRED", "请填写翻译目标语言"),
    ):
        if not config.get(field) and field not in connected_fields:
            raise NodeExecutionError(code, message)


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.llm_translation import translate
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..application.workflow_connections import (
        ConnectionSnapshotError,
        inline_connection_from_snapshot,
    )
    from ..domain.current_text import read_current_text, with_current_text
    from ..domain.workflow import WorkflowArtifact

    config = LLMTranslateConfig.model_validate(context.node.config)
    validate_execution_config(config.model_dump(), frozenset())
    upstream = context.input("transcript").value.transcript
    if not upstream.segments:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_EMPTY", "输入转写没有可翻译片段")
    try:
        items, language = read_current_text(upstream)
    except ValueError:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_INVALID", "输入转写片段无效") from None
    source_language = config.source_language or language
    if not source_language:
        raise NodeExecutionError(
            "LLM_SOURCE_LANGUAGE_REQUIRED", "上游当前文本缺少语言，请填写源语言"
        )
    try:
        connection, api_key = inline_connection_from_snapshot(
            context.store, context.run.workflow, context.node.id, context.run.project_id
        )
    except ConnectionSnapshotError as error:
        raise NodeExecutionError(error.code, str(error)) from None
    translated = await translate(
        items=items,
        source_language=source_language,
        target_language=config.target_language,
        connection=connection,
        api_key=api_key,
        model=config.model,
        instructions=config.instructions,
        glossary=config.glossary,
        batch_size=config.batch_size,
        max_batch_characters=config.max_batch_characters,
        context_segments=config.context_segments,
        max_attempts=config.max_attempts,
        additional_parameters=config.additional_parameters,
        cancel_event=context.cancel_event,
    )
    mapping = translated.translations
    if set(mapping) != {item["id"] for item in items} or any(
        not isinstance(text, str) or not text.strip() for text in mapping.values()
    ):
        raise NodeExecutionError("LLM_TRANSLATION_OUTPUT_INVALID", "翻译结果片段不完整")
    transcript = upstream.model_copy(deep=True)
    for segment in transcript.segments:
        segment.translated_text = mapping[segment.id]
    transcript = with_current_text(transcript, "translated_text", config.target_language)
    target = context.store.workflow_artifact_path(
        context.run.project_id,
        context.run.audio_id,
        context.run.id,
        context.node.id,
        "transcript.json",
    )
    size, digest = await _write_bytes(
        target, transcript.model_dump_json().encode("utf-8"), context.cancel_event
    )
    artifact = WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        port="transcript",
        filename="transcript.json",
        media_type="application/json",
        size_bytes=size,
        sha256=digest,
        download_url=f"/api/v1/projects/{context.run.project_id}/audios/{context.run.audio_id}/workflow-runs/{context.run.id}/outputs/{context.node.id}/download",
    )
    result = NodeResult(artifact=artifact)
    result.add("transcript", TranscriptData(path=target, transcript=transcript))
    return result


def definition():
    return NodeDefinition(
        type="llm_translate",
        label="LLM翻译",
        category=TRANSLATION,
        input_ports=("transcript", "model", "source_language", "target_language"),
        required_input_ports=("transcript",),
        output_ports=("transcript",),
        terminal=False,
        config_kind="llm_translate",
        fields=fields_from_model(
            LLMTranslateConfig,
            widgets={"glossary": ("json", None), "additional_parameters": ("json", None)},
            input_ports={
                "model": "model",
                "source_language": "source_language",
                "target_language": "target_language",
            },
        ),
        settings_default_fields=tuple(
            key for key in LLMTranslateConfig.model_fields if key != "credential_ref"
        ),
        normalize_config=normalize,
        validate_config=normalize,
        validate_execution_config=validate_execution_config,
        execute=execute,
        presentation={
            "inline_connection_input": {
                "provider_type": "llm_translate",
                "label": "LLM翻译",
                "base_url_key": "base_url",
                "timeout_key": "timeout_seconds",
                "region_key": None,
                "credential_ref_key": "credential_ref",
            }
        },
    )
