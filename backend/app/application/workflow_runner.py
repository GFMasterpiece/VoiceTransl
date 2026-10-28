import asyncio
import hashlib
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..domain import (
    WorkflowArtifact,
    WorkflowNodeError,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
)

COPY_CHUNK_SIZE = 1024 * 1024


def now():
    return datetime.now(timezone.utc)


class WorkflowRunCancelled(Exception):
    pass


async def _copy_audio(
    source: Path,
    target: Path,
    cancel_event: asyncio.Event,
    *,
    expected_size: int,
) -> tuple[int, str]:
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
    cancellation_event = cancel_event
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.05)
            if done:
                result = await task
                if cancellation_event.is_set():
                    with publish_lock:
                        cancellation.set()
                    target.unlink(missing_ok=True)
                    raise WorkflowRunCancelled
                return result
            if cancellation_event.is_set():
                with publish_lock:
                    cancellation.set()
                try:
                    await task
                except WorkflowRunCancelled:
                    pass
                target.unlink(missing_ok=True)
                raise WorkflowRunCancelled
    except asyncio.CancelledError:
        # Server shutdown cancels the asyncio task. Signal the worker, then
        # wait for it under a shield so no copy can publish after shutdown
        # reports the run interrupted. The original cancellation is preserved.
        with publish_lock:
            cancellation.set()
        try:
            await asyncio.shield(task)
        except (WorkflowRunCancelled, asyncio.CancelledError):
            pass
        # A worker can publish between the last cooperative cancellation check
        # and this handler. The per-run/node target is unique, so remove that
        # now-unreferenced copy before allowing shutdown to persist interrupted.
        target.unlink(missing_ok=True)
        raise


def _download_url(run: WorkflowRun, node_id: str) -> str:
    return (
        f"/api/v1/projects/{run.project_id}/audios/{run.audio_id}"
        f"/workflow-runs/{run.id}/outputs/{node_id}/download"
    )


async def execute_workflow_run(
    store,
    run: WorkflowRun,
    cancel_event: asyncio.Event,
) -> WorkflowRun:
    run.status = WorkflowRunStatus.RUNNING
    run.started_at = now()
    run.finished_at = None
    run.error = None
    store.save_workflow_run(run)

    output_nodes = [node for node in run.workflow.nodes if node.type == "audio_output"]
    records = {node.node_id: node for node in run.nodes}
    input_nodes = [node for node in run.workflow.nodes if node.type == "audio_input"]
    input_record = records.get(input_nodes[0].id) if input_nodes else None
    successes = 0

    audio = None
    source = None
    suffix = ""
    try:
        project = store.load_project(run.project_id)
        audio = next((item for item in project.audios if item.id == run.audio_id), None)
        if audio is None:
            raise FileNotFoundError(run.audio_id)
        source = store.scope(run.project_id, run.audio_id).source_path(
            run.project_id, audio.source.path
        )
        suffix = source.suffix
        if not suffix:
            raise ValueError("source audio has no file extension")
        if input_record is not None:
            input_record.status = WorkflowNodeStatus.COMPLETED
            input_record.started_at = input_record.started_at or now()
            input_record.finished_at = now()
            input_record.error = None
            store.save_workflow_run(run)
    except Exception:
        if input_record is not None:
            input_record.status = WorkflowNodeStatus.FAILED
            input_record.started_at = input_record.started_at or now()
            input_record.finished_at = now()
            input_record.error = WorkflowNodeError(
                type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用"
            )
        for record in run.nodes:
            if record is not input_record and record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
            }:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
        run.status = WorkflowRunStatus.FAILED
        run.finished_at = now()
        run.error = WorkflowRunError(type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用")
        store.save_workflow_run(run)
        return run

    try:
        for output in output_nodes:
            if cancel_event.is_set():
                raise WorkflowRunCancelled
            record = records[output.id]
            record.status = WorkflowNodeStatus.RUNNING
            record.started_at = now()
            record.finished_at = None
            record.error = None
            store.save_workflow_run(run)
            stem = str(output.config.get("filename_stem", "audio-copy"))
            filename = f"{stem}{suffix}"
            try:
                target = store.workflow_artifact_path(
                    run.project_id,
                    run.audio_id,
                    run.id,
                    output.id,
                    filename,
                )
                target.parent.mkdir(parents=True, exist_ok=False)
                size, sha256 = await _copy_audio(
                    source,
                    target,
                    cancel_event,
                    expected_size=audio.source.size_bytes,
                )
                record.status = WorkflowNodeStatus.COMPLETED
                record.finished_at = now()
                record.artifact = WorkflowArtifact(
                    artifact_id=f"art_{uuid4().hex}",
                    filename=filename,
                    media_type=audio.source.media_type or "application/octet-stream",
                    size_bytes=size,
                    sha256=sha256,
                    download_url=_download_url(run, output.id),
                )
                successes += 1
            except WorkflowRunCancelled:
                raise
            except Exception:
                record.status = WorkflowNodeStatus.FAILED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="OUTPUT_COPY_FAILED", message="输出音频复制失败"
                )
            store.save_workflow_run(run)
    except WorkflowRunCancelled:
        run.cancel_requested = True
        run.status = WorkflowRunStatus.CANCELLED
        run.finished_at = now()
        for record in run.nodes:
            if record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
                WorkflowNodeStatus.BLOCKED,
            }:
                record.status = WorkflowNodeStatus.CANCELLED
                record.finished_at = run.finished_at
        store.save_workflow_run(run)
        return run
    except Exception:
        run.status = WorkflowRunStatus.FAILED
        run.error = WorkflowRunError(type="WORKFLOW_RUN_FAILED", message="工作流运行失败")
        for record in run.nodes:
            if record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
                WorkflowNodeStatus.BLOCKED,
            }:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
    else:
        if successes == len(output_nodes):
            run.status = WorkflowRunStatus.COMPLETED
        elif successes:
            run.status = WorkflowRunStatus.PARTIAL
            run.error = WorkflowRunError(type="PARTIAL_FAILURE", message="部分输出节点执行失败")
        else:
            run.status = WorkflowRunStatus.FAILED
            run.error = WorkflowRunError(type="ALL_OUTPUTS_FAILED", message="全部输出节点执行失败")
    run.finished_at = now()
    store.save_workflow_run(run)
    return run
