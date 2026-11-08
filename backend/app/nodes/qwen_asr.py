"""Qianwen Filetrans node: node-owned connection and real transcript output."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..adapters.qwen_protocol import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    TEMPORARY_UPLOAD_RETENTION_HOURS,
    validate_additional_parameters,
    validate_base_url,
)
from ..domain.inline_connection import LegacyConnectionConfigError
from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult


class QwenASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())

    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=128, description="模型名称")
    language: str = Field(default="", max_length=32, description="留空使用当前输入音频已声明的语言")
    additional_parameters: dict[str, object] = Field(
        default={}, description="可选模型参数（JSON对象）"
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        max_length=2048,
        json_schema_extra={"creation_default": DEFAULT_BASE_URL},
    )
    timeout_seconds: int = Field(
        default=3600, ge=1, le=3600, json_schema_extra={"creation_default": 3600}
    )
    region: str | None = Field(default=None, max_length=64)
    credential_ref: str | None = Field(default=None, max_length=128)

    @field_validator("base_url")
    @classmethod
    def validate_connection_url(cls, value):
        return validate_base_url(value)

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
    if isinstance(config, dict) and {"provider_profile_id", "connection_mode", "api_key_env"} & set(
        config
    ):
        raise LegacyConnectionConfigError()
    try:
        value = QwenASRConfig.model_validate(config).model_dump(mode="json")
        value["base_url"] = value["base_url"].rstrip("/")
        return value
    except ValueError:
        raise ValueError("Qwen配置无效：请检查节点连接、模型、语言及附加参数") from None


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.qwen_asr import transcribe
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..application.workflow_connections import (
        ConnectionSnapshotError,
        inline_connection_from_snapshot,
    )
    from ..domain.workflow import WorkflowArtifact

    config = QwenASRConfig.model_validate(context.node.config)
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
        raise NodeExecutionError("QWEN_LANGUAGE_REQUIRED", "请声明输入音频的语言或设置识别语言")
    transcript = await transcribe(
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
        type="qwen_asr",
        label="Qwen语音识别",
        category=SPEECH_RECOGNITION,
        input_ports=("audio", "language"),
        required_input_ports=("audio",),
        output_ports=("transcript",),
        terminal=False,
        config_kind="qwen_asr",
        fields=fields_from_model(
            QwenASRConfig,
            widgets={"additional_parameters": ("json", None), "timeout_seconds": ("integer", None)},
            input_ports={"language": "language"},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        presentation={
            "inline_connection_input": {
                "provider_type": "qwen_asr",
                "label": "Qwen ASR",
                "data_handling": {
                    "temporary_upload_retention_hours": TEMPORARY_UPLOAD_RETENTION_HOURS,
                    "remote_cancel_pending_only": True,
                },
                "base_url_key": "base_url",
                "timeout_key": "timeout_seconds",
                "region_key": "region",
                "credential_ref_key": "credential_ref",
            },
        },
    )
