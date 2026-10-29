"""Bounded local GGUF discovery; metadata hints never load a model or its weights."""

from __future__ import annotations

import asyncio
import json
import os
import re
import stat
import struct
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from .local_asr_settings import error
from .platform_io import IS_WINDOWS, open_model_root, pread
from .platform_process import spawn_contained, terminate_contained

MAX_ENTRIES = 4096
MAX_MODELS = 128
SCAN_SECONDS = 5.0
FILE_SECONDS = 2.0
MAX_METADATA_BYTES = 16 * 1024 * 1024
MAX_KV = 1024
MAX_TENSORS = 8192
MAX_ARRAY_VALUES = 1_000_000
MAX_STRING_BYTES = 65536
MAX_DEPTH = 4
CLI_OUTPUT_BYTES = 256 * 1024
SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
SAFE_ARCHITECTURE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
# Only this mapping has been verified with the supported local CLI. In particular,
# a language-model qwen3 architecture is not the ASR architecture qwen3asr.
ARCHITECTURE_BACKENDS = {"qwen3asr": "qwen3"}
SCALAR_BYTES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


class MetadataInvalid(ValueError):
    pass


class MetadataLimit(MetadataInvalid):
    pass


class ScanStopped(Exception):
    pass


class GGUFReader:
    def __init__(self, file, size: int, deadline: float, stop: threading.Event):
        self.file = file
        self.descriptor = file.fileno()
        self.position = 0
        self.size = size
        self.deadline = deadline
        self.stop = stop
        self.array_values = 0

    def guard(self, count: int = 0):
        if self.stop.is_set():
            raise ScanStopped
        if time.monotonic() > self.deadline:
            raise MetadataLimit("模型元信息检查超出时间限额")
        if count < 0 or self.position + count > MAX_METADATA_BYTES:
            raise MetadataLimit("模型元信息超出读取字节限额")
        if self.position + count > self.size:
            raise MetadataInvalid("GGUF 元信息或描述符不完整")

    def read(self, count: int) -> bytes:
        self.guard(count)
        # A virtual cursor avoids repeated tell/seek syscalls for large tokenizer
        # arrays. Exact positional reads never prefetch tensor weights or mmap
        # files that an administrator might truncate during a scan.
        read_at = getattr(self.file, "read_at", None)
        value = (
            read_at(count, self.position)
            if read_at is not None
            else pread(self.descriptor, count, self.position)
        )
        if len(value) != count:
            raise MetadataInvalid("GGUF 元信息读取不完整")
        self.position += count
        return value

    def integer(self, fmt: str) -> int:
        return struct.unpack("<" + fmt, self.read(struct.calcsize("<" + fmt)))[0]

    def skip(self, count: int):
        self.guard(count)
        self.position += count

    def string(self, *, capture: bool = False) -> str | None:
        count = self.integer("Q")
        if count > MAX_STRING_BYTES:
            raise MetadataLimit("GGUF 字符串超出读取限额")
        if not capture:
            self.skip(count)
            return None
        try:
            return self.read(count).decode("utf-8")
        except UnicodeError as exc:
            raise MetadataInvalid("GGUF 字符串格式无效") from exc

    def skip_value(self, kind: int, depth: int = 0):
        self.guard()
        if depth > MAX_DEPTH:
            raise MetadataLimit("GGUF 数组嵌套超出限额")
        if kind in SCALAR_BYTES:
            self.skip(SCALAR_BYTES[kind])
        elif kind == 8:
            self.string()
        elif kind == 9:
            element_kind, count = self.integer("I"), self.integer("Q")
            if element_kind not in SCALAR_BYTES and element_kind not in {8, 9}:
                raise MetadataInvalid("GGUF 数组类型无效")
            self.array_values += count
            if self.array_values > MAX_ARRAY_VALUES:
                raise MetadataLimit("GGUF 数组元素数量超出限额")
            if element_kind in SCALAR_BYTES:
                self.skip(count * SCALAR_BYTES[element_kind])
            else:
                for _ in range(count):
                    self.skip_value(element_kind, depth + 1)
        else:
            raise MetadataInvalid("GGUF 元信息类型无效")

    def architecture(self) -> str | None:
        if self.read(4) != b"GGUF" or self.integer("I") not in {2, 3}:
            raise MetadataInvalid("不是支持的 GGUF v2/v3 文件")
        tensors, pairs = self.integer("Q"), self.integer("Q")
        if not tensors or not pairs:
            raise MetadataInvalid("GGUF 模型无张量或架构元信息")
        if tensors > MAX_TENSORS or pairs > MAX_KV:
            raise MetadataLimit("GGUF 元信息或张量数量超出限额")
        architecture = None
        alignment = 32
        keys = set()
        for _ in range(pairs):
            key = self.string(capture=True)
            if key in keys:
                raise MetadataInvalid("GGUF 元信息包含重复键")
            keys.add(key)
            kind = self.integer("I")
            if key == "general.architecture":
                if kind != 8:
                    raise MetadataInvalid("GGUF 架构元信息类型无效")
                value = self.string(capture=True)
                if not SAFE_ARCHITECTURE.fullmatch(value):
                    raise MetadataInvalid("GGUF 架构标识符无效")
                architecture = value
            elif key == "general.alignment":
                if kind != 4:
                    raise MetadataInvalid("GGUF 对齐元信息类型无效")
                alignment = self.integer("I")
                if alignment < 8 or alignment > 4096 or alignment % 8:
                    raise MetadataInvalid("GGUF 对齐值无效")
            else:
                self.skip_value(kind)
        offsets = []
        for _ in range(tensors):
            self.string()
            dimensions = self.integer("I")
            if not 0 < dimensions <= 8:
                raise MetadataInvalid("GGUF 张量维度无效")
            elements = 1
            for _ in range(dimensions):
                length = self.integer("Q")
                if not 0 < length < 2**48:
                    raise MetadataInvalid("GGUF 张量大小无效")
                elements *= length
            tensor_type = self.integer("I")
            if tensor_type > 255:
                raise MetadataInvalid("GGUF 张量类型无效")
            offset = self.integer("Q")
            if offset % alignment:
                raise MetadataInvalid("GGUF 张量偏移无效")
            # Exact sizes for common float tensors; a conservative one-bit lower
            # bound for quantized types also catches obvious truncation without
            # loading weights or claiming to validate every quantization format.
            minimum_bytes = elements * {0: 4, 1: 2}.get(tensor_type, 0)
            if not minimum_bytes:
                minimum_bytes = (elements + 7) // 8
            offsets.append((offset, minimum_bytes))
        position = self.position
        data_start = position + (-position % alignment)
        # Validate offsets against file size without seeking into or reading weights.
        if any(data_start + offset + minimum > self.size for offset, minimum in offsets):
            raise MetadataInvalid("GGUF 张量数据不完整")
        return architecture


