"""Cancellable CrispASR command line provider.

The provider deliberately accepts only server configured executable/model roots
and client supplied identifiers.  It never invokes a shell, and all generated
files live in a unique temporary directory which is removed after parsing.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..domain import ASRConfig, Project, Segment, Transcript


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
SUPPORTED_BACKENDS = frozenset(
    {"qwen3", "qwen3-1.7b", "whisper", "mega-asr", "voxtral", "voxtral4b", "granite"}
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
        if end <= start:
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
            if end <= start:
                raise ValueError("invalid JSON time range")
            confidence = item.get("confidence")
            segments.append(
                Segment(
                    id=f"seg_{len(segments) + 1:03d}",
                    start=start,
                    end=end,
                    source_text=source.strip(),
                    language=language,
                    confidence=float(confidence) if confidence is not None else None,
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
    return await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
    ):
        self.executable = Path(executable).expanduser()
        self.model_dir = Path(model_dir).expanduser()
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.cancel_event = cancel_event
        self.process_factory = process_factory or _default_process_factory

    def _model_path(self) -> Path:
        identifier = self.config.model
        if not _SAFE_IDENTIFIER.fullmatch(identifier) or identifier in {".", ".."}:
            raise CrispASRError("ASR_MODEL_INVALID", "识别模型标识符无效")
        root = self.model_dir.resolve()
        path = (root / identifier).resolve()
        if root not in path.parents or not path.is_file():
            raise CrispASRError("ASR_MODEL_NOT_FOUND", "找不到识别模型，请检查服务器模型目录")
        return path

    def command(self, audio: Path, output_base: Path) -> list[str]:
        backend = self.config.backend
        if not _SAFE_IDENTIFIER.fullmatch(backend) or backend not in SUPPORTED_BACKENDS:
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

    async def _terminate(self, process: Any) -> None:
        if getattr(process, "returncode", None) is not None:
            return
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=2)
        except (ProcessLookupError, asyncio.TimeoutError):
            if getattr(process, "returncode", None) is None:
                process.kill()
                await process.wait()

    async def _run(self, command: list[str]) -> tuple[int, bytes, bytes]:
        try:
            process = await self.process_factory(*command)
        except FileNotFoundError as exc:
            raise CrispASRError("ASR_EXECUTABLE_NOT_FOUND", "找不到 CrispASR 可执行文件") from exc
        except OSError as exc:
            raise CrispASRError(
                "ASR_EXECUTABLE_UNAVAILABLE", "无法启动 CrispASR 可执行文件"
            ) from exc
        communicate = asyncio.create_task(process.communicate())
        cancel_wait = (
            asyncio.create_task(self.cancel_event.wait()) if self.cancel_event is not None else None
        )
        try:
            waits = [communicate]
            if cancel_wait is not None:
                waits.append(cancel_wait)
            done, _ = await asyncio.wait(
                waits, timeout=self.timeout_seconds, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                await self._terminate(process)
                communicate.cancel()
                await asyncio.gather(communicate, return_exceptions=True)
                raise CrispASRError("ASR_TIMEOUT", "CrispASR 处理超时")
            if cancel_wait is not None and cancel_wait in done and cancel_wait.result():
                await self._terminate(process)
                communicate.cancel()
                await asyncio.gather(communicate, return_exceptions=True)
                raise CrispASRError("ASR_CANCELLED", "语音识别已取消")
            stdout, stderr = await communicate
            return int(getattr(process, "returncode", 0) or 0), stdout or b"", stderr or b""
        except asyncio.CancelledError:
            await self._terminate(process)
            communicate.cancel()
            await asyncio.gather(communicate, return_exceptions=True)
            raise
        finally:
            if cancel_wait is not None:
                cancel_wait.cancel()
                await asyncio.gather(cancel_wait, return_exceptions=True)

    async def transcribe(self, audio: Path, project: Project) -> Transcript:
        if not audio.is_file():
            raise CrispASRError("ASR_INPUT_MISSING", "预处理音频不存在")
        if not self.executable.is_file():
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
    "SUPPORTED_BACKENDS",
    "parse_json_transcript",
    "parse_srt",
    "parse_transcript",
]
