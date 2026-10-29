"""ASR and text-output execution for node workflows."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..domain import (
    ASRConfig,
    Project,
    TextOutputConfig,
    Transcript,
    WorkflowArtifact,
    WorkflowRun,
    validate_asr_config,
    validate_text_output_config,
    validate_workflow_transcript,
)
from ..exporters import render_lrc, render_srt, render_vtt
from ..providers import CrispASRError, CrispASRProvider, MockASRProvider


class WorkflowASRError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class WorkflowASRCancelled(Exception):
    pass


@dataclass(frozen=True)
class ASRSettings:
    executable: str | Path = "crispasr"
    model_dir: str | Path = "crispasr/models"
    timeout_seconds: float = 300
    process_factory: Callable[..., Awaitable[Any]] | None = None
    mock_delay_ms: int = 0


@dataclass(frozen=True)
class TranscriptData:
    path: Path
    transcript: Transcript

    @property
    def suffix(self) -> str:
        return ".json"

    @property
    def media_type(self) -> str:
        return "application/json"


MEDIA_TYPES = {
    "txt": "text/plain",
    "srt": "application/x-subrip",
    "vtt": "text/vtt",
    "lrc": "text/plain",
}


def _validate_wav(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise WorkflowASRError("ASR_INPUT_MISSING", "ASR 输入音频不存在")
    try:
        with wave.open(str(path), "rb") as stream:
            if (
                stream.getcomptype() != "NONE"
                or stream.getframerate() != 16_000
                or stream.getnchannels() != 1
                or stream.getsampwidth() not in {1, 2, 3, 4}
                or stream.getnframes() <= 0
            ):
                raise WorkflowASRError(
                    "ASR_INPUT_FORMAT_INVALID",
                    "CrispASR 需要非空、完整的 16 kHz 单声道 PCM WAV；请先使用 FFmpeg 预处理",
                )
            frame_size = stream.getnchannels() * stream.getsampwidth()
            remaining = stream.getnframes() * frame_size
            while remaining:
                chunk = stream.readframes(min(remaining // frame_size, 64 * 1024))
                if not chunk:
                    break
                remaining -= len(chunk)
            if remaining:
                raise WorkflowASRError(
                    "ASR_INPUT_FORMAT_INVALID",
                    "CrispASR 输入 WAV 数据不完整；请先使用 FFmpeg 预处理",
                )
    except WorkflowASRError:
        raise
    except (EOFError, OSError, wave.Error) as exc:
        raise WorkflowASRError(
            "ASR_INPUT_FORMAT_INVALID",
            "CrispASR 输入不是可读取的 PCM WAV；请先使用 FFmpeg 预处理",
        ) from exc


async def _join_thread(task: asyncio.Task) -> None:
    """Wait for a to_thread operation without cancelling its worker thread."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated shutdown/cancel requests must not abandon the worker.
            continue
        except Exception:
            break
    if task.done() and not task.cancelled():
        task.exception()


async def _validate_wav_cancellable(path: Path, cancel_event: asyncio.Event) -> None:
    if cancel_event.is_set():
        raise WorkflowASRCancelled
    task = asyncio.create_task(asyncio.to_thread(_validate_wav, path))
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_wait in done and cancel_wait.result():
            await _join_thread(task)
            raise WorkflowASRCancelled
        return await task
    except asyncio.CancelledError:
        await _join_thread(task)
        raise
    finally:
        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)


def _snapshot_transcript(transcript: Transcript, project_id: str, config: ASRConfig):
    result = transcript.model_copy(deep=True)
    result.project_id = project_id
    result.stage = "raw"
    result.language = config.language
    result.metadata = {
        "provider": config.provider,
        "backend": config.backend,
        "model": config.model,
    }
    for segment in result.segments:
        segment.language = config.language
    try:
        validate_workflow_transcript(result)
    except ValueError as exc:
        raise WorkflowASRError("ASR_RESULT_INVALID", "ASR 结果缺少有效原文、ID 或时间范围") from exc
    if result.duration is not None and (not math.isfinite(result.duration) or result.duration < 0):
        raise WorkflowASRError("ASR_RESULT_INVALID", "ASR 结果时长无效")
    return result


def _write_bytes_atomic(
    path: Path,
    payload: bytes,
    cancellation: threading.Event,
    publish_lock: threading.Lock,
) -> tuple[int, str] | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    digest = hashlib.sha256(payload).hexdigest()
    try:
        if cancellation.is_set():
            return None
        with temp.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with publish_lock:
            if cancellation.is_set():
                return None
            os.replace(temp, path)
        return len(payload), digest
    finally:
        temp.unlink(missing_ok=True)


async def _write_bytes(path: Path, payload: bytes, cancel_event: asyncio.Event):
    if cancel_event.is_set():
        raise WorkflowASRCancelled
    cancellation = threading.Event()
    publish_lock = threading.Lock()
    task = asyncio.create_task(
        asyncio.to_thread(_write_bytes_atomic, path, payload, cancellation, publish_lock)
    )
    cancel_wait = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED)
        if cancel_wait in done and cancel_wait.result():
            with publish_lock:
                cancellation.set()
            await _join_thread(task)
            path.unlink(missing_ok=True)
            raise WorkflowASRCancelled
        result = await task
        if result is None or cancel_event.is_set():
            path.unlink(missing_ok=True)
            raise WorkflowASRCancelled
        return result
    except asyncio.CancelledError:
        with publish_lock:
            cancellation.set()
        await _join_thread(task)
        path.unlink(missing_ok=True)
        raise
    finally:
        cancel_wait.cancel()
        await asyncio.gather(cancel_wait, return_exceptions=True)


