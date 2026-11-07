"""Cancellable Qianwen Filetrans REST protocol, without SDK or global credentials.

The control, temporary OSS, and result clients deliberately share neither
authentication nor cookies. A submitted task is never automatically resubmitted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ..domain.node_execution import NodeExecutionCancelled, NodeExecutionError
from ..domain.transcript import Segment, Transcript
from .qwen_protocol import (
    CANCEL_TIMEOUT,
    CHUNK_SIZE,
    MAX_RESULT_BYTES,
    POLL_INTERVAL,
    TEMPORARY_UPLOAD_RETENTION_HOURS,
    validate_additional_parameters,
)
from .qwen_protocol import DEFAULT_MODEL as DEFAULT_MODEL

_PRIVATE_HTTP = ContextVar("qwen_private_http", default=False)


class _PrivateHTTPFilter(logging.Filter):
    def filter(self, record):
        # httpx logs complete signed request URLs at INFO; httpcore DEBUG can
        # include response headers. Suppress only this task's transport logs.
        return not _PRIVATE_HTTP.get()


_HTTP_FILTER = _PrivateHTTPFilter()
for _logger_name in (
    "httpx",
    "httpcore",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(_logger_name).addFilter(_HTTP_FILTER)


class QwenASRError(NodeExecutionError):
    pass


class QwenASRCancelled(NodeExecutionCancelled):
    def __init__(self, outcome="not_submitted"):
        self.remote_cancel_outcome = outcome
        message = (
            "本地请求已停止；远端仅排队任务可取消，已开始的识别可能继续执行；"
            f"临时文件由平台在{TEMPORARY_UPLOAD_RETENTION_HOURS}小时后清理"
        )
        super().__init__("QWEN_ASR_CANCELLED", message)


async def _settle(task):
    """Join cancellation cleanup, including an in-progress bounded disk read."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()


async def _read_chunk(stream):
    task = asyncio.create_task(asyncio.to_thread(stream.read, CHUNK_SIZE))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _settle(task)
        raise


class _Multipart(httpx.AsyncByteStream):
    def __init__(self, path: Path, fields: dict[str, str], filename: str):
        self.path = path
        self.file = None
        self.boundary = uuid4().hex
        pieces = []
        for key, value in fields.items():
            pieces.append(
                f'--{self.boundary}\r\nContent-Disposition: form-data; name="{key}"'
                f"\r\n\r\n{value}\r\n".encode()
            )
        pieces.append(
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
        )
        self.prefix = b"".join(pieces)
        self.ending = f"\r\n--{self.boundary}--\r\n".encode()
        self.size = path.stat().st_size

    @property
    def headers(self):
        return {
            "Content-Type": f"multipart/form-data; boundary={self.boundary}",
            "Content-Length": str(len(self.prefix) + self.size + len(self.ending)),
        }

    async def __aiter__(self):
        yield self.prefix
        count = 0
        self.file = self.path.open("rb")
        try:
            while chunk := await _read_chunk(self.file):
                count += len(chunk)
                if count > self.size:
                    raise QwenASRError("QWEN_AUDIO_CHANGED", "上传期间音频文件发生变化")
                yield chunk
        finally:
            await self.aclose()
        if count != self.size:
            raise QwenASRError("QWEN_AUDIO_CHANGED", "上传期间音频文件发生变化")
        yield self.ending

    async def aclose(self):
        if self.file is not None:
            self.file.close()
            self.file = None