def _scan_posix(root: Path, stop: threading.Event) -> dict:
    deadline = time.monotonic() + SCAN_SECONDS
    items: list[dict] = []
    truncated = False
    excluded = False
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_DIRECTORY
    with open_model_root(root) as root_fd:
        root_mode = os.fstat(root_fd).st_mode
        if not root_mode & 0o444 or not root_mode & 0o111:
            raise error(
                503, "LOCAL_ASR_MODEL_DIR_UNAVAILABLE", "当前模型目录不可读取，请检查目录权限"
            )
        names = []
        with os.scandir(root_fd) as entries:
            for index, entry in enumerate(entries):
                if stop.is_set():
                    raise ScanStopped
                if index >= MAX_ENTRIES or time.monotonic() >= deadline:
                    truncated = True
                    break
                if not entry.name.lower().endswith(".gguf"):
                    continue
                if not SAFE_MODEL.fullmatch(entry.name) or entry.is_symlink():
                    excluded = True
                    continue
                names.append(entry.name)
        for name in sorted(names):
            if stop.is_set():
                raise ScanStopped
            if len(items) >= MAX_MODELS or time.monotonic() >= deadline:
                truncated = True
                break
            descriptor = None
            try:
                descriptor = os.open(name, flags & ~os.O_DIRECTORY, dir_fd=root_fd)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    excluded = True
                    continue
                architecture = None
                reason = None
                try:
                    with os.fdopen(descriptor, "rb", buffering=0) as file:
                        descriptor = None
                        architecture = GGUFReader(
                            file, info.st_size, min(deadline, time.monotonic() + FILE_SECONDS), stop
                        ).architecture()
                except MetadataInvalid as exc:
                    reason = str(exc)
                    if isinstance(exc, MetadataLimit):
                        truncated = True
                backend = ARCHITECTURE_BACKENDS.get(architecture)
                if reason is None and backend is None:
                    reason = "尚无经过验证的架构与 ASR 后端匹配，请使用兼容模型或手动配置"
                items.append(
                    {
                        "model": name,
                        "backend": backend,
                        "size_bytes": info.st_size,
                        "architecture": architecture,
                        "selectable": False,
                        "reason": reason,
                    }
                )
            except OSError:
                excluded = True
            finally:
                if descriptor is not None:
                    os.close(descriptor)
    return {"items": items, "truncated": truncated, "excluded": excluded}


