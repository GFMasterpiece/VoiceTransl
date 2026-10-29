"""Cancellable CrispASR command line provider.

The provider deliberately accepts only server configured executable/model roots
and client supplied identifiers. It never invokes a shell, and all generated
files live in a unique temporary directory which is removed after parsing.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..application.platform_process import ContainedProcess, spawn_contained, terminate_contained
from ..domain import ASRConfig, Project, Segment, Transcript

MAX_DIAGNOSTIC_BYTES = 256 * 1024
DEFAULT_TERMINATE_SECONDS = 2.0


class CrispASRError(RuntimeError):
    """Stable, readable errors from CrispASR."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TIMESTAMP = re.compile(r"^(?P<h>\d{1,4}):(?P<m>[0-5]?\d):(?P<s>[0-5]?\d)(?:[,.](?P<ms>\d{1,3}))?$")
_SRT_TIMING = re.compile(
    r"(?P<start>\d{1,4}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,4}:\d{2}:\d{2}[,.]\d{1,3})"
)


def _timestamp(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        # JSON exports in the wild use milliseconds for integer timestamps.
        if result > 100_000:
            result /= 1000
    elif isinstance(value, str):
        match = _TIMESTAMP.match(value.strip())
        if not match:
            try:
                result = float(value)
            except ValueError as exc:
                raise ValueError("invalid timestamp") from exc
        else:
            result = (
                int(match.group("h")) * 3600
                + int(match.group("m")) * 60
                + int(match.group("s"))
                + int((match.group("ms") or "0").ljust(3, "0")) / 1000
            )
    else:
        raise ValueError("invalid timestamp")
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid timestamp")
    return result


def parse_srt(text: str, *, language: str = "ja") -> list[Segment]:
    """Parse SRT cues while rejecting malformed text and time ranges."""

    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n{2,}", normalized.strip())
    segments: list[Segment] = []
    for block in blocks:
        lines = [line.strip() for line in block.split("\n")]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            raise ValueError("missing SRT timing")
        match = _SRT_TIMING.fullmatch(lines[timing_index])
        if not match:
            raise ValueError("invalid SRT timing")
        start, end = _timestamp(match.group("start")), _timestamp(match.group("end"))
        if end < start:
            raise ValueError("invalid SRT time range")
        source_text = "\n".join(lines[timing_index + 1 :]).strip()
        if not source_text:
            raise ValueError("SRT cue text is empty")
        segments.append(
            Segment(
                id=f"seg_{len(segments) + 1:03d}",
                start=start,
                end=end,
                source_text=source_text,
                language=language,
            )
        )
    if not segments:
        raise ValueError("SRT contains no cues")
    return segments


def _json_items(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("segments", "cues", "results", "transcript"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("JSON transcript must contain segments")


def parse_json_transcript(text: str, *, language: str = "ja") -> list[Segment]:
    try:
        payload = json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON transcript") from exc
    segments: list[Segment] = []
    try:
        for item in _json_items(payload):
            if not isinstance(item, dict):
                raise ValueError("invalid JSON segment")
            source = item.get("text", item.get("source_text", item.get("content")))
            if not isinstance(source, str) or not source.strip():
                raise ValueError("JSON segment text is empty")
            start = _timestamp(item.get("start", item.get("start_time")))
            end = _timestamp(item.get("end", item.get("end_time")))
            if end < start:
                raise ValueError("invalid JSON time range")
            confidence = item.get("confidence")
            if confidence is not None:
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError) as exc:
                    raise ValueError("invalid JSON confidence") from exc
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("invalid JSON confidence")
            segments.append(
                Segment(
                    id=f"seg_{len(segments) + 1:03d}",
                    start=start,
                    end=end,
                    source_text=source.strip(),
                    language=language,
                    confidence=confidence,
                )
            )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("invalid JSON transcript") from exc
    if not segments:
        raise ValueError("JSON transcript contains no segments")
    return segments


def parse_transcript(path: Path, *, language: str = "ja") -> list[Segment]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError("unable to read CrispASR output") from exc
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
        return parse_json_transcript(text, language=language)
    return parse_srt(text, language=language)


ProcessFactory = Callable[..., Awaitable[Any]]


async def _default_process_factory(*command: str):
    return await spawn_contained(
        command,
        stdin=asyncio.subprocess.DEVNULL,
        # The workflow only needs exit status and the generated subtitle file.
        # Discard diagnostics instead of buffering unbounded provider output.
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


class CrispASRProvider:
    def __init__(
        self,
        executable: str | Path,
        model_dir: str | Path,
        config: ASRConfig,
        *,
        timeout_seconds: float = 300,
        cancel_event: asyncio.Event | None = None,
        process_factory: ProcessFactory | None = None,
        terminate_seconds: float = DEFAULT_TERMINATE_SECONDS,
    ):
        self.executable = Path(executable).expanduser()
        self.model_dir = Path(model_dir).expanduser()
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.cancel_event = cancel_event
        self.process_factory = process_factory or _default_process_factory
        self.terminate_seconds = terminate_seconds

    def _model_path(self) -> Path:
        identifier = self.config.model
        if (
            not _SAFE_IDENTIFIER.fullmatch(identifier)
            or identifier in {".", ".."}
            or identifier.endswith(".")
        ):
            raise CrispASRError("ASR_MODEL_INVALID", "识别模型标识符无效")
        try:
            root = self.model_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CrispASRError(
                "ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录"
            ) from exc
        candidate = root / identifier
        # A child symlink could escape the configured model root or change
        # after resolve(). Inspect the directory entry itself and require a
        # regular file before resolving the parent and child together.
        try:
            entry = os.lstat(candidate)
        except OSError as exc:
            raise CrispASRError(
                "ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录"
            ) from exc
        if not stat.S_ISREG(entry.st_mode):
            raise CrispASRError("ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录")
        try:
            path = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CrispASRError(
                "ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录"
            ) from exc
        if root not in path.parents:
            raise CrispASRError("ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录")
        return path

    def command(self, audio: Path, output_base: Path) -> list[str]:
        backend = self.config.backend
        if not _SAFE_IDENTIFIER.fullmatch(backend):
            raise CrispASRError("ASR_BACKEND_UNSUPPORTED", "不支持所选 CrispASR 后端")
        return [
            str(self.executable),
            "--backend",
            backend,
            "--model",
            str(self._model_path()),
            "--language",
            self.config.language,
            "--output-srt",
            "--output-file",
            str(output_base),
            "--file",
            str(audio),
            *(
                ["--vad", "--vad-max-speech-duration-s", str(self.config.chunk_seconds)]
                if self.config.vad_enabled
                else []
            ),
        ]

    async def _reap(self, process: Any) -> None:
        """Wait for a process even if returncode was already observed."""

        wait = getattr(process, "wait", None)
        if wait is None:
            return
        try:
            await wait()
        except (ProcessLookupError, ChildProcessError):
            pass

    async def _reap_uncancelled(self, process: Any) -> None:
        """Run one wait operation in a cleanup task that callers cannot interrupt."""

        await self._reap(process)

    async def _terminate(self, process: Any) -> None:
        """Terminate the contained tree and wait without abandoning cleanup."""

        if isinstance(process, ContainedProcess):
            cleanup = asyncio.create_task(terminate_contained(process))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            await cleanup
            return
        wait_task: asyncio.Task[None] | None = None
        if getattr(process, "returncode", None) is None:
            try:
                terminate = getattr(process, "terminate", None)
                if terminate is not None:
                    terminate()
            except ProcessLookupError:
                pass
            wait_task = asyncio.create_task(self._reap_uncancelled(process))
            done, _ = await asyncio.wait({wait_task}, timeout=self.terminate_seconds)
            if not done and getattr(process, "returncode", None) is None:
                try:
                    kill = getattr(process, "kill", None)
                    if kill is not None:
                        kill()
                except ProcessLookupError:
                    pass
        if wait_task is not None:
            await asyncio.shield(wait_task)
        else:
            wait_task = asyncio.create_task(self._reap_uncancelled(process))
            await asyncio.shield(wait_task)

    async def _cleanup_resources(
        self,
        *,
        spawn_task: asyncio.Task[Any],
        spawn_result_consumed: bool,
        cancel_wait: asyncio.Task[Any] | None,
        process: Any | None,
        communicate_task: asyncio.Task[Any] | None,
        process_succeeded: bool,
    ) -> None:
        """Settle child/output tasks and always release the cancel waiter."""

        if communicate_task is not None:
            if not communicate_task.done():
                communicate_task.cancel()
            await asyncio.gather(communicate_task, return_exceptions=True)
        if process is None and not spawn_result_consumed:
            # If the factory swallowed cancellation and returned a real child,
            # this is the only place that sees the result. If spawn itself
            # failed, that failure was already mapped by the main path and must
            # not be re-raised here.
            spawned = await self._settle_spawn(spawn_task)
            if not isinstance(spawned, BaseException):
                process = spawned
        if process is not None:
            if process_succeeded:
                await self._reap(process)
            else:
                await self._terminate(process)
        if cancel_wait is not None:
            cancel_wait.cancel()
            await asyncio.gather(cancel_wait, return_exceptions=True)

    async def _settle_spawn(self, spawn_task: asyncio.Task[Any]) -> Any:
        """Wait for the factory to settle and classify cancelled/failed spawn."""

        while not spawn_task.done():
            try:
                await asyncio.wait({spawn_task}, timeout=0.05)
            except asyncio.CancelledError:
                continue
        if spawn_task.cancelled():
            return asyncio.CancelledError()
        try:
            return spawn_task.result()
        except asyncio.CancelledError:
            return asyncio.CancelledError()
        except BaseException as exc:
            return exc

    async def _run(self, command: list[str]) -> tuple[int, bytes, bytes]:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CrispASRError("ASR_CANCELLED", "语音识别已取消")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        spawn_task = asyncio.create_task(self.process_factory(*command))
        cancel_wait = (
            asyncio.create_task(self.cancel_event.wait()) if self.cancel_event is not None else None
        )
        process = None
        communicate_task = None
        process_succeeded = False
        spawn_result_consumed = False
        pending_cancel = False

        try:
            waits = {spawn_task}
            if cancel_wait is not None:
                waits.add(cancel_wait)
            while True:
                try:
                    done, _ = await asyncio.wait(
                        waits,
                        timeout=max(0.0, deadline - loop.time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    pending_cancel = True
                    spawned = await self._settle_spawn(spawn_task)
                    spawn_result_consumed = True
                    if isinstance(spawned, BaseException):
                        raise asyncio.CancelledError from spawned
                    process = spawned
                    raise asyncio.CancelledError
                if not done:
                    raise CrispASRError("ASR_TIMEOUT", "CrispASR 处理超时")
                if cancel_wait is not None and cancel_wait in done and cancel_wait.result():
                    raise CrispASRError("ASR_CANCELLED", "语音识别已取消")
                if spawn_task in done:
                    spawned = await self._settle_spawn(spawn_task)
                    spawn_result_consumed = True
                    if isinstance(spawned, BaseException):
                        if isinstance(spawned, FileNotFoundError):
                            raise CrispASRError(
                                "ASR_EXECUTABLE_NOT_FOUND", "找不到 CrispASR 可执行文件"
                            ) from spawned
                        if isinstance(spawned, OSError):
                            raise CrispASRError(
                                "ASR_EXECUTABLE_UNAVAILABLE", "无法启动 CrispASR 可执行文件"
                            ) from spawned
                        raise spawned
                    process = spawned
                    break

            if process is None:
                raise CrispASRError("ASR_EXECUTABLE_UNAVAILABLE", "无法启动 CrispASR 可执行文件")
            communicate_task = asyncio.create_task(process.communicate())
            waits = {communicate_task}
            if cancel_wait is not None:
                waits.add(cancel_wait)
            while True:
                try:
                    done, _ = await asyncio.wait(
                        waits,
                        timeout=max(0.0, deadline - loop.time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    pending_cancel = True
                    raise
                if not done:
                    raise CrispASRError("ASR_TIMEOUT", "CrispASR 处理超时")
                if cancel_wait is not None and cancel_wait in done and cancel_wait.result():
                    raise CrispASRError("ASR_CANCELLED", "语音识别已取消")
                if communicate_task in done:
                    try:
                        stdout, stderr = await communicate_task
                    except asyncio.CancelledError:
                        pending_cancel = True
                        raise
                    process_succeeded = True
                    return (
                        int(getattr(process, "returncode", 0) or 0),
                        stdout or b"",
                        stderr or b"",
                    )
        finally:
            # Cleanup always owns the child and event waiter. If the caller is
            # cancelled during cleanup, finish cleanup before propagating that
            # cancellation; never silently swallow it.
            cleanup = asyncio.create_task(
                self._cleanup_resources(
                    spawn_task=spawn_task,
                    spawn_result_consumed=spawn_result_consumed,
                    cancel_wait=cancel_wait,
                    process=process,
                    communicate_task=communicate_task,
                    process_succeeded=process_succeeded,
                )
            )
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    pending_cancel = True
                    continue
            await cleanup
            if pending_cancel:
                raise asyncio.CancelledError

    async def transcribe(self, audio: Path, project: Project) -> Transcript:
        if not audio.is_file():
            raise CrispASRError("ASR_INPUT_MISSING", "预处理音频不存在")
        if not self.executable.is_file() or self.executable.is_symlink():
            raise CrispASRError("ASR_EXECUTABLE_NOT_FOUND", "找不到 CrispASR 可执行文件")
        self.model_dir = self.model_dir.resolve()
        with tempfile.TemporaryDirectory(prefix="crispasr_", dir=str(audio.parent)) as temp:
            output_base = Path(temp) / "transcript"
            command = self.command(audio, output_base)
            return_code, _stdout, _stderr = await self._run(command)
            if return_code != 0:
                raise CrispASRError("ASR_PROCESS_FAILED", "CrispASR 执行失败")
            outputs = [
                output_base.with_suffix(".srt"),
                output_base.with_suffix(".json"),
                output_base,
            ]
            output = next(
                (path for path in outputs if path.is_file() and path.stat().st_size), None
            )
            if output is None:
                raise CrispASRError("ASR_OUTPUT_MISSING", "CrispASR 未生成字幕输出")
            try:
                segments = parse_transcript(output, language=self.config.language)
            except ValueError as exc:
                raise CrispASRError("ASR_OUTPUT_INVALID", "CrispASR 输出字幕格式无效") from exc
        return Transcript(
            project_id=project.id,
            stage="raw",
            language=self.config.language,
            duration=max(float(s.end or 0) for s in segments),
            created_at=datetime.now(timezone.utc),
            segments=segments,
            metadata={
                "provider": "crispasr",
                "backend": self.config.backend,
                "model": self.config.model,
            },
        )


__all__ = [
    "CrispASRError",
    "CrispASRProvider",
    "parse_json_transcript",
    "parse_srt",
    "parse_transcript",
]