def _remote_url(value, base_url):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 33 for c in value):
        raise QwenASRError("QWEN_RESPONSE_INVALID", "服务返回的文件地址无效")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise QwenASRError("QWEN_RESPONSE_INVALID", "服务返回的文件地址无效") from None
    base = urlsplit(base_url)
    local_http = (
        base.scheme == parsed.scheme == "http"
        and base.netloc == parsed.netloc
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise QwenASRError("QWEN_RESPONSE_INVALID", "服务返回的文件地址必须为无认证的HTTPS地址")
    return value


def _number(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果包含无效时间范围")
    return value / 1000


def _times(item):
    begin, end = item.get("begin_time"), item.get("end_time")
    if begin is None and end is None:
        return None, None
    begin, end = _number(begin), _number(end)
    if end < begin:
        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果包含无效时间范围")
    return begin, end


def parse_transcript(payload, *, project_id: str, language: str) -> Transcript:
    """Convert real milliseconds; never synthesize timestamps for full-text results."""
    segments = []
    tracks = payload.get("transcripts")
    if not isinstance(tracks, list) or not tracks:
        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果缺少有效文本")
    for track in tracks:
        if not isinstance(track, dict):
            raise QwenASRError("QWEN_RESULT_INVALID", "识别结果音轨无效")
        sentences = track.get("sentences")
        if sentences is None or sentences == []:
            sentences = [{"text": track.get("text")}]
        if not isinstance(sentences, list):
            raise QwenASRError("QWEN_RESULT_INVALID", "识别结果片段无效")
        for item in sentences:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise QwenASRError("QWEN_RESULT_INVALID", "识别结果缺少有效文本")
            if not item["text"].strip():
                continue
            start, end = _times(item)
            metadata = {}
            words = item.get("words")
            if words is not None:
                if not isinstance(words, list):
                    raise QwenASRError("QWEN_RESULT_INVALID", "识别结果词条无效")
                clean_words = []
                for word in words:
                    if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果词条无效")
                    word_start, word_end = _times(word)
                    punctuation = word.get("punctuation", "")
                    if not isinstance(punctuation, str):
                        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果标点无效")
                    clean_words.append(
                        {
                            "start": word_start,
                            "end": word_end,
                            "text": word["text"],
                            "punctuation": punctuation,
                        }
                    )
                metadata["words"] = clean_words
            speaker, channel = item.get("speaker_id"), track.get("channel_id")
            for label in (speaker, channel):
                if label is not None and (type(label) is not int or label < 0):
                    raise QwenASRError("QWEN_RESULT_INVALID", "识别结果说话人或音轨无效")
            segments.append(
                Segment(
                    id=f"seg_{len(segments) + 1}",
                    start=start,
                    end=end,
                    source_text=item["text"],
                    language=language,
                    channel=str(channel) if channel is not None else None,
                    speaker=str(speaker) if speaker is not None else None,
                    metadata=metadata,
                )
            )
    if not segments:
        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果为空")
    properties = payload.get("properties", {})
    if not isinstance(properties, dict):
        raise QwenASRError("QWEN_RESULT_INVALID", "识别结果音频信息无效")
    duration = properties.get("original_duration_in_milliseconds")
    return Transcript(
        project_id=project_id,
        stage="raw",
        language=language,
        duration=_number(duration) if duration is not None else None,
        segments=segments,
        metadata={"provider": "qwen_asr"},
    )


class _Session:
    def __init__(self, profile, model):
        self.profile, self.model = profile, model
        self.task_id = None
        self.cancel_attempted = False
        self.stage = "初始化"
        if not profile.base_url or not profile.api_key_env:
            raise QwenASRError("QWEN_PROFILE_INVALID", "Qwen连接需要服务地址和密钥环境变量名")
        self.base = profile.base_url.rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise QwenASRError("QWEN_PROFILE_INVALID", "Qwen服务地址不能包含查询参数或认证信息")
        key = os.environ.get(profile.api_key_env)
        if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise QwenASRError("QWEN_CREDENTIALS_MISSING", "Qwen密钥环境变量未设置或格式无效")
        self.auth = {"Authorization": f"Bearer {key}"}

    def client(self):
        client = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=None)
        # httpcore imports its transport loggers lazily during client creation.
        for name in list(logging.Logger.manager.loggerDict):
            if name.startswith("httpcore."):
                logger = logging.getLogger(name)
                if _HTTP_FILTER not in logger.filters:
                    logger.addFilter(_HTTP_FILTER)
        return client

    async def json(self, client, method, url, *, limit=1024 * 1024, **kwargs):
        client.cookies.clear()
        async with client.stream(method, url, **kwargs) as response:
            if not 200 <= response.status_code < 300:
                raise QwenASRError(
                    "QWEN_HTTP_ERROR", f"Qwen{self.stage}失败（HTTP {response.status_code}）"
                )
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > limit:
                    raise QwenASRError("QWEN_RESPONSE_INVALID", f"Qwen{self.stage}响应过大")
                content.extend(chunk)
            try:
                value = json.loads(content)
            except (ValueError, UnicodeError, RecursionError):
                raise QwenASRError(
                    "QWEN_RESPONSE_INVALID", f"Qwen{self.stage}未返回合法JSON对象"
                ) from None
            if not isinstance(value, dict):
                raise QwenASRError("QWEN_RESPONSE_INVALID", f"Qwen{self.stage}未返回JSON对象")
            return value

    async def run(self, path, project_id, language, parameters):
        async with self.client() as control, self.client() as oss, self.client() as result:
            self.stage = "获取上传凭证"
            response = await self.json(
                control,
                "GET",
                f"{self.base}/uploads",
                headers={**self.auth, "Content-Type": "application/json"},
                params={"action": "getPolicy", "model": self.model},
            )
            data = response.get("data")
            if not isinstance(data, dict):
                raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen上传凭证无效")
            required = (
                "policy",
                "signature",
                "upload_dir",
                "upload_host",
                "oss_access_key_id",
                "x_oss_object_acl",
                "x_oss_forbid_overwrite",
            )
            if any(not isinstance(data.get(key), str) or not data[key] for key in required):
                raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen上传凭证字段不完整")
            try:
                if (
                    type(data["max_file_size_mb"]) is bool
                    or type(data["expire_in_seconds"]) is bool
                ):
                    raise ValueError
                limit, expiry = float(data["max_file_size_mb"]), float(data["expire_in_seconds"])
                if not math.isfinite(limit) or not math.isfinite(expiry) or min(limit, expiry) <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError):
                raise QwenASRError(
                    "QWEN_RESPONSE_INVALID", "Qwen上传凭证大小或有效期无效"
                ) from None
            if path.stat().st_size > limit * 1024 * 1024:
                raise QwenASRError("QWEN_UPLOAD_TOO_LARGE", "音频超过Qwen本次临时上传大小限制")
            directory = data["upload_dir"]
            if (
                any(ord(c) < 33 for c in directory)
                or directory.startswith("/")
                or "\\" in directory
                or any(p in ("", ".", "..") for p in directory.split("/"))
            ):
                raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen上传目录无效")
            suffix = path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", path.suffix) else ".bin"
            filename = f"{uuid4().hex}{suffix}"
            key = f"{directory}/{filename}"
            stream = _Multipart(
                path,
                {
                    "OSSAccessKeyId": data["oss_access_key_id"],
                    "Signature": data["signature"],
                    "policy": data["policy"],
                    "key": key,
                    "x-oss-object-acl": data["x_oss_object_acl"],
                    "x-oss-forbid-overwrite": data["x_oss_forbid_overwrite"],
                    "success_action_status": "200",
                },
                filename,
            )
            self.stage = "上传音频"
            try:
                async with asyncio.timeout(expiry):
                    async with oss.stream(
                        "POST",
                        _remote_url(data["upload_host"], self.base),
                        content=stream,
                        headers=stream.headers,
                    ) as response:
                        if response.status_code != 200:
                            raise QwenASRError(
                                "QWEN_UPLOAD_FAILED", f"Qwen上传失败（HTTP {response.status_code}）"
                            )
            except TimeoutError:
                raise QwenASRError("QWEN_UPLOAD_FAILED", "Qwen上传凭证已过期，请重新运行") from None
            finally:
                await stream.aclose()
            self.stage = "提交识别"
            submitted = await self.json(
                control,
                "POST",
                f"{self.base}/services/audio/asr/transcription",
                headers={
                    **self.auth,
                    "X-DashScope-Async": "enable",
                    "X-DashScope-OssResourceResolve": "enable",
                },
                json={
                    "model": self.model,
                    "input": {"file_urls": [f"oss://{key}"]},
                    "parameters": {**parameters, "language_hints": [language]},
                },
            )
            output = submitted.get("output")
            task_id = output.get("task_id") if isinstance(output, dict) else None
            if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
                raise QwenASRError(
                    "QWEN_RESPONSE_INVALID", "Qwen未返回有效任务编号，未自动重新提交"
                )
            self.task_id = task_id
            self.stage = "查询识别任务"
            while True:
                status = await self.json(
                    control, "GET", f"{self.base}/tasks/{task_id}", headers=self.auth
                )
                output = status.get("output")
                if not isinstance(output, dict):
                    raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen任务状态无效")
                state = output.get("task_status")
                if state == "SUCCEEDED":
                    break
                if state in ("FAILED", "CANCELED", "UNKNOWN"):
                    raise QwenASRError("QWEN_TASK_FAILED", "Qwen远端识别任务失败或已取消")
                if state not in ("PENDING", "RUNNING"):
                    raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen返回未知任务状态")
                await asyncio.sleep(POLL_INTERVAL)
            results = output.get("results")
            if (
                not isinstance(results, list)
                or len(results) != 1
                or not isinstance(results[0], dict)
            ):
                raise QwenASRError("QWEN_RESPONSE_INVALID", "Qwen未返回单文件识别结果")
            if results[0].get("subtask_status") != "SUCCEEDED":
                raise QwenASRError("QWEN_TASK_FAILED", "Qwen文件识别子任务失败")
            self.stage = "读取识别结果"
            payload = await self.json(
                result,
                "GET",
                _remote_url(results[0].get("transcription_url"), self.base),
                limit=MAX_RESULT_BYTES,
            )
            return parse_transcript(payload, project_id=project_id, language=language)

    async def cancel(self):
        if self.task_id is None:
            return "not_submitted"
        if self.cancel_attempted:
            return "unknown"
        self.cancel_attempted = True
        try:
            async with asyncio.timeout(CANCEL_TIMEOUT), self.client() as client:
                async with client.stream(
                    "POST", f"{self.base}/tasks/{self.task_id}/cancel", headers=self.auth
                ) as response:
                    if response.status_code == 200:
                        return "pending_cancel_requested"
                    if response.status_code == 400:
                        return "not_cancelable"
        except Exception:
            pass
        return "unknown"