def _scan_windows(root: Path, stop: threading.Event) -> dict:
    deadline = time.monotonic() + SCAN_SECONDS
    items: list[dict] = []
    truncated = False
    excluded = False
    with open_model_root(root) as directory:
        names = []
        for index, name in enumerate(directory.scan_entries()):
            if stop.is_set():
                raise ScanStopped
            if index >= MAX_ENTRIES or time.monotonic() >= deadline:
                truncated = True
                break
            if not name.lower().endswith(".gguf"):
                continue
            if not SAFE_MODEL.fullmatch(name):
                excluded = True
                continue
            names.append(name)
        for name in sorted(names):
            if stop.is_set():
                raise ScanStopped
            if len(items) >= MAX_MODELS or time.monotonic() >= deadline:
                truncated = True
                break
            file = None
            try:
                file = directory.open_file(name)
                size = os.fstat(file.fileno()).st_size
                architecture = None
                reason = None
                try:
                    architecture = GGUFReader(
                        file, size, min(deadline, time.monotonic() + FILE_SECONDS), stop
                    ).architecture()
                except MetadataInvalid as exc:
                    reason = str(exc)
                    if isinstance(exc, MetadataLimit):
                        truncated = True
                backend = ARCHITECTURE_BACKENDS.get(architecture)
                if reason is None and backend is None:
                    reason = "尚无经过验证的架构与 ASR 后端匹配，请使用兼容模型或手动配置"
                items.append(
                    {
                        "model": name,
                        "backend": backend,
                        "size_bytes": size,
                        "architecture": architecture,
                        "selectable": False,
                        "reason": reason,
                    }
                )
            except OSError:
                excluded = True
            finally:
                if file is not None:
                    file.close()
    return {"items": items, "truncated": truncated, "excluded": excluded}


def scan_models(root: Path, stop: threading.Event) -> dict:
    try:
        scanned = _scan_windows(root, stop) if IS_WINDOWS else _scan_posix(root, stop)
    except OSError as exc:
        raise error(
            503,
            "LOCAL_ASR_MODEL_DIR_UNAVAILABLE",
            "当前模型目录无法访问，请检查服务器设置和目录权限",
        ) from exc
    warnings = []
    if scanned["truncated"]:
        warnings.append("模型扫描达到数量、字节或时间限额，部分结果未完成")
    if scanned["excluded"]:
        warnings.append("已忽略不安全名称、链接、非普通文件或无法读取的目录项")
    return {
        "items": scanned["items"],
        "truncated": scanned["truncated"],
        "warnings": warnings,
    }


