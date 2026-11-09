"""Built-in codecs; transport objects and credentials never enter checkpoints."""

from ..domain.checkpoint import CheckpointCodec, CheckpointUnavailable


def _safe_metadata(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if normalized in {
                "apikey",
                "secret",
                "password",
                "authorization",
                "credential",
                "credentials",
                "accesstoken",
                "refreshtoken",
                "downloadurl",
                "uploadurl",
                "resulturl",
                "temporaryurl",
                "signedurl",
            }:
                raise CheckpointUnavailable(
                    "CHECKPOINT_UNSAFE", "结果元数据包含不能保存的连接或认证信息"
                )
            _safe_metadata(item)
    elif isinstance(value, list):
        for item in value:
            _safe_metadata(item)


async def encode_scalar(value, context):
    return value


async def decode_scalar(payload, context):
    return payload


async def encode_audio(value, context):
    key = await context.add_file(value.path)
    if (
        context.files[key]["size_bytes"] != value.size_bytes
        or context.file(key).suffix != value.suffix
    ):
        raise CheckpointUnavailable()
    return {
        "file": key,
        "suffix": value.suffix,
        "media_type": value.media_type,
        "size_bytes": value.size_bytes,
    }


async def decode_audio(payload, context):
    from ..application.workflow_media import AudioData

    return AudioData(
        path=context.file(payload["file"]),
        suffix=payload["suffix"],
        media_type=payload["media_type"],
        size_bytes=payload["size_bytes"],
    )


async def encode_transcript(value, context):
    _safe_metadata(value.transcript.metadata)
    for segment in value.transcript.segments:
        _safe_metadata(segment.metadata)
    return {
        "file": await context.add_file(value.path),
        "transcript": value.transcript.model_dump(mode="json"),
    }


async def decode_transcript(payload, context):
    from ..application.workflow_asr import TranscriptData
    from ..domain.transcript import Transcript
    from ..domain.workflow import validate_workflow_transcript

    transcript = Transcript.model_validate(payload["transcript"])
    validate_workflow_transcript(transcript)
    return TranscriptData(path=context.file(payload["file"]), transcript=transcript)


async def encode_text(value, context):
    return {"file": await context.add_artifact(value), "artifact": context.artifact_payload(value)}


async def decode_text(payload, context):
    return context.restore_artifact(payload["artifact"])


SCALAR_CODEC = CheckpointCodec("scalar", "1", encode_scalar, decode_scalar)
AUDIO_CODEC = CheckpointCodec("local_audio", "1", encode_audio, decode_audio)
TRANSCRIPT_CODEC = CheckpointCodec("transcript", "1", encode_transcript, decode_transcript)
TEXT_CODEC = CheckpointCodec("text_artifact", "1", encode_text, decode_text)
