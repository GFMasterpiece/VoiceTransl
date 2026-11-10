"""Translate the current transcript using independently composed inputs."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.context_values import (
    TranslationOptions,
    optional_packet,
    validate_text,
)
from ..domain.node_categories import TRANSLATION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult


class LLMTranslateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: str = Field(default="", json_schema_extra={"widget": "multiline"})

    @field_validator("context")
    @classmethod
    def valid_context(cls, value):
        return validate_text(value)


def normalize(config):
    return LLMTranslateConfig.model_validate(config).model_dump(mode="json")


def validate_execution(config, connected_fields):
    preferences = normalize(config)["context"]
    if "context" not in connected_fields and not preferences.strip():
        raise NodeExecutionError("LLM_CONTEXT_INVALID", "翻译上下文必须是非空字符串")


def validate_inputs(context):
    optional_packet(context, "translation_options", TranslationOptions)
    if "context" in context.inputs:
        preferences = context.input("context").value
        try:
            validate_text(preferences)
            if not preferences.strip():
                raise ValueError
        except ValueError:
            raise NodeExecutionError("LLM_CONTEXT_INVALID", "翻译上下文必须是非空字符串") from None
    else:
        edges = getattr(getattr(context.run, "workflow", None), "edges", ())
        connected = any(
            edge.target == context.node.id and edge.target_port == "context" for edge in edges
        )
        validate_execution(
            context.node.config, frozenset({"context"}) if connected else frozenset()
        )


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.llm_translation import translate
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..domain.configuration import resolve_model
    from ..domain.current_text import read_current_text, with_current_text
    from ..domain.workflow import WorkflowArtifact

    validate_inputs(context)
    preferences = (
        context.input("context").value
        if "context" in context.inputs
        else normalize(context.node.config)["context"]
    )
    options = optional_packet(context, "translation_options", TranslationOptions)
    upstream = context.input("transcript").value.transcript
    if not upstream.segments:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_EMPTY", "输入转写没有可翻译片段")
    try:
        items, language = read_current_text(upstream)
    except ValueError:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_INVALID", "输入转写片段无效") from None
    connection, model, parameters, api_key = await resolve_model(
        context, context.input("llm_model").value, "translation", options.timeout_seconds
    )
    translated = await translate(
        items=items,
        source_language=language or None,
        context=preferences,
        connection=connection,
        api_key=api_key,
        model=model,
        batch_size=options.batch_size,
        max_batch_characters=options.max_batch_characters,
        context_segments=options.context_segments,
        max_attempts=options.max_attempts,
        additional_parameters=parameters,
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
    transcript = with_current_text(transcript, "translated_text", translated.language)
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
        input_ports=("transcript", "llm_model", "context", "translation_options"),
        required_input_ports=("transcript", "llm_model"),
        output_ports=("transcript",),
        terminal=False,
        config_kind="object",
        fields=fields_from_model(LLMTranslateConfig, input_ports={"context": "context"}),
        settings_default_fields=("context",),
        validate_execution_config=validate_execution,
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        validate_inputs=validate_inputs,
        validation_input_ports=("llm_model", "context", "translation_options"),
        binding_inputs={"llm_model": "translation"},
        execution_version="4",
    )