async def _backend_process(executable: str):
    return await spawn_contained(
        [executable, "--list-backends-json"],
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


ASR_CAPABILITIES = frozenset(
    {
        "timestamps-native",
        "timestamps-ctc",
        "word-timestamps",
        "token-confidence",
        "language-detect",
    }
)
NON_ASR_CAPABILITIES = frozenset(
    {"tts", "s2s", "separate", "pitch", "chords", "beats", "tab", "piano"}
)


def _backend_caps(item: dict) -> frozenset[str] | None:
    caps = item.get("caps")
    if caps is None:
        return frozenset()
    if (
        not isinstance(caps, list)
        or len(caps) > 128
        or not all(isinstance(cap, str) and len(cap) <= 64 for cap in caps)
    ):
        raise ValueError("backend diagnostic capabilities invalid")
    return frozenset(caps)


def classify_backends(entries: list[dict]) -> list[dict[str, object]]:
    details = []
    for item in entries:
        name = item["name"]
        caps = _backend_caps(item)
        if "task" in item:
            task = item["task"]
            if task not in {"asr", "tts", "translation", "music", "other"}:
                task = "other"
                reason = "CLI 任务类型无法识别"
            else:
                reason = None if task == "asr" else "CLI 显式任务类型不是 ASR"
        elif caps & NON_ASR_CAPABILITIES:
            task = "other"
            reason = "CLI 能力标记包含非 ASR 任务"
        elif caps & ASR_CAPABILITIES:
            task = "asr"
            reason = None
        else:
            task = "other"
            reason = "CLI 未提供可确认的 ASR 能力标记"
        details.append(
            {
                "id": name,
                "label": name,
                "task": task,
                "reason": reason,
                "caps": sorted(caps),
            }
        )
    return sorted(details, key=lambda item: item["id"])


async def compiled_backends(executable: Path, factory, timeout: float) -> list[dict[str, object]]:
    process = None
    tasks = []

    async def read_limited(stream):
        output = bytearray()
        while chunk := await stream.read(8192):
            output.extend(chunk)
            if len(output) > CLI_OUTPUT_BYTES:
                raise ValueError("backend diagnostic output limit")
        return bytes(output)

    try:
        async with asyncio.timeout(timeout):
            process = await factory(str(executable))
            tasks = [
                asyncio.create_task(read_limited(process.stdout)),
                asyncio.create_task(read_limited(process.stderr)),
                asyncio.create_task(process.wait()),
            ]
            stdout, _, status = await asyncio.gather(*tasks)
            if status != 0:
                raise ValueError("backend diagnostic failed")
            payload = json.loads(stdout)
            entries = payload.get("backends") if isinstance(payload, dict) else None
            if not isinstance(entries, list) or len(entries) > 1024:
                raise ValueError("backend diagnostic shape invalid")
            if not all(
                isinstance(item, dict) and isinstance(item.get("name"), str) for item in entries
            ):
                raise ValueError("backend diagnostic names invalid")
            names = {item["name"] for item in entries}
            if len(names) != len(entries):
                raise ValueError("backend diagnostic duplicate names")
            invalid = [
                name for name in names if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name)
            ]
            if invalid:
                raise ValueError("backend diagnostic identifier invalid")
            return classify_backends(entries)
    finally:
        if process is not None:
            await terminate_contained(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class LocalASRModels:
    def __init__(self, settings):
        self.settings = settings
        self.lock = asyncio.Lock()
        self.process_factory = _backend_process

    async def get(self):
        async with self.lock:
            try:
                root = self.settings._trusted_path(self.settings.active["crispasr_model_dir"])
            except HTTPException as exc:
                raise error(
                    503,
                    "LOCAL_ASR_MODEL_DIR_UNAVAILABLE",
                    "当前模型目录不在已授权范围，请检查设置并重启后端",
                ) from exc
            stop = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(scan_models, root, stop))
            try:
                scanned = await asyncio.shield(task)
            finally:
                stop.set()
                # A disconnected caller cannot leave scans overlapping the next request.
                cancelled = False
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                if cancelled:
                    raise asyncio.CancelledError
            executable = {"ok": False, "version": None, "message": "当前 CrispASR 程序无法使用"}
            backend_details = []
            try:
                path = self.settings._trusted_path(
                    self.settings.active["crispasr_executable"], executable=True
                )
                executable = await self.settings._version(path)
                if executable["ok"]:
                    backend_details = await compiled_backends(
                        path, self.process_factory, self.settings.check_timeout
                    )
            except (OSError, ValueError, TimeoutError, RecursionError, HTTPException):
                executable = {
                    "ok": False,
                    "version": executable["version"],
                    "message": "无法确认 CrispASR 编译后端，请检查本机程序版本",
                }
            for item in scanned["items"]:
                if not executable["ok"]:
                    item["reason"] = item["reason"] or "当前 CrispASR 程序或编译后端不可用"
                elif item["backend"] is not None and item["backend"] not in {
                    entry["id"] for entry in backend_details if entry["task"] == "asr"
                }:
                    item["reason"] = "当前 CrispASR 程序未编译此模型需要的后端"
                item["selectable"] = item["reason"] is None and executable["ok"]
            return {
                "provider": "crispasr",
                "executable": executable,
                "model_dir": self.settings._display(root),
                "restart_required": self.settings.get()["restart_required"],
                "backends": [entry["id"] for entry in backend_details if entry["task"] == "asr"],
                "backend_details": backend_details,
                **scanned,
            }
