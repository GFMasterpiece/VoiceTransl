"""Built-in runtime data contracts, registered at composition time."""

import math

from ..domain.node_constants import AUDIO_PORT, TEXT_PORT, TRANSCRIPT_PORT


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
    register_type(AUDIO_PORT, label="音频", validator=_is_audio_data)
    register_type(TRANSCRIPT_PORT, label="转写结果", validator=_is_transcript_data)
    register_type(TEXT_PORT, label="文本产物", validator=_is_text_artifact)
    register_port(AUDIO_PORT, type_id=AUDIO_PORT)
    register_port(TRANSCRIPT_PORT, type_id=TRANSCRIPT_PORT)
    register_port(TEXT_PORT, type_id=TEXT_PORT)
    register_type("string", label="字符串", validator=lambda value: isinstance(value, str))
    register_type(
        "number",
        label="数字",
        validator=lambda value: (
            type(value) in (int, float) and (type(value) is int or math.isfinite(value))
        ),
    )
    register_type(
        "integer", label="整数", parent_type_id="number", validator=lambda value: type(value) is int
    )
    register_type("boolean", label="布尔值", validator=lambda value: type(value) is bool)
    for port in ("string", "number", "integer", "boolean"):
        register_port(port, type_id=port)
    for port in ("language", "filename_stem", "text_format"):
        register_port(port, type_id="string")
