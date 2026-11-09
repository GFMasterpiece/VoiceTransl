"""File audio streamed over WebSocket with a credential owned by this node instance."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..adapters.qwen_streaming_protocol import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    validate_additional_parameters,
    validate_endpoint,
)
from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult

PROVIDER_TYPE = "qwen_asr_streaming"


class QwenStreamingASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())

    endpoint: str = Field(default=DEFAULT_ENDPOINT, max_length=2048)
    timeout_seconds: int = Field(default=3600, ge=1, le=3600)
    credential_ref: str | None = Field(default=None, max_length=128)
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=128)
    language: str = Field(
        default="",
        max_length=32,
        description="留空使用当前输入音频已声明的语言",
        json_schema_extra={"suggestion_source": "languages"},
    )
    additional_parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("endpoint")
    @classmethod
    def validate_connection_endpoint(cls, value):
        return validate_endpoint(value)

    @field_validator("model", "language")
    @classmethod
    def validate_string(cls, value):
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("配置字符串不能包含控制字符或首尾空白")
        return value

    @field_validator("additional_parameters", mode="before")
    @classmethod
    def validate_parameters(cls, value):
        return validate_additional_parameters(value)


def normalize(config):
    try:
        return QwenStreamingASRConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        raise ValueError("Qwen Streaming配置无效：请检查连接、模型、语言及附加参数") from None


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.qwen_asr_streaming import QwenStreamingASRAdapter
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..application.workflow_connections import (
        ConnectionSnapshotError,
        inline_connection_from_snapshot,
    )
    from ..domain.workflow import WorkflowArtifact

    config = QwenStreamingASRConfig.model_validate(context.node.config)
    try:
        connection, api_key = inline_connection_from_snapshot(
            context.store, context.run.workflow, context.node.id, context.run.project_id
        )
    except ConnectionSnapshotError as error:
        raise NodeExecutionError(error.code, str(error)) from None
    audio = next(
        (audio for audio in context.project.audios if audio.id == context.run.audio_id), None
    )
    language = config.language or (
        audio.source.language if audio else context.project.source_language
    )
    if not language:
        raise NodeExecutionError(
            "QWEN_STREAM_LANGUAGE_REQUIRED", "请声明输入音频的语言或设置识别语言"
        )
    transcript = await QwenStreamingASRAdapter().transcribe(
        audio_path=context.input("audio").value.path,
        project_id=context.run.project_id,
        language=language,
        connection=connection,
        model=config.model,
        additional_parameters=config.additional_parameters,
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
        type=PROVIDER_TYPE,
        label="Qwen Streaming语音识别",
        category=SPEECH_RECOGNITION,
        input_ports=("audio", "language"),
        required_input_ports=("audio",),
        output_ports=("transcript",),
        terminal=False,
        config_kind=PROVIDER_TYPE,
        fields=fields_from_model(
            QwenStreamingASRConfig,
            widgets={
                "additional_parameters": ("json", None),
            },
            input_ports={"language": "language"},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        presentation={
            "inline_connection_input": {
                "provider_type": PROVIDER_TYPE,
                "label": "Qwen Streaming",
                "base_url_key": "endpoint",
                "timeout_key": "timeout_seconds",
                "region_key": None,
                "credential_ref_key": "credential_ref",
                "data_handling": {
                    "temporary_upload_retention_hours": None,
                    "remote_cancel_pending_only": False,
                    "cancel_behavior": "close_stream",
                },
            },
        },
    )
