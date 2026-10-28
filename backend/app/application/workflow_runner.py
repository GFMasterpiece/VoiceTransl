import asyncio
import hashlib
import logging
import os
import subprocess
import threading
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..domain import (
    PREPROCESS_NODE_TYPE,
    PreprocessConfig,
    WorkflowArtifact,
    WorkflowNode,
    WorkflowNodeError,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
)
from ..providers import FFmpegPreprocessProvider, PreprocessError

COPY_CHUNK_SIZE = 1024 * 1024
FFMPEG_TIMEOUT_SECONDS = 300.0
FFMPEG_TERMINATE_SECONDS = 2.0

logger = logging.getLogger("voicetransl.workflow")


def now():
    return datetime.now(timezone.utc)


class WorkflowRunCancelled(Exception):
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


async def _terminate_process(process) -> None:
    """Terminate and reap a child process before callers can observe completion."""

    if getattr(process, "returncode", None) is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=FFMPEG_TERMINATE_SECONDS)
        except asyncio.TimeoutError:
            if getattr(process, "returncode", None) is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
    else:
        # A process can have exited while its wait future is still pending.
        await process.wait()


async def _ffmpeg_process_runner(command: list[str], cancel_event: asyncio.Event):
    if cancel_event.is_set():
        raise WorkflowRunCancelled
    spawn_task = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    )
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {spawn_task, cancel_wait},
            timeout=FFMPEG_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            spawn_task.cancel()
            spawned = await asyncio.gather(spawn_task, return_exceptions=True)
            process = spawned[0]
            if not isinstance(process, BaseException):
                await _terminate_process(process)
            raise PreprocessError("PREPROCESS_FFMPEG_TIMEOUT", "FFmpeg 音频转换超时")
        if cancel_wait in done and cancel_wait.result():
            spawn_task.cancel()
            spawned = await asyncio.gather(spawn_task, return_exceptions=True)
            process = spawned[0]
            if not isinstance(process, BaseException):
                await _terminate_process(process)
            raise WorkflowRunCancelled
        process = await spawn_task
    except asyncio.CancelledError:
        spawn_task.cancel()
        spawned = await asyncio.gather(spawn_task, return_exceptions=True)
        process = spawned[0]
        if not isinstance(process, BaseException):
            await _terminate_process(process)
        raise
    finally:
        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)

    wait_task = asyncio.create_task(process.wait())
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {wait_task, cancel_wait},
            timeout=FFMPEG_TIMEOUT_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            await _terminate_process(process)
            await asyncio.gather(wait_task, return_exceptions=True)
            raise PreprocessError("PREPROCESS_FFMPEG_TIMEOUT", "FFmpeg 音频转换超时")
        if cancel_wait in done and cancel_wait.result():
            await _terminate_process(process)
            await asyncio.gather(wait_task, return_exceptions=True)
            raise WorkflowRunCancelled
        return_code = await wait_task
        return subprocess.CompletedProcess(command, int(return_code), b"", b"")
    except asyncio.CancelledError:
        await _terminate_process(process)
        await asyncio.gather(wait_task, return_exceptions=True)
        raise
    finally:
        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)


def _make_ffmpeg_runner(cancel_event: asyncio.Event):
    async def runner(command: list[str]):
        return await _ffmpeg_process_runner(command, cancel_event)

    return runner


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


