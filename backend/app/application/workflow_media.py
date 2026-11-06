"""Project-scoped media access and cancellable artifact publication."""

import asyncio
import hashlib
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..domain.node_execution import NodeExecutionCancelled
from ..domain.workflow import WorkflowArtifact, WorkflowRun

COPY_CHUNK_SIZE = 1024 * 1024


class WorkflowRunCancelled(NodeExecutionCancelled):
    pass


@dataclass(frozen=True)
class AudioData:
    path: Path
    suffix: str
    media_type: str
    size_bytes: int


async def _copy_audio(
    source: Path,
    target: Path,
    cancel_event: asyncio.Event,
    *,
    expected_size: int,
) -> tuple[int, str]:
    if cancel_event.is_set():
        raise WorkflowRunCancelled
    cancellation = threading.Event()
    publish_lock = threading.Lock()

    def copy_blocking() -> tuple[int, str]:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        digest = hashlib.sha256()
        total = 0
        try:
            with source.open("rb") as source_stream, temp.open("xb") as target_stream:
                while chunk := source_stream.read(COPY_CHUNK_SIZE):
                    if cancellation.is_set():
                        raise WorkflowRunCancelled
                    digest.update(chunk)
                    target_stream.write(chunk)
                    total += len(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            if total != expected_size:
                raise OSError("source size changed while copying")
            with publish_lock:
                if cancellation.is_set():
                    raise WorkflowRunCancelled
                os.replace(temp, target)
            return total, digest.hexdigest()
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    task = asyncio.create_task(asyncio.to_thread(copy_blocking))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.05)
            if done:
                result = await task
                if cancel_event.is_set():
                    with publish_lock:
                        cancellation.set()
                    target.unlink(missing_ok=True)
                    raise WorkflowRunCancelled
                return result
            if cancel_event.is_set():
                with publish_lock:
                    cancellation.set()
                try:
                    await task
                except WorkflowRunCancelled:
                    pass
                target.unlink(missing_ok=True)
                raise WorkflowRunCancelled
    except asyncio.CancelledError:
        # A shutdown must not report interrupted while a copy thread can still
        # publish. Signal it and wait even if this coroutine is cancelled again.
        with publish_lock:
            cancellation.set()
        try:
            await asyncio.shield(task)
        except (WorkflowRunCancelled, asyncio.CancelledError):
            pass
        target.unlink(missing_ok=True)
        raise


def _download_url(run: WorkflowRun, node_id: str) -> str:
    return (
        f"/api/v1/projects/{run.project_id}/audios/{run.audio_id}"
        f"/workflow-runs/{run.id}/outputs/{node_id}/download"
    )


def _artifact(
    run: WorkflowRun, node_id: str, filename: str, media_type: str, size: int, digest: str
) -> WorkflowArtifact:
    return WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        filename=filename,
        media_type=media_type,
        size_bytes=size,
        sha256=digest,
        download_url=_download_url(run, node_id),
    )


def _source_audio(store, run: WorkflowRun, audio) -> AudioData:
    scope = store.scope(run.project_id, run.audio_id)
    path = scope.source_path(run.project_id, audio.source.path)
    if not path.is_file():
        raise FileNotFoundError("source")
    suffix = path.suffix
    if not suffix:
        raise ValueError("source audio has no extension")
    return AudioData(
        path=path,
        suffix=suffix,
        media_type=audio.source.media_type or "application/octet-stream",
        size_bytes=audio.source.size_bytes,
    )


async def write_audio_artifact(store, run, node_id, upstream, cancel_event, filename):
    target = store.workflow_artifact_path(run.project_id, run.audio_id, run.id, node_id, filename)
    size, digest = await _copy_audio(
        upstream.path, target, cancel_event, expected_size=upstream.size_bytes
    )
    return _artifact(run, node_id, filename, upstream.media_type, size, digest)
