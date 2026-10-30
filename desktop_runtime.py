"""Shared runtime helpers for the desktop application.

Most file handling stays in the standard library. This module only localises
operations whose meaning genuinely differs between supported systems: console
window suppression, shell command formatting, shutdown, and tool discovery.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def subprocess_creation_flags() -> int:
    """Hide console helper windows where the host provides that control."""
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def split_command(command: str) -> list[str]:
    """Split a command template using host-appropriate quoting rules."""
    if os.name != "nt":
        return shlex.split(command)
    tokens = shlex.split(command, posix=False)
    return [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
        else token
        for token in tokens
    ]


def format_command(command: Sequence[str]) -> str:
    """Render a command for logs using the host's quoting rules."""
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return shlex.join(command)


def resolve_executable(candidate: str | os.PathLike[str]) -> Path | None:
    """Resolve an explicit file or a bare command without hard-coding suffixes."""
    value = os.fspath(candidate)
    if not value:
        return None

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_file():
        return path.resolve()

    if Path(value).parent == Path("."):
        found = shutil.which(value)
        if found:
            return Path(found).resolve()
    return None


def find_tool(name: str, bundled_path: str | os.PathLike[str] | None = None) -> str:
    """Prefer a bundled tool, then fall back to the process PATH."""
    if bundled_path is not None:
        resolved = resolve_executable(bundled_path)
        if resolved is not None:
            return str(resolved)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(name)


def ffmpeg_paths() -> tuple[str, str]:
    """Resolve ffmpeg and ffprobe, preferring the bundled directory."""
    ffmpeg = resolve_executable(Path("ffmpeg") / "ffmpeg")
    ffprobe = resolve_executable(Path("ffmpeg") / "ffprobe")
    if ffmpeg is None:
        found = shutil.which("ffmpeg")
        ffmpeg = Path(found) if found else None
    if ffprobe is None:
        found = shutil.which("ffprobe")
        ffprobe = Path(found) if found else None

    if ffmpeg is None:
        raise RuntimeError(
            "未找到 ffmpeg，请将 ffmpeg 放置在 ffmpeg 目录中，"
            "或安装 ffmpeg 并添加到系统 PATH 环境变量"
        )
    if ffprobe is None:
        # Keep ffmpeg-only operations usable when ffprobe is absent.
        ffprobe = ffmpeg.with_name("ffprobe")
    return str(ffmpeg), str(ffprobe)


def popen(args: Sequence[str], *, hide_console: bool = True, **kwargs):
    """Start a child process and apply the desktop console policy."""
    if hide_console:
        kwargs.setdefault("creationflags", subprocess_creation_flags())
    return subprocess.Popen(list(args), **kwargs)


def run(args: Sequence[str], *, hide_console: bool = True, check: bool = False, **kwargs):
    """Run a short-lived child process with the desktop console policy."""
    if hide_console:
        kwargs.setdefault("creationflags", subprocess_creation_flags())
    return subprocess.run(list(args), check=check, **kwargs)


def terminate_process(proc, *, timeout: float = 3.0) -> None:
    """Terminate and reap a child process, escalating when necessary."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        return
    proc.wait(timeout=timeout)


def shutdown_command() -> list[str]:
    """Return the host's immediate system shutdown command."""
    if sys.platform == "darwin":
        return ["osascript", "-e", 'tell application "System Events" to shut down']
    if os.name == "nt":
        return ["shutdown", "/s", "/t", "0"]
    return ["shutdown", "-h", "now"]


def start_shutdown() -> None:
    """Start the platform shutdown command without a console window."""
    popen(shutdown_command(), hide_console=True)