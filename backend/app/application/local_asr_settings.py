"""Local-only, restart-required edits of two server-controlled ASR settings."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..config import AppSettings
from .platform_io import FileLock, set_mode_from
from .platform_process import spawn_contained, terminate_contained

FIELDS = ("crispasr_executable", "crispasr_model_dir")
VERSION = re.compile(r"\bcrispasr\b[^\r\n]*?\bv?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)", re.I)
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")


def parse_version(output: str) -> str | None:
    """Recognize branded output or the official CLI's complete GGML build report."""
    if match := VERSION.search(output):
        return match[1]
    lines = output.strip().splitlines()
    if not lines or lines[0].strip() != "=== build info ===":
        return None
    metadata = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip()
    required = {
        "version",
        "git sha",
        "git date",
        "git subject",
        "build date",
        "build type",
        "compiler",
        "os",
        "arch",
        "ggml backends",
    }
    version = metadata.get("version", "")
    if (
        not all(metadata.get(key) for key in required)
        or not SEMVER.fullmatch(version)
        or not re.fullmatch(r"[0-9a-fA-F]{7,40}", metadata["git sha"])
        or "cpu" not in metadata["ggml backends"].split(",")
    ):
        return None
    return version


def error(status: int, code: str, message: str):
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


class LocalASRPaths(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    crispasr_executable: str = Field(min_length=1, max_length=4096)
    crispasr_model_dir: str = Field(min_length=1, max_length=4096)


class LocalASRUpdate(LocalASRPaths):
    revision: str = Field(min_length=1, max_length=128)


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def require_local_request(request: Request):
    """Trust the actual peer and Host; forwarded headers confer no authority."""
    # Uvicorn may already have replaced scope.client using proxy headers.
    # This management surface deliberately does not support forwarded access.
    if any(name == "forwarded" or name.startswith("x-forwarded-") for name in request.headers):
        raise error(403, "SETTINGS_LOCAL_ONLY", "全局设置不支持代理转发标头")
    try:
        host = urlsplit("//" + request.headers.get("host", ""))
        host_port = host.port or (443 if request.url.scheme == "https" else 80)
        valid_host = (
            _loopback(host.hostname)
            and host.username is None
            and not host.path
            and not host.query
            and not host.fragment
        )
        origin_value = request.headers.get("origin")
        valid_origin = True
        if origin_value:
            origin = urlsplit(origin_value)
            origin_port = origin.port or (443 if origin.scheme == "https" else 80)
            valid_origin = (
                origin.scheme == request.url.scheme
                and origin.hostname == host.hostname
                and origin_port == host_port
                and origin.username is None
                and not origin.path
                and not origin.query
                and not origin.fragment
            )
    except ValueError:
        valid_host = valid_origin = False
    if (
        not request.client
        or not _loopback(request.client.host)
        or not valid_host
        or not valid_origin
    ):
        raise error(403, "SETTINGS_LOCAL_ONLY", "全局设置仅允许本机同源访问")


async def _create_process(executable: str):
    return await spawn_contained(
        [executable, "--version"],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


class LocalASRSettings:
    def __init__(self, config_path: Path, settings: AppSettings):
        self.config_path = config_path.resolve()
        self.root = self.config_path.parent.parent
        self.active = {key: str(self._resolve(str(getattr(settings, key)))) for key in FIELDS}
        self.managed = {key: os.getenv(f"VOICETRANSL_{key.upper()}") is not None for key in FIELDS}
        self.allowed_roots = tuple(
            dict.fromkeys(
                [self._resolve(str(root)) for root in settings.crispasr_allowed_roots]
                + [Path(self.active[FIELDS[0]]).parent, Path(self.active[FIELDS[1]])]
            )
        )
        self.external_roots = tuple(
            root for root in self.allowed_roots if not root.is_relative_to(self.root)
        )
        self.lock = asyncio.Lock()
        self.process_factory = _create_process
        self.check_timeout = 5.0

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def _display(self, path: str | Path) -> str:
        """Return reversible labels without disclosing deployment prefixes."""
        path = Path(path)
        if path.is_relative_to(self.root):
            relative = path.relative_to(self.root).as_posix()
            return "./" if relative == "." else f"./{relative}"
        candidates = [
            (index, root)
            for index, root in enumerate(self.external_roots, start=1)
            if path.is_relative_to(root)
        ]
        if not candidates:
            return "@unavailable"
        index, root = max(candidates, key=lambda item: len(item[1].parts))
        suffix = path.relative_to(root).as_posix()
        return f"@root/{index}" + (f"/{suffix}" if suffix != "." else "")

    def _candidate(self, value: str) -> Path:
        if value.startswith("@"):
            match = re.fullmatch(r"@root/([1-9][0-9]*)(?:/(.*))?", value)
            if not match:
                raise error(
                    422,
                    "PATH_NOT_ALLOWED",
                    "路径标签无效或尚未授权，请在服务器配置允许目录并重启后端",
                )
            index = int(match[1])
            suffix = match[2] or ""
            if index > len(self.external_roots) or suffix.startswith("/"):
                raise error(
                    422,
                    "PATH_NOT_ALLOWED",
                    "路径标签未授权，请重新加载设置或由管理员配置目录后重启",
                )
            return self.external_roots[index - 1] / suffix
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def _read(self):
        try:
            data = self.config_path.read_bytes()
            raw = yaml.safe_load(data) or {}
            if not isinstance(raw, dict) or not isinstance(raw.get("app", {}), dict):
                raise ValueError("configuration must be a mapping")
            return data, raw
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise error(503, "SETTINGS_UNAVAILABLE", "无法读取设置文件，请检查后端配置") from exc

    def _editable(self) -> bool:
        try:
            return all(
                path.stat().st_mode & 0o222 and os.access(path, os.W_OK)
                for path in (self.config_path, self.config_path.parent)
            ) and os.access(self.config_path.parent, os.X_OK)
        except OSError:
            return False

    def _saved_paths(self, raw: dict) -> dict[str, str]:
        defaults = AppSettings()
        return {
            key: self.active[key]
            if self.managed[key]
            else str(self._resolve(str(raw.get("app", {}).get(key, getattr(defaults, key)))))
            for key in FIELDS
        }

    def _view(self, data: bytes, raw: dict) -> dict:
        saved = self._saved_paths(raw)
        return {
            **{key: self._display(value) for key, value in saved.items()},
            "active": {key: self._display(value) for key, value in self.active.items()},
            "managed_by_environment": self.managed.copy(),
            "restart_required": saved != self.active,
            "editable": self._editable(),
            "allowed_roots": [self._display(root) for root in self.allowed_roots],
            "revision": hashlib.sha256(data).hexdigest(),
        }

    def get(self):
        return self._view(*self._read())

    def _trusted_path(self, value: str, *, executable: bool = False) -> Path:
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value) or "\\" in value:
            raise error(422, "PATH_NOT_ALLOWED", "路径包含不安全字符")
        if ".." in Path(value).parts:
            raise error(422, "PATH_NOT_ALLOWED", "路径不能包含上级目录跳转")
        try:
            candidate = self._candidate(value)
            path = candidate.resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise error(422, "PATH_NOT_ALLOWED", "路径无法安全解析") from exc
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise error(422, "PATH_NOT_ALLOWED", "路径不在服务器允许的目录内")
        names = {"crispasr", "crispasr.exe"} if os.name == "nt" else {"crispasr"}
        if executable and (candidate.name not in names or path.name not in names):
            raise error(422, "PATH_NOT_ALLOWED", "可执行文件名称必须为 crispasr")
        return path

    async def _version(self, path: Path) -> dict:
        result = {"ok": False, "message": "CrispASR 可执行文件不存在或不可执行", "version": None}
        try:
            executable_ok = path.is_file() and os.access(path, os.X_OK)
        except OSError:
            executable_ok = False
        if not executable_ok:
            return result
        process = None
        tasks = []

        async def read_limited(stream):
            data = bytearray()
            while chunk := await stream.read(1024):
                data.extend(chunk)
                if len(data) > 8192:
                    raise ValueError("version output too large")
            return bytes(data)

        try:
            async with asyncio.timeout(self.check_timeout):
                process = await self.process_factory(str(path))
                tasks = [
                    asyncio.create_task(read_limited(process.stdout)),
                    asyncio.create_task(read_limited(process.stderr)),
                    asyncio.create_task(process.wait()),
                ]
                stdout, stderr, returncode = await asyncio.gather(*tasks)
                version = parse_version((stdout + b"\n" + stderr).decode("utf-8", errors="replace"))
                if returncode == 0 and version:
                    return {"ok": True, "message": "CrispASR 版本检查通过", "version": version}
                result["message"] = "未识别到有效的 CrispASR 版本，请检查可执行文件"
        except TimeoutError:
            result["message"] = "CrispASR 版本检查超时"
        except (OSError, ValueError):
            result["message"] = "无法执行 CrispASR 版本检查，或输出超出限制"
        finally:
            if process is not None:
                # The process group/session also covers descendants holding
                # stdout/stderr open.
                await terminate_contained(process)
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        return result

    async def check(self, paths: LocalASRPaths):
        executable = self._trusted_path(paths.crispasr_executable, executable=True)
        model_dir = self._trusted_path(paths.crispasr_model_dir)
        try:
            directory_ok = (
                model_dir.is_dir()
                and bool(model_dir.stat().st_mode & 0o444)
                and bool(model_dir.stat().st_mode & 0o111)
                and os.access(model_dir, os.R_OK | os.X_OK)
            )
        except OSError:
            directory_ok = False
        return {
            "executable": await self._version(executable),
            "model_dir": {
                "ok": directory_ok,
                "message": "模型目录可访问" if directory_ok else "模型目录不存在或无法读取",
            },
        }

    def _atomic_write(self, data: bytes):
        descriptor, name = tempfile.mkstemp(prefix=".app-settings-", dir=self.config_path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                set_mode_from(self.config_path, file)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.config_path)
        finally:
            temporary.unlink(missing_ok=True)

    async def update(self, values: LocalASRUpdate):
        async with self.lock:
            data, raw = self._read()
            current = self._view(data, raw)
            current_paths = self._saved_paths(raw)
            if values.revision != current["revision"]:
                raise error(409, "SETTINGS_REVISION_CONFLICT", "设置已更新，请重新加载后保存")
            for key in FIELDS:
                if (
                    self.managed[key]
                    and str(self._trusted_path(getattr(values, key), executable=key == FIELDS[0]))
                    != current_paths[key]
                ):
                    raise error(422, "SETTINGS_ENVIRONMENT_MANAGED", "环境变量管理的设置无法修改")
            result = await self.check(values)
            if not all(item["ok"] for item in result.values()):
                raise error(422, "SETTINGS_VALIDATION_FAILED", "本地 ASR 检查未通过，请先检查路径")
            if not current["editable"]:
                raise error(503, "SETTINGS_NOT_EDITABLE", "设置文件不可写，请检查文件和目录权限")
            try:
                with FileLock(self.config_path.with_suffix(".yaml.lock")):
                    data, raw = self._read()
                    if hashlib.sha256(data).hexdigest() != values.revision:
                        raise error(
                            409, "SETTINGS_REVISION_CONFLICT", "设置已更新，请重新加载后保存"
                        )
                    app = raw.setdefault("app", {})
                    changed = False
                    for key in FIELDS:
                        if self.managed[key]:
                            continue
                        desired = str(
                            self._trusted_path(getattr(values, key), executable=key == FIELDS[0])
                        )
                        if desired != current_paths[key]:
                            app[key] = desired
                            changed = True
                    if changed:
                        self._atomic_write(
                            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False).encode("utf-8")
                        )
                    return self.get()
            except OSError as exc:
                raise error(503, "SETTINGS_WRITE_FAILED", "保存设置失败，原设置文件已保留") from exc