def _validate_wav_output(path: Path, *, sample_rate: int, channels: int) -> None:
    """Validate the WAV format rather than trusting FFmpeg's exit code."""

    if path.is_symlink() or not path.is_file():
        raise PreprocessError("PREPROCESS_OUTPUT_MISSING", "FFmpeg 未生成预处理音频")
    try:
        with wave.open(str(path), "rb") as wav_file:
            if (
                wav_file.getcomptype() != "NONE"
                or wav_file.getnchannels() != channels
                or wav_file.getframerate() != sample_rate
                or wav_file.getsampwidth() not in {1, 2, 3, 4}
                or wav_file.getnframes() <= 0
            ):
                raise PreprocessError(
                    "PREPROCESS_OUTPUT_INVALID",
                    "FFmpeg 生成的 WAV 参数与配置不一致",
                )
            frame_count = wav_file.getnframes()
            frame_size = wav_file.getnchannels() * wav_file.getsampwidth()
            remaining = frame_count * frame_size
            while remaining:
                chunk = wav_file.readframes(min(remaining // frame_size, 64 * 1024))
                if not chunk:
                    break
                remaining -= len(chunk)
            if remaining:
                raise PreprocessError("PREPROCESS_OUTPUT_INVALID", "FFmpeg 生成的 WAV 数据不完整")
    except PreprocessError:
        raise
    except (EOFError, OSError, wave.Error) as error:
        raise PreprocessError("PREPROCESS_OUTPUT_INVALID", "FFmpeg 生成的音频无法读取") from error


def _dependency_edges(run: WorkflowRun) -> tuple[dict[str, str], list[str]]:
    incoming: dict[str, str] = {}
    order: list[str] = []
    outgoing: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for node in run.workflow.nodes:
        indegree[node.id] = 0
        outgoing[node.id] = []
    for edge in run.workflow.edges:
        incoming[edge.target] = edge.source
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1
    queue = [node.id for node in run.workflow.nodes if indegree[node.id] == 0]
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(indegree):
        raise ValueError("workflow graph has a cycle")
    return incoming, order


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


async def _execute_copy_node(
    store,
    run: WorkflowRun,
    node: WorkflowNode,
    upstream: AudioData,
    cancel_event: asyncio.Event,
) -> WorkflowArtifact:
    suffix = (
        upstream.suffix
        if node.type == PREPROCESS_NODE_TYPE
        else str(node.config.get("filename_stem", "audio-copy"))
    )
    if node.type == PREPROCESS_NODE_TYPE:
        filename = f"preprocessed{suffix}"
    else:
        filename = f"{suffix}{upstream.suffix}"
    target = store.workflow_artifact_path(run.project_id, run.audio_id, run.id, node.id, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    size, digest = await _copy_audio(
        upstream.path, target, cancel_event, expected_size=upstream.size_bytes
    )
    return _artifact(run, node.id, filename, upstream.media_type, size, digest)


async def _execute_ffmpeg_node(
    store,
    run: WorkflowRun,
    node: WorkflowNode,
    upstream: AudioData,
    cancel_event: asyncio.Event,
    ffmpeg_path: str,
) -> tuple[WorkflowArtifact, AudioData]:
    config = PreprocessConfig.model_validate(node.config, strict=True)
    filename = "preprocessed.wav"
    target = store.workflow_artifact_path(run.project_id, run.audio_id, run.id, node.id, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp.wav")
    runner = _make_ffmpeg_runner(cancel_event)
    try:
        await FFmpegPreprocessProvider(ffmpeg_path, runner=runner).preprocess(
            upstream.path, temp, config
        )
        if cancel_event.is_set():
            raise WorkflowRunCancelled
        validation = asyncio.create_task(
            asyncio.to_thread(
                _validate_wav_output, temp, sample_rate=config.sample_rate, channels=config.channels
            )
        )
        try:
            await asyncio.shield(validation)
        except asyncio.CancelledError:
            # The validation thread is short and only reads a local temporary
            # file. Do not let it keep using a path after cancellation cleanup.
            try:
                await asyncio.shield(validation)
            except PreprocessError:
                pass
            raise
        size = temp.stat().st_size
        published_size, digest = await _copy_audio(temp, target, cancel_event, expected_size=size)
        artifact = _artifact(run, node.id, filename, "audio/wav", published_size, digest)
        return artifact, AudioData(
            path=target, suffix=".wav", media_type="audio/wav", size_bytes=published_size
        )
    except asyncio.CancelledError:
        raise
    finally:
        temp.unlink(missing_ok=True)


async def _execute_preprocess_node(
    store,
    run: WorkflowRun,
    node: WorkflowNode,
    upstream: AudioData,
    cancel_event: asyncio.Event,
    ffmpeg_path: str,
) -> tuple[WorkflowArtifact, AudioData]:
    config = PreprocessConfig.model_validate(node.config, strict=True)
    if config.provider == "mock":
        artifact = await _execute_copy_node(store, run, node, upstream, cancel_event)
        return artifact, AudioData(
            path=store.workflow_artifact_path(
                run.project_id, run.audio_id, run.id, node.id, artifact.filename
            ),
            suffix=upstream.suffix,
            media_type=upstream.media_type,
            size_bytes=artifact.size_bytes,
        )
    if config.provider == "ffmpeg":
        return await _execute_ffmpeg_node(store, run, node, upstream, cancel_event, ffmpeg_path)
    raise PreprocessError("PREPROCESS_PROVIDER_INVALID", "预处理 Provider 不受支持")


async def execute_workflow_run(
    store,
    run: WorkflowRun,
    cancel_event: asyncio.Event,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> WorkflowRun:
    run.status = WorkflowRunStatus.RUNNING
    run.started_at = now()
    run.finished_at = None
    run.error = None
    store.save_workflow_run(run)

    records = {node.node_id: node for node in run.nodes}
    node_by_id = {node.id: node for node in run.workflow.nodes}
    output_ids = [node.id for node in run.workflow.nodes if node.type == "audio_output"]
    input_nodes = [node for node in run.workflow.nodes if node.type == "audio_input"]
    input_record = records.get(input_nodes[0].id) if input_nodes else None
    incoming, order = _dependency_edges(run)

    audio = None
    source = None
    try:
        project = store.load_project(run.project_id)
        audio = next((item for item in project.audios if item.id == run.audio_id), None)
        if audio is None:
            raise FileNotFoundError(run.audio_id)
        source = _source_audio(store, run, audio)
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
                record.error = WorkflowNodeError(
                    type="DEPENDENCY_BLOCKED", message="上游音频不可用，节点未执行"
                )
        run.status = WorkflowRunStatus.FAILED
        run.finished_at = now()
        run.error = WorkflowRunError(type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用")
        store.save_workflow_run(run)
        return run

    audio_by_node: dict[str, AudioData] = {}
    if input_nodes:
        audio_by_node[input_nodes[0].id] = source
    failed_or_blocked: set[str] = set()
    try:
        for node_id in order:
            node = node_by_id[node_id]
            record = records[node_id]
            if node.type == "audio_input":
                continue
            upstream_id = incoming[node_id]
            if upstream_id in failed_or_blocked or upstream_id not in audio_by_node:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="DEPENDENCY_BLOCKED", message="上游节点未成功，节点未执行"
                )
                failed_or_blocked.add(node.id)
                store.save_workflow_run(run)
                continue
            if cancel_event.is_set():
                raise WorkflowRunCancelled
            record.finished_at = None
            record.error = None
            upstream = audio_by_node[upstream_id]
            record.status = WorkflowNodeStatus.RUNNING
            record.started_at = now()
            store.save_workflow_run(run)
            try:
                if node.type == PREPROCESS_NODE_TYPE:
                    artifact, output = await _execute_preprocess_node(
                        store, run, node, upstream, cancel_event, ffmpeg_path
                    )
                    record.artifact = artifact
                    audio_by_node[node.id] = output
                elif node.type == "audio_output":
                    artifact = await _execute_copy_node(store, run, node, upstream, cancel_event)
                    record.artifact = artifact
                    audio_by_node[node.id] = AudioData(
                        path=store.workflow_artifact_path(
                            run.project_id,
                            run.audio_id,
                            run.id,
                            node.id,
                            artifact.filename,
                        ),
                        suffix=upstream.suffix,
                        media_type=artifact.media_type,
                        size_bytes=artifact.size_bytes,
                    )
                else:
                    raise ValueError("unsupported node type")
                record.status = WorkflowNodeStatus.COMPLETED
                record.finished_at = now()
                store.save_workflow_run(run)
            except WorkflowRunCancelled:
                raise
            except PreprocessError as error:
                record.status = WorkflowNodeStatus.FAILED
                record.finished_at = now()
                record.error = WorkflowNodeError(type=error.code, message=str(error))
                failed_or_blocked.add(node.id)
            except Exception:
                record.status = WorkflowNodeStatus.FAILED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type=(
                        "PREPROCESS_FAILED"
                        if node.type == PREPROCESS_NODE_TYPE
                        else "OUTPUT_COPY_FAILED"
                    ),
                    message=(
                        "预处理执行失败"
                        if node.type == PREPROCESS_NODE_TYPE
                        else "输出音频复制失败"
                    ),
                )
                failed_or_blocked.add(node.id)
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
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("workflow run failed run=%s", run.id)
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
                record.error = WorkflowNodeError(
                    type="WORKFLOW_RUN_FAILED", message="工作流运行失败，节点未完成"
                )
    else:
        successes = sum(
            records[node_id].status == WorkflowNodeStatus.COMPLETED for node_id in output_ids
        )
        if successes == len(output_ids):
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
