"""FFmpeg backed audio normalization for the preprocess stage."""

from __future__ import annotations

import asyncio
import inspect
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..domain import PreprocessConfig


class PreprocessError(RuntimeError):
    """Stable, user-facing errors raised by audio preprocessing."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


Runner = Callable[[list[str]], Any | Awaitable[Any]]


def _run_ffmpeg(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


class FFmpegPreprocessProvider:
    """Normalize an input audio file into a project-local WAV artifact.

    ``runner`` is injectable so tests and embedded deployments can replace the
    subprocess call without patching the provider. It may be synchronous or
    asynchronous and receives the exact argument list passed to FFmpeg.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg", runner: Runner | None = None):
        self.ffmpeg_path = ffmpeg_path
        self.runner = runner or _run_ffmpeg

    async def preprocess(
        self, audio: Path, output: Path, config: PreprocessConfig
    ) -> dict[str, object]:
        if not audio.is_file():
            raise PreprocessError("PREPROCESS_INPUT_MISSING", "源音频文件不存在")
        if output.resolve() == audio.resolve():
            raise PreprocessError("PREPROCESS_SOURCE_OVERWRITE", "预处理不能覆盖原始音频")
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio),
            "-ac",
            str(config.channels),
            "-ar",
            str(config.sample_rate),
            "-vn",
            str(output),
        ]
        try:
            result = await asyncio.to_thread(self.runner, command)
            if inspect.isawaitable(result):
                result = await result
        except FileNotFoundError as exc:
            raise PreprocessError(
                "PREPROCESS_FFMPEG_NOT_FOUND", "未找到 FFmpeg，请先安装并配置 FFmpeg"
            ) from exc
        except OSError as exc:
            raise PreprocessError("PREPROCESS_FFMPEG_UNAVAILABLE", "无法启动 FFmpeg") from exc

        return_code = getattr(result, "returncode", None)
        if return_code not in (0, None):
            # stderr is retained in server diagnostics by the caller; never
            # include arbitrary command output in the persisted job error.
            raise PreprocessError("PREPROCESS_FFMPEG_FAILED", "FFmpeg 音频转换失败")
        if not output.is_file():
            raise PreprocessError("PREPROCESS_OUTPUT_MISSING", "FFmpeg 未生成预处理音频")
        return {
            "status": "completed",
            "provider": "ffmpeg",
            "source_path": str(audio),
            "output_path": str(output),
            "sample_rate": config.sample_rate,
            "channels": config.channels,
            "preserve_original": config.preserve_original,
        }


__all__ = ["FFmpegPreprocessProvider", "PreprocessError"]
