"""Independent terminal executors with persistent per-run preview artifacts."""

from ..domain.node_execution import NodeResult
from ..domain.workflow import WorkflowPreview
from .workflow_asr import _render_text, _write_bytes
from .workflow_runner import _artifact, _copy_audio


def _result(artifact, renderer):
    return NodeResult(
        artifact=artifact,
        preview=WorkflowPreview(
            renderer=renderer,
            media_type=artifact.media_type,
            content_url=artifact.download_url.removesuffix("/download") + "/content",
        ),
    )


async def execute_audio_preview(context):
    from ..domain.media import inline_media_type

    audio = context.input("audio").value
    filename = "preview" + audio.suffix
    target = context.store.workflow_artifact_path(
        context.run.project_id, context.run.audio_id, context.run.id, context.node.id, filename
    )
    size, digest = await _copy_audio(
        audio.path, target, context.cancel_event, expected_size=audio.size_bytes
    )
    artifact = _artifact(
        context.run, context.node.id, filename, inline_media_type(target), size, digest
    )
    return _result(artifact, "audio")


async def execute_text_preview(context):
    transcript = context.input("transcript").value.transcript
    payload = _render_text(transcript, "txt").encode("utf-8")
    target = context.store.workflow_artifact_path(
        context.run.project_id, context.run.audio_id, context.run.id, context.node.id, "preview.txt"
    )
    size, digest = await _write_bytes(target, payload, context.cancel_event)
    artifact = _artifact(
        context.run, context.node.id, "preview.txt", "text/plain; charset=utf-8", size, digest
    )
    artifact.port = "text"
    return _result(artifact, "text")
