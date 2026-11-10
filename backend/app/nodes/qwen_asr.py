"""Recognize audio using composed model, language context and execution options."""

from uuid import uuid4

from ..adapters.qwen_context import input_context
from ..domain.context_values import (
    ASROptions,
    normalize_processor_config,
    optional_packet,
    resolve_audio_language,
)
from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_contracts import NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeResult

normalize = normalize_processor_config


def validate_inputs(context):
    normalize(context.node.config)
    input_context(context)
    optional_packet(context, "asr_options", ASROptions)


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.qwen_asr import transcribe
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..domain.configuration import resolve_model
    from ..domain.workflow import WorkflowArtifact

    validate_inputs(context)
    text_context = input_context(context)
    options = optional_packet(context, "asr_options", ASROptions)
    language = resolve_audio_language(context)
    connection, model, parameters, api_key = resolve_model(
        context, context.input("model").value, "asr_file", options.timeout_seconds
    )
    transcript = await transcribe(
        audio_path=context.input("audio").value.path,
        project_id=context.run.project_id,
        language=language,
        context=text_context,
        connection=connection,
        model=model,
        additional_parameters=parameters,
        cancel_event=context.cancel_event,
        api_key=api_key,
    )
    target = context.store.workflow_artifact_path(
        context.run.project_id,
        context.run.audio_id,
        context.run.id,
        context.node.id,
        "transcript.json",
    )
    payload = transcript.model_dump_json(indent=2).encode("utf-8") + b"\n"
    size, digest = await _write_bytes(target, payload, context.cancel_event)
    artifact = WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        port="transcript",
        filename="transcript.json",
        media_type="application/json",
        size_bytes=size,
        sha256=digest,
        download_url=(
            f"/api/v1/projects/{context.run.project_id}/audios/{context.run.audio_id}"
            f"/workflow-runs/{context.run.id}/outputs/{context.node.id}/download"
        ),
    )
    result = NodeResult(artifact=artifact)
    result.add("transcript", TranscriptData(path=target, transcript=transcript))
    return result


def definition():
    return NodeDefinition(
        type="qwen_asr",
        label="Qwen语音识别",
        category=SPEECH_RECOGNITION,
        input_ports=("audio", "model", "context", "asr_options"),
        required_input_ports=("audio", "model"),
        output_ports=("transcript",),
        terminal=False,
        config_kind="none",
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        validate_inputs=validate_inputs,
        validation_input_ports=("model", "context", "asr_options"),
        binding_inputs={"model": "asr_file"},
        execution_version="3",
    )
