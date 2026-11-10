"""Built-in runtime data contracts, registered at composition time."""

import math

from ..domain.node_constants import AUDIO_PORT, TEXT_PORT, TRANSCRIPT_PORT
from .checkpoint_codecs import AUDIO_CODEC, SCALAR_CODEC, TEXT_CODEC, TRANSCRIPT_CODEC


def _is_audio_data(value: object) -> bool:
    # Structural validation keeps concrete audio implementations replaceable.
    path = getattr(value, "path", None)
    return (
        path is not None
        and hasattr(path, "open")
        and isinstance(getattr(value, "suffix", None), str)
        and isinstance(getattr(value, "media_type", None), str)
        and isinstance(getattr(value, "size_bytes", None), int)
        and getattr(value, "size_bytes", -1) >= 0
    )


def _is_transcript_data(value: object) -> bool:
    from ..domain.transcript import Transcript

    return isinstance(getattr(value, "transcript", None), Transcript) and hasattr(value, "path")


def _is_text_artifact(value: object) -> bool:
    from ..domain.workflow import WorkflowArtifact

    return isinstance(value, WorkflowArtifact) and value.port == TEXT_PORT


def register_builtin_types(register_type, register_port):
    from ..domain.checkpoint import CheckpointCodec
    from ..domain.configuration import ConnectionBinding, ModelBinding
    from .configuration_values import register_configuration_types

    async def encode_binding(value, context):
        return value.model_dump(mode="json")

    async def decode_connection(payload, context):
        from ..domain.configuration import validate_connection

        value = ConnectionBinding.model_validate(payload)
        validate_connection(context, value)
        return value

    async def decode_model(payload, context):
        from ..application.configuration_evaluator import evaluate_configuration_graph

        value = ModelBinding.model_validate(payload)
        results = await evaluate_configuration_graph(
            context.store,
            context.run,
            context.store.load_project(context.run.project_id),
            context.cancel_event,
            required_ids={value.owner_node_id},
        )
        expected = results[value.owner_node_id].outputs["model"].value
        if value != expected:
            from ..domain.checkpoint import CheckpointUnavailable

            raise CheckpointUnavailable()
        return value

    register_type(
        "connection_binding",
        label="服务连接",
        data_type=ConnectionBinding,
        checkpoint_codec=CheckpointCodec(
            "connection_binding", "1", encode_binding, decode_connection
        ),
        presentation={"color": "#A78BFA"},
    )
    register_type(
        "model_binding",
        label="模型",
        data_type=ModelBinding,
        checkpoint_codec=CheckpointCodec("model_binding", "1", encode_binding, decode_model),
        presentation={"color": "#C084FC"},
    )
    register_port("connection", type_id="connection_binding", label="服务连接")
    register_port("model", type_id="model_binding", label="模型")
    register_type(AUDIO_PORT, label="音频", validator=_is_audio_data, checkpoint_codec=AUDIO_CODEC)
    register_type(
        TRANSCRIPT_PORT,
        label="转写结果",
        validator=_is_transcript_data,
        checkpoint_codec=TRANSCRIPT_CODEC,
    )
    register_type(
        TEXT_PORT, label="文本产物", validator=_is_text_artifact, checkpoint_codec=TEXT_CODEC
    )
    register_port(AUDIO_PORT, type_id=AUDIO_PORT)
    register_port(TRANSCRIPT_PORT, type_id=TRANSCRIPT_PORT)
    register_port(TEXT_PORT, type_id=TEXT_PORT)
    register_type(
        "string",
        label="字符串",
        validator=lambda value: isinstance(value, str),
        checkpoint_codec=SCALAR_CODEC,
    )
    register_type(
        "number",
        label="数字",
        checkpoint_codec=SCALAR_CODEC,
        validator=lambda value: (
            type(value) in (int, float) and (type(value) is int or math.isfinite(value))
        ),
    )
    register_type(
        "integer",
        label="整数",
        parent_type_id="number",
        validator=lambda value: type(value) is int,
        checkpoint_codec=SCALAR_CODEC,
    )
    register_type(
        "boolean",
        label="布尔值",
        validator=lambda value: type(value) is bool,
        checkpoint_codec=SCALAR_CODEC,
    )
    for port in ("string", "number", "integer", "boolean"):
        register_port(port, type_id=port)
    for port in ("filename_stem", "text_format", "model_name"):
        register_port(port, type_id="string")
    register_configuration_types(register_type, register_port)
    register_port("additional_parameters", type_id="json_object", label="模型请求参数")
