"""Qianwen Filetrans node: frozen connection profile and real transcript output."""

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..adapters.qwen_protocol import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    TEMPORARY_UPLOAD_RETENTION_HOURS,
    validate_additional_parameters,
)
from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeExecutionError, NodeResult


class QwenASRConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())

    provider_profile_id: str = Field(default="", max_length=64, description="Qwen连接配置")
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=128, description="模型名称")
    language: str = Field(default="", max_length=32, description="留空使用当前输入音频已声明的语言")
    additional_parameters: dict[str, object] = Field(
        default={}, description="可选模型参数（JSON对象）"
    )

    @field_validator("provider_profile_id", "model", "language")
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
        return QwenASRConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        # Pydantic's default rendering can include rejected authentication values.
        raise ValueError(
            "Qwen配置无效：请检查模型、语言及附加参数（对象、16 KiB、最多8层）"
        ) from None


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..adapters.qwen_asr import transcribe
    from ..application.workflow_asr import TranscriptData, _write_bytes
    from ..application.workflow_profiles import ProfileSnapshotError, profile_from_snapshot
    from ..domain.workflow import WorkflowArtifact

    config = QwenASRConfig.model_validate(context.node.config)
    try:
        profile = profile_from_snapshot(
            context.run.workflow, config.provider_profile_id, "qwen_asr"
        )
    except ProfileSnapshotError as error:
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
        profile=profile,
        model=config.model,
        additional_parameters=config.additional_parameters,
        cancel_event=context.cancel_event,
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
            widgets={"additional_parameters": ("json", None)},
            input_ports={"language": "language"},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        presentation={
            "provider_profile_input": {
                "config_key": "provider_profile_id",
                "provider_type": "qwen_asr",
                "label": "Qwen ASR",
                "data_handling": {
                    "temporary_upload_retention_hours": TEMPORARY_UPLOAD_RETENTION_HOURS,
                    "remote_cancel_pending_only": True,
                },
                "defaults": {
                    "base_url": DEFAULT_BASE_URL,
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "default_timeout": 3600,
                },
            }
        },
    )