async def transcribe(
    *, audio_path, project_id, language, profile, model, additional_parameters, cancel_event
):
    validate_additional_parameters(additional_parameters)
    if cancel_event.is_set():
        raise QwenASRCancelled()
    session = _Session(profile, model)
    token = _PRIVATE_HTTP.set(True)
    work = asyncio.create_task(
        session.run(Path(audio_path), project_id, language, additional_parameters)
    )
    cancelled = asyncio.create_task(cancel_event.wait())
    try:
        async with asyncio.timeout(profile.default_timeout):
            done, _ = await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done or cancel_event.is_set():
                raise QwenASRCancelled()
            return await work
    except QwenASRCancelled:
        work.cancel()
        await _settle(work)
        raise QwenASRCancelled(await session.cancel()) from None
    except (TimeoutError, httpx.TimeoutException):
        work.cancel()
        await _settle(work)
        await session.cancel()
        raise QwenASRError("QWEN_TIMEOUT", "Qwen识别总超时；远端已执行任务可能继续运行") from None
    except asyncio.CancelledError:
        work.cancel()
        await _settle(work)
        cleanup = asyncio.create_task(session.cancel())
        await _settle(cleanup)
        raise
    except httpx.HTTPError:
        raise QwenASRError(
            "QWEN_CONNECTION_FAILED", f"Qwen{session.stage}连接失败，未自动重新提交"
        ) from None
    finally:
        cancelled.cancel()
        await _settle(cancelled)
        if not work.done():
            work.cancel()
            await _settle(work)
        _PRIVATE_HTTP.reset(token)
