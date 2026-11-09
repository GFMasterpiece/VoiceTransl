"""Translate the current transcript using independently composed inputs."""

from uuid import uuid4

from ..domain.context_values import (
    TranslationContext,
    TranslationOptions,
    normalize_processor_config,
    optional_packet,
)
from ..domain.node_categories import TRANSLATION
from ..domain.node_contracts import NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult

normalize = normalize_processor_config


def validate_inputs(context):
    normalize(context.node.config)
    packet = optional_packet(context, "translation_context", TranslationContext)
    if not packet.target_language:
        raise NodeExecutionError("LLM_TARGET_LANGUAGE_REQUIRED", "请填写翻译上下文的目标语言")
    optional_packet(context, "translation_options", TranslationOptions)


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.llm_translation import translate
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..domain.configuration import resolve_model
    from ..domain.current_text import read_current_text, with_current_text
    from ..domain.workflow import WorkflowArtifact

    validate_inputs(context)
    packet = context.input("translation_context").value
    options = optional_packet(context, "translation_options", TranslationOptions)
    upstream = context.input("transcript").value.transcript
    if not upstream.segments:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_EMPTY", "输入转写没有可翻译片段")
    try:
        items, language = read_current_text(upstream)
    except ValueError:
        raise NodeExecutionError("LLM_TRANSLATION_INPUT_INVALID", "输入转写片段无效") from None
    source_language = packet.source_language or language
    if not source_language:
        raise NodeExecutionError(
            "LLM_SOURCE_LANGUAGE_REQUIRED", "上游当前文本缺少语言，请填写源语言"
        )
    connection, model, parameters, api_key = resolve_model(
        context, context.input("model").value, "translation", options.timeout_seconds
    )
    translated = await translate(
        items=items,
        source_language=source_language,
        target_language=packet.target_language,
        connection=connection,
        api_key=api_key,
        model=model,
        instructions=packet.render_instructions(),
        glossary=packet.glossary,
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
    transcript = with_current_text(transcript, "translated_text", packet.target_language)
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
        input_ports=("transcript", "model", "translation_context", "translation_options"),
        required_input_ports=("transcript", "model", "translation_context"),
        output_ports=("transcript",),
        terminal=False,
        config_kind="none",
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        validate_inputs=validate_inputs,
        validation_input_ports=("model", "translation_context", "translation_options"),
        binding_inputs={"model": "translation"},
        execution_version="2",
    )