async def execute_asr_node(
    store,
    run: WorkflowRun,
    node,
    audio_path: Path,
    project: Project,
    cancel_event: asyncio.Event,
    settings: ASRSettings,
) -> tuple[WorkflowArtifact, TranscriptData]:
    config = validate_asr_config(node.config)
    output_path = store.workflow_artifact_path(
        run.project_id, run.audio_id, run.id, node.id, "transcript.json"
    )
    if config.provider == "crispasr":
        await _validate_wav_cancellable(audio_path, cancel_event)
        provider = CrispASRProvider(
            settings.executable,
            settings.model_dir,
            config,
            timeout_seconds=settings.timeout_seconds,
            cancel_event=cancel_event,
            process_factory=settings.process_factory,
        )
        try:
            transcript = await provider.transcribe(audio_path, project)
        except CrispASRError as error:
            if error.code == "ASR_CANCELLED" and cancel_event.is_set():
                raise WorkflowASRCancelled from error
            raise WorkflowASRError(error.code, str(error)) from error
    else:
        delay_task = asyncio.create_task(asyncio.sleep(settings.mock_delay_ms / 1000))
        cancel_wait = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {delay_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait in done and cancel_wait.result():
                delay_task.cancel()
                await asyncio.gather(delay_task, return_exceptions=True)
                raise WorkflowASRCancelled
            await delay_task
        except asyncio.CancelledError:
            delay_task.cancel()
            await asyncio.gather(delay_task, return_exceptions=True)
            raise
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
        provider = MockASRProvider(0)
        transcript_task = asyncio.create_task(provider.transcribe(audio_path, project))
        cancel_wait = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {transcript_task, cancel_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_wait in done and cancel_wait.result():
                transcript_task.cancel()
                await asyncio.gather(transcript_task, return_exceptions=True)
                raise WorkflowASRCancelled
            transcript = await transcript_task
        except asyncio.CancelledError:
            transcript_task.cancel()
            await asyncio.gather(transcript_task, return_exceptions=True)
            raise
        finally:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)
    if cancel_event.is_set():
        raise WorkflowASRCancelled
    try:
        transcript = _snapshot_transcript(transcript, project.id, config)
    except WorkflowASRError:
        raise
    except Exception as exc:
        raise WorkflowASRError("ASR_RESULT_INVALID", "ASR 结果无效") from exc
    payload = transcript.model_dump_json(indent=2).encode("utf-8") + b"\n"
    size, digest = await _write_bytes(output_path, payload, cancel_event)
    artifact = WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        port="transcript",
        filename="transcript.json",
        media_type="application/json",
        size_bytes=size,
        sha256=digest,
        download_url=(
            f"/api/v1/projects/{run.project_id}/audios/{run.audio_id}"
            f"/workflow-runs/{run.id}/outputs/{node.id}/download"
        ),
    )
    return artifact, TranscriptData(path=output_path, transcript=transcript)


def _normalized_source(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _render_text(transcript: Transcript, format_name: str) -> str:
    validate_workflow_transcript(transcript)
    segments = []
    for segment in transcript.segments:
        source = _normalized_source(segment.source_text)
        segments.append(
            segment.model_copy(update={"source_text": source, "translated_text": source})
        )
    if format_name == "txt":
        return "".join(f"{segment.source_text}\n" for segment in segments)
    if format_name == "srt":
        return render_srt(segments, bilingual=False)
    if format_name == "vtt":
        return render_vtt(segments, bilingual=False)
    if format_name == "lrc":
        return render_lrc(segments)
    raise ValueError("unsupported text format")


async def execute_text_output_node(
    store,
    run: WorkflowRun,
    node,
    transcript_data: TranscriptData,
    cancel_event: asyncio.Event,
) -> WorkflowArtifact:
    config: TextOutputConfig = validate_text_output_config(node.config)
    try:
        payload = _render_text(transcript_data.transcript, config.format).encode("utf-8")
    except ValueError as error:
        raise WorkflowASRError(
            "TEXT_OUTPUT_TIMESTAMPS_INVALID",
            f"无法导出 {config.format.upper()}：片段需要完整、有效的时间范围",
        ) from error
    filename = f"{config.filename_stem}.{config.format}"
    target = store.workflow_artifact_path(run.project_id, run.audio_id, run.id, node.id, filename)
    size, digest = await _write_bytes(target, payload, cancel_event)
    return WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        port="text",
        filename=filename,
        media_type=MEDIA_TYPES[config.format],
        size_bytes=size,
        sha256=digest,
        download_url=(
            f"/api/v1/projects/{run.project_id}/audios/{run.audio_id}"
            f"/workflow-runs/{run.id}/outputs/{node.id}/download"
        ),
    )


__all__ = [
    "ASRSettings",
    "TranscriptData",
    "WorkflowASRCancelled",
    "WorkflowASRError",
    "execute_asr_node",
    "execute_text_output_node",
]
