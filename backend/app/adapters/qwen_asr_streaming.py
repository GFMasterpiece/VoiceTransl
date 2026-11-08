"""One cancellable Qwen Audio Streaming task per connection; no reconnect/replay.

Only verified PCM16 mono WAV is sent. Final sentences are keyed by channel and
sentence ID: identical final retransmissions are ignored; conflicting finals
fail explicitly. Interim text never becomes an output segment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import struct
from pathlib import Path
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ..domain.node_execution import NodeExecutionCancelled, NodeExecutionError
from ..domain.transcript import Segment, Transcript
from .qwen_streaming_protocol import (
    CHUNK_SIZE,
    CLOSE_TIMEOUT,
    MAX_EVENT_BYTES,
    MAX_RESULT_BYTES,
    MAX_SENTENCES,
    validate_additional_parameters,
    validate_endpoint,
)

# DEBUG frame/handshake logging would expose headers and recognition text.
_PRIVATE_LOGGER = logging.Logger("qwen_streaming_private")
_PRIVATE_LOGGER.disabled = True
_PRIVATE_LOGGER.propagate = False


class QwenStreamingError(NodeExecutionError):
    pass


class QwenStreamingCancelled(NodeExecutionCancelled):
    def __init__(self):
        super().__init__(
            code="QWEN_STREAM_CANCELLED",
            message=(
                "本地WebSocket与文件读取已停止；平台未公开已开始任务的取消确认或恢复保证，"
                "不能保证远端立即停止或退费"
            ),
        )


def _error(code, message):
    return QwenStreamingError(f"QWEN_STREAM_{code}", message)


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        # Even a same-origin redirect may change endpoint semantics. Never send
        # authentication to any endpoint other than the frozen configured URI.
        return exc


async def _join(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()


async def _disk_call(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A thread cannot be killed safely. Join its bounded read before closing
        # the handle; don't detach a worker that might still use the file.
        await _join(task)
        raise


def _identity(stream):
    stat = os.fstat(stream.fileno())
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _read_audio_chunk(stream, size, identity):
    if _identity(stream) != identity:
        raise _error("AUDIO_INVALID", "输入音频在发送期间发生变动")
    chunk = stream.read(size)
    if _identity(stream) != identity:
        raise _error("AUDIO_INVALID", "输入音频在发送期间发生变动")
    return chunk


def _inspect_wav(stream):
    """Bounded RIFF parsing, including chunk sizes and actual file boundaries."""
    original = _identity(stream)
    size = original[2]
    header = stream.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise _error("AUDIO_UNSUPPORTED", "仅支持实际为 PCM16 单声道的 WAV 文件；不会自动转码")
    limit = struct.unpack_from("<I", header, 4)[0] + 8
    if limit != size or limit < 44:
        raise _error("AUDIO_INVALID", "WAV 文件已截断或包含无效的长度信息")
    settings = None
    data_size = None
    for _ in range(4096):
        if stream.tell() == limit:
            break
        chunk = stream.read(8)
        if len(chunk) != 8:
            raise _error("AUDIO_INVALID", "WAV 数据块不完整")
        chunk_size = struct.unpack_from("<I", chunk, 4)[0]
        end = stream.tell() + chunk_size
        padded_end = end + (chunk_size & 1)
        if padded_end > limit:
            raise _error("AUDIO_INVALID", "WAV 数据块超出文件边界")
        if chunk[:4] == b"fmt ":
            if settings is not None or chunk_size < 16:
                raise _error("AUDIO_INVALID", "WAV 格式块无效")
            codec, channels, rate, byte_rate, alignment, bits = struct.unpack(
                "<HHIIHH", stream.read(16)
            )
            if codec != 1 or channels != 1 or bits != 16:
                raise _error("AUDIO_UNSUPPORTED", "WAV 必须为 PCM16 单声道；不会自动转码")
            if rate <= 0 or alignment != 2 or byte_rate != rate * 2:
                raise _error("AUDIO_INVALID", "WAV 采样率或帧信息无效")
            settings = rate
        elif chunk[:4] == b"data":
            if data_size is not None or chunk_size <= 0 or chunk_size % 2:
                raise _error("AUDIO_INVALID", "WAV 音频数据为空或帧不完整")
            data_size = chunk_size
        stream.seek(padded_end)
    else:
        raise _error("AUDIO_INVALID", "WAV 数据块数量超出安全限制")
    if settings is None or data_size is None or original != _identity(stream):
        raise _error("AUDIO_INVALID", "WAV 格式或数据缺失，或文件在读取期间发生变动")
    stream.seek(0)
    return settings, data_size / (settings * 2), original


def _endpoint(value):
    try:
        return validate_endpoint(value)
    except ValueError:
        raise _error("CONNECTION_INVALID", "请配置有效的 WSS 地址；明文 WS 仅限本机测试") from None


def _times(value):
    start, end = value.get("begin_time"), value.get("end_time")
    if start is None and end is None:
        return None, None
    try:
        valid = (
            type(start) in (int, float)
            and type(end) in (int, float)
            and math.isfinite(start)
            and math.isfinite(end)
            and start >= 0
            and end >= start
        )
    except OverflowError:
        valid = False
    if not valid:
        raise _error("PROTOCOL_INVALID", "平台返回了无效的时间范围")
    return start / 1000, end / 1000


def _speaker(value):
    if value is None:
        return None
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[\w-]{1,64}", value):
        return value
    raise _error("PROTOCOL_INVALID", "平台返回了无效的说话人标识")


class _Sentences:
    def __init__(self, language):
        self.language = language
        self.final = {}
        self.pending = set()
        self.size = 0

    def add(self, sentence):
        if not isinstance(sentence, dict):
            raise _error("PROTOCOL_INVALID", "平台未返回有效的句子事件")
        if sentence.get("heartbeat") is True:
            return
        sentence_id = sentence.get("sentence_id")
        channel = sentence.get("channel_id", 0)
        if (
            type(sentence_id) is not int
            or sentence_id < 1
            or type(channel) is not int
            or channel < 0
        ):
            raise _error("PROTOCOL_INVALID", "平台返回了无效的句子标识")
        key = channel, sentence_id
        text = sentence.get("text")
        if not isinstance(text, str):
            raise _error("PROTOCOL_INVALID", "平台返回了无效的句子文本")
        final = sentence.get("sentence_end")
        if type(final) is not bool:
            raise _error("PROTOCOL_INVALID", "平台未声明句子是否为最终结果")
        if not final:
            if text.strip() and key not in self.final:
                self.pending.add(key)
                if len(self.pending) + len(self.final) > MAX_SENTENCES:
                    raise _error("RESULT_TOO_LARGE", "平台返回的句子数量超出安全限制")
            return
        self.pending.discard(key)
        if not text.strip():
            if key in self.final:
                raise _error("PROTOCOL_INVALID", "平台重复返回了相互冲突的最终句子")
            return
        start, end = _times(sentence)
        words = sentence.get("words", [])
        if not isinstance(words, list):
            raise _error("PROTOCOL_INVALID", "平台返回了无效的字级信息")
        safe_words = []
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise _error("PROTOCOL_INVALID", "平台返回了无效的字级信息")
            word_start, word_end = _times(word)
            safe = {"text": word["text"], "start": word_start, "end": word_end}
            if isinstance(word.get("punctuation"), str):
                safe["punctuation"] = word["punctuation"]
            if type(word.get("fixed")) is bool:
                safe["fixed"] = word["fixed"]
            if word.get("speaker_id") is not None:
                safe["speaker"] = _speaker(word["speaker_id"])
            safe_words.append(safe)
        segment = Segment(
            id=f"stream_{channel}_{sentence_id}",
            start=start,
            end=end,
            source_text=text,
            language=self.language,
            channel=str(channel),
            speaker=_speaker(sentence.get("speaker_id")),
            metadata={"sentence_id": sentence_id, "words": safe_words},
        )
        if key in self.final:
            if self.final[key] != segment:
                raise _error("PROTOCOL_INVALID", "平台重复返回了相互冲突的最终句子")
            return
        self.size += len(segment.model_dump_json().encode("utf-8"))
        if self.size > MAX_RESULT_BYTES or len(self.final) >= MAX_SENTENCES:
            raise _error("RESULT_TOO_LARGE", "平台返回的文本结果超出安全限制")
        self.final[key] = segment


async def _event(ws, task_id):
    raw = await ws.recv()
    if not isinstance(raw, str):
        raise _error("PROTOCOL_INVALID", "平台返回了无效的二进制任务事件")
    if len(raw.encode("utf-8")) > MAX_EVENT_BYTES:
        raise _error("RESULT_TOO_LARGE", "平台事件超出安全限制")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        body = json.loads(raw, object_pairs_hook=unique_object)
        header = body["header"]
        if not isinstance(header, dict) or header.get("task_id") != task_id:
            raise ValueError
        event = header["event"]
        payload = body.get("payload", {})
        if not isinstance(payload, dict) or not isinstance(event, str):
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        raise _error("PROTOCOL_INVALID", "平台事件格式或任务标识不匹配") from None
    if event == "task-failed":
        raise _error("REMOTE_FAILED", "平台识别任务失败；请检查模型、参数或账户状态")
    return event, payload


async def _close(ws):
    try:
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await ws.close()
    except (Exception, asyncio.CancelledError):
        ws.transport.abort()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await ws.wait_closed()
        except (Exception, asyncio.CancelledError):
            pass


class QwenStreamingASRAdapter:
    async def transcribe(
        self,
        *,
        audio_path: Path,
        project_id: str,
        language: str,
        connection,
        model: str,
        additional_parameters: dict,
        cancel_event: asyncio.Event,
        api_key: str,
    ) -> Transcript:
        try:
            parameters = dict(validate_additional_parameters(additional_parameters))
            if not isinstance(model, str) or not model.strip() or len(model) > 256:
                raise ValueError
            if not isinstance(language, str) or not language.strip() or len(language) > 128:
                raise ValueError
            timeout = connection.default_timeout
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise _error("PARAMETERS_INVALID", "识别语言、模型、超时或附加参数无效") from None
        endpoint = _endpoint(connection.base_url)
        if not isinstance(api_key, str) or not api_key.strip() or any(ord(c) < 32 for c in api_key):
            raise _error("API_KEY_REQUIRED", "请为当前节点保存有效的 API Key")
        if cancel_event.is_set():
            raise QwenStreamingCancelled()

        async def timed():
            try:
                async with asyncio.timeout(timeout):
                    return await self._run(
                        Path(audio_path), project_id, language, endpoint, model, parameters, api_key
                    )
            except TimeoutError:
                raise _error("TIMEOUT", "流式识别已超时，本地连接与文件读取已回收") from None
            except InvalidStatus as error:
                if error.response.status_code in (401, 403):
                    raise _error("AUTH_FAILED", "平台认证失败；请检查当前节点的 API Key") from None
                raise _error("HTTP_FAILED", "平台拒绝了 WebSocket 连接请求") from None
            except ConnectionClosed as error:
                if any(
                    frame is not None and frame.code == 1009 for frame in (error.rcvd, error.sent)
                ):
                    raise _error("RESULT_TOO_LARGE", "平台事件超出安全限制") from None
                raise _error(
                    "CONNECTION_FAILED", "WebSocket 提前断开；未得到完整识别结果"
                ) from None
            except QwenStreamingError:
                raise
            except Exception:
                raise _error("CONNECTION_FAILED", "流式识别连接或文件读取失败") from None

        operation = asyncio.create_task(timed())
        cancelled = asyncio.create_task(cancel_event.wait())
        try:
            await asyncio.wait((operation, cancelled), return_when=asyncio.FIRST_COMPLETED)
            if cancel_event.is_set():
                operation.cancel()
                await _join(operation)
                raise QwenStreamingCancelled()
            return operation.result()
        except asyncio.CancelledError:
            operation.cancel()
            await _join(operation)
            raise QwenStreamingCancelled() from None
        finally:
            cancelled.cancel()
            await _join(cancelled)

    async def _run(self, path, project_id, language, endpoint, model, parameters, api_key):
        try:
            stream = path.open("rb")
        except OSError:
            raise _error("AUDIO_INVALID", "无法读取输入音频文件") from None
        ws = None
        try:
            rate, duration, identity = await _disk_call(_inspect_wav, stream)
            ws = await _NoRedirectConnect(
                endpoint,
                additional_headers={"Authorization": f"Bearer {api_key}"},
                compression=None,
                proxy=None,
                max_size=MAX_EVENT_BYTES,
                max_queue=4,
                write_limit=CHUNK_SIZE,
                close_timeout=CLOSE_TIMEOUT,
                open_timeout=None,
                logger=_PRIVATE_LOGGER,
            )
            task_id = uuid4().hex
            await ws.send(
                json.dumps(
                    {
                        "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
                        "payload": {
                            "task_group": "audio",
                            "task": "asr",
                            "function": "recognition",
                            "model": model,
                            "parameters": {
                                **parameters,
                                "format": "wav",
                                "sample_rate": rate,
                                "language_hints": [language],
                            },
                            "input": {},
                        },
                    },
                    ensure_ascii=False,
                )
            )
            event, _ = await _event(ws, task_id)
            if event != "task-started":
                raise _error("PROTOCOL_INVALID", "平台未确认识别任务开始")
            finished_sending = asyncio.Event()
            sentences = _Sentences(language)

            async def send():
                chunk_size = max(2, min(CHUNK_SIZE, (rate // 10) * 2))
                sent = 0
                started = asyncio.get_running_loop().time()
                while True:
                    chunk = await _disk_call(_read_audio_chunk, stream, chunk_size, identity)
                    if not chunk:
                        break
                    if sent + len(chunk) > identity[2]:
                        raise _error("AUDIO_INVALID", "输入音频在发送期间发生变动")
                    await ws.send(chunk)
                    sent += len(chunk)
                    # Real-time pace: at most ~100ms PCM per chunk. Container
                    # bytes may make the stream slightly slower, never faster.
                    delay = sent / (rate * 2) - (asyncio.get_running_loop().time() - started)
                    if delay > 0:
                        await asyncio.sleep(delay)
                if sent != identity[2] or _identity(stream) != identity:
                    raise _error("AUDIO_INVALID", "输入音频被截断或在发送期间发生变动")
                finished_sending.set()
                await ws.send(
                    json.dumps(
                        {
                            "header": {
                                "action": "finish-task",
                                "task_id": task_id,
                                "streaming": "duplex",
                            },
                            "payload": {"input": {}},
                        }
                    )
                )

            async def receive():
                while True:
                    event, payload = await _event(ws, task_id)
                    if event == "result-generated":
                        output = payload.get("output")
                        if not isinstance(output, dict):
                            raise _error("PROTOCOL_INVALID", "平台未返回有效的识别输出")
                        sentences.add(output.get("sentence"))
                    elif event == "task-finished":
                        if not finished_sending.is_set() or sentences.pending:
                            raise _error("PROTOCOL_INVALID", "平台在音频或最终句子未完成时结束任务")
                        return
                    else:
                        raise _error("PROTOCOL_INVALID", "平台返回了不支持的流式任务事件")

            tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                for task in tasks:
                    await _join(task)
            if not sentences.final:
                raise _error("RESULT_EMPTY", "平台未返回任何最终识别文本")
            return Transcript(
                project_id=project_id,
                stage="raw",
                language=language,
                duration=duration,
                segments=[sentences.final[key] for key in sorted(sentences.final)],
                metadata={"provider": "qwen_asr_streaming", "model": model},
            )
        finally:
            stream.close()
            if ws is not None:
                cleanup = asyncio.create_task(_close(ws))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await _join(cleanup)
                    raise
