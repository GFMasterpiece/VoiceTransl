"""Sequential, cancellable Chat Completions translation; no partial results or replay on IO loss."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from ..domain.inline_connection import InlineConnection
from ..domain.node_execution import NodeExecutionCancelled, NodeExecutionError
from .llm_translation_protocol import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_CONTEXT_SEGMENTS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_BATCH_CHARACTERS,
    MAX_INPUT_CHARACTERS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_RETRY_DELAY,
    MAX_TOTAL_OUTPUT_BYTES,
    SYSTEM_MESSAGE,
)
from .llm_translation_protocol import (
    validate_additional_parameters as validate_additional_parameters,
)
from .llm_translation_protocol import validate_base_url as validate_base_url
from .private_http import PRIVATE_HTTP, ensure_private_http_filters


class LLMTranslationError(NodeExecutionError):
    def __init__(self, code, message, *, http_status=None):
        self.http_status = http_status
        super().__init__(f"LLM_TRANSLATION_{code}", message)


class LLMTranslationCancelled(NodeExecutionCancelled):
    def __init__(self):
        super().__init__(
            "LLM_TRANSLATION_CANCELLED", "本地翻译请求已停止；已发送请求可能仍被服务端处理"
        )


@dataclass(frozen=True)
class TranslationResult:
    translations: dict[str, str]
    request_count: int
    usage: dict[str, int]
    language: str


def _client():
    client = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=None)
    ensure_private_http_filters()
    return client


async def _settle(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled():
        task.exception()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("invalid JSON constant")


def _json(value):
    return json.loads(value, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def _retry_after(value, attempt):
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = min(2**attempt, 5)
    # NaN and infinity must never create an unbounded or invalid sleep.
    if not 0 <= delay <= MAX_RETRY_DELAY:
        delay = MAX_RETRY_DELAY if delay > 0 else 0
    return delay


def _preflight(
    items,
    connection,
    api_key,
    model,
    source_language,
    context,
    batch_size,
    max_batch_characters,
    context_segments,
    max_attempts,
    additional_parameters,
):
    try:
        if not isinstance(connection, InlineConnection):
            raise ValueError
        validate_base_url(connection.base_url)
        for value, low, high in (
            (batch_size, 1, 100),
            (max_batch_characters, 256, 30000),
            (context_segments, 0, 10),
            (max_attempts, 1, 5),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError
        for value, maximum, required in ((model, 128, True),):
            if (
                not isinstance(value, str)
                or len(value) > maximum
                or (required and not value)
                or value != value.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError
            value.encode("utf-8")
        from ..domain.context_values import validate_language, validate_text

        if source_language is not None:
            validate_language(source_language)
        validate_text(context)
        if not context.strip():
            raise ValueError
        validate_additional_parameters(additional_parameters)
    except (ValueError, TypeError, UnicodeError):
        raise LLMTranslationError(
            "CONFIG_INVALID", "翻译节点的连接、语言、模型或参数无效"
        ) from None
    if (
        not isinstance(api_key, str)
        or not api_key
        or any(ord(char) < 33 or ord(char) > 126 for char in api_key)
    ):
        raise LLMTranslationError("CREDENTIALS_MISSING", "翻译节点密钥未设置或格式无效")
    if not isinstance(items, list) or not items or len(items) > 100000:
        raise LLMTranslationError("INPUT_INVALID", "翻译片段清单无效")
    seen, records, blanks, total = set(), [], {}, 0
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "text"}
            or not isinstance(item["id"], str)
            or not item["id"]
            or len(item["id"]) > 256
            or not isinstance(item["text"], str)
            or item["id"] in seen
        ):
            raise LLMTranslationError("INPUT_INVALID", "翻译片段必须具有唯一ID和文本")
        seen.add(item["id"])
        try:
            item["id"].encode("utf-8")
            item["text"].encode("utf-8")
        except UnicodeError:
            raise LLMTranslationError("INPUT_INVALID", "翻译片段必须包含合法Unicode文本") from None
        total += len(item["text"]) + len(item["id"])
        if total > MAX_INPUT_CHARACTERS:
            raise LLMTranslationError("INPUT_INVALID", "翻译输入超出安全大小限制")
        if not item["text"].strip():
            blanks[item["id"]] = item["text"]
        elif len(item["text"]) > max_batch_characters:
            raise LLMTranslationError(
                "SEGMENT_TOO_LONG", "存在超过每批字符限制的单个片段；未发送任何翻译请求"
            )
        else:
            records.append(dict(item))
    if not records:
        raise LLMTranslationError("INPUT_EMPTY", "输入不包含可翻译的文本")
    return records, blanks


def _batches(records, size, characters, context):
    offset = 0
    while offset < len(records):
        end, used = offset, 0
        while end < len(records) and end - offset < size:
            length = len(records[end]["text"])
            if used + length > characters:
                break
            used += length
            end += 1
        before, after = [], []
        for distance in range(context):
            for index, target in ((offset - distance - 1, before), (end + distance, after)):
                if 0 <= index < len(records) and used + len(records[index]["text"]) <= characters:
                    target.append(records[index])
                    used += len(records[index]["text"])
        yield records[offset:end], list(reversed(before)), after
        offset = end


def _parse_output(envelope, expected):
    if (
        not isinstance(envelope, dict)
        or not isinstance(envelope.get("choices"), list)
        or not envelope["choices"]
    ):
        raise LLMTranslationError("RESPONSE_INVALID", "翻译服务未返回有效的完成结果")
    choice = envelope["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise LLMTranslationError("RESPONSE_INVALID", "翻译服务未返回有效的完成结果")
    message = choice["message"]
    if "role" in message and message["role"] != "assistant":
        raise LLMTranslationError("RESPONSE_INVALID", "翻译服务未返回有效的完成结果")
    if (
        message.get("refusal") is not None
        or message.get("tool_calls") not in (None, [])
        or message.get("function_call") is not None
    ):
        raise LLMTranslationError("REFUSED", "翻译服务拒绝请求或返回了不支持的工具调用")
    # Third-party compatible APIs may omit finish_reason. An explicitly supplied
    # value must still confirm a complete text response.
    if "finish_reason" in choice and choice["finish_reason"] != "stop":
        raise LLMTranslationError("INCOMPLETE", "翻译服务未完整生成结果")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMTranslationError("OUTPUT_INVALID", "翻译服务未返回要求的JSON译文")
    content = content.strip()
    lines = content.splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in {"```", "```json"}
        and lines[-1].strip() == "```"
    ):
        content = "\n".join(lines[1:-1])
    try:
        result = _json(content)
        if (
            not isinstance(result, dict)
            or set(result) != {"language", "translations"}
            or not isinstance(result["translations"], list)
        ):
            raise ValueError
        from ..domain.context_values import validate_language

        language = validate_language(result["language"])
        if not re.fullmatch(r"[A-Za-z]{1,8}(?:[-_][A-Za-z0-9]{1,8})*", language):
            raise ValueError
        translated = {}
        for item in result["translations"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"id", "translation"}
                or not isinstance(item["id"], str)
                or item["id"] not in expected
                or item["id"] in translated
                or not isinstance(item["translation"], str)
                or not item["translation"].strip()
            ):
                raise ValueError
            translated[item["id"]] = item["translation"]
            item["translation"].encode("utf-8")
        if set(translated) != expected:
            raise ValueError
        return translated, language
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise LLMTranslationError(
            "OUTPUT_INVALID", "翻译结果须返回有效语言代码，并逐项匹配输入ID及非空译文"
        ) from None


async def _request(client, endpoint, api_key, payload):
    client.cookies.clear()
    async with client.stream(
        "POST", endpoint, headers={"Authorization": f"Bearer {api_key}"}, json=payload
    ) as response:
        status = response.status_code
        if not 200 <= status < 300:
            return None, status, response.headers.get("Retry-After")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                raise LLMTranslationError("RESPONSE_TOO_LARGE", "翻译服务响应超出安全大小限制")
            content.extend(chunk)
        try:
            return _json(content), status, None
        except (ValueError, UnicodeError, RecursionError):
            raise LLMTranslationError("RESPONSE_INVALID", "翻译服务未返回合法JSON响应") from None


async def _run(
    records,
    blanks,
    connection,
    api_key,
    model,
    source_language,
    context,
    batch_size,
    max_batch_characters,
    context_segments,
    max_attempts,
    additional_parameters,
):
    output_bytes = sum(len(text.encode("utf-8")) for text in blanks.values())
    if output_bytes > MAX_TOTAL_OUTPUT_BYTES:
        raise LLMTranslationError("RESPONSE_TOO_LARGE", "翻译结果总量超出安全大小限制")
    translated, usage, count = dict(blanks), {}, 0
    output_language = None
    async with _client() as client:
        for items, before, after in _batches(
            records, batch_size, max_batch_characters, context_segments
        ):
            data = {
                "translation_context": context,
                "context_before": before,
                "items": items,
                "context_after": after,
            }
            if source_language:
                data["source_language_hint"] = source_language
            if output_language:
                data["output_language"] = output_language
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_MESSAGE},
                    {
                        "role": "user",
                        "content": json.dumps(data, ensure_ascii=False, allow_nan=False),
                    },
                ],
                **additional_parameters,
            }
            try:
                if (
                    len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                    > MAX_REQUEST_BYTES
                ):
                    raise ValueError
            except (ValueError, UnicodeError):
                raise LLMTranslationError(
                    "CONFIG_INVALID", "单次翻译请求超出安全大小限制"
                ) from None
            for attempt in range(max_attempts):
                count += 1
                try:
                    envelope, status, retry_after = await _request(
                        client,
                        connection.base_url.rstrip("/") + "/chat/completions",
                        api_key,
                        payload,
                    )
                    if not 200 <= status < 300:
                        error = LLMTranslationError(
                            "HTTP_ERROR", f"翻译服务请求失败（HTTP {status}）", http_status=status
                        )
                        if (status == 429 or 500 <= status <= 599) and attempt + 1 < max_attempts:
                            await asyncio.sleep(_retry_after(retry_after, attempt))
                            continue
                        raise error
                    if isinstance(envelope, dict) and isinstance(envelope.get("usage"), dict):
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                            value = envelope["usage"].get(key)
                            if type(value) is int and value >= 0:
                                usage[key] = usage.get(key, 0) + value
                    result, language = _parse_output(envelope, {item["id"] for item in items})
                    if output_language is not None and language != output_language:
                        raise LLMTranslationError("OUTPUT_INVALID", "不同翻译批次返回的语言不一致")
                except LLMTranslationError as error:
                    if (
                        error.code
                        not in {
                            "LLM_TRANSLATION_OUTPUT_INVALID",
                            "LLM_TRANSLATION_RESPONSE_INVALID",
                        }
                        or attempt + 1 == max_attempts
                    ):
                        raise
                    await asyncio.sleep(0)
                    continue
                output_bytes += sum(len(text.encode("utf-8")) for text in result.values())
                if output_bytes > MAX_TOTAL_OUTPUT_BYTES:
                    raise LLMTranslationError("RESPONSE_TOO_LARGE", "翻译结果总量超出安全大小限制")
                output_language = language
                translated.update(result)
                break
    return TranslationResult(translated, count, usage, output_language)


async def translate(
    *,
    items,
    context,
    source_language=None,
    connection: InlineConnection,
    api_key: str,
    model,
    batch_size=DEFAULT_BATCH_SIZE,
    max_batch_characters=DEFAULT_MAX_BATCH_CHARACTERS,
    context_segments=DEFAULT_CONTEXT_SEGMENTS,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    additional_parameters=None,
    cancel_event,
):
    additional_parameters = {} if additional_parameters is None else additional_parameters
    records, blanks = _preflight(
        items,
        connection,
        api_key,
        model,
        source_language,
        context,
        batch_size,
        max_batch_characters,
        context_segments,
        max_attempts,
        additional_parameters,
    )
    if cancel_event.is_set():
        raise LLMTranslationCancelled()
    # Keep later batches independent of mutation by callers while a request awaits IO.
    additional_parameters = copy.deepcopy(additional_parameters)
    token = PRIVATE_HTTP.set(True)
    work = asyncio.create_task(
        _run(
            records,
            blanks,
            connection,
            api_key,
            model,
            source_language,
            context,
            batch_size,
            max_batch_characters,
            context_segments,
            max_attempts,
            additional_parameters,
        )
    )
    cancelled = asyncio.create_task(cancel_event.wait())
    try:
        async with asyncio.timeout(connection.default_timeout):
            done, _ = await asyncio.wait({work, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done or cancel_event.is_set():
                raise LLMTranslationCancelled()
            return await work
    except (TimeoutError, httpx.TimeoutException):
        raise LLMTranslationError("TIMEOUT", "翻译任务总超时；未自动重发未确认的请求") from None
    except httpx.HTTPError:
        raise LLMTranslationError(
            "CONNECTION_FAILED", "翻译连接失败；未自动重发未确认的请求"
        ) from None
    except (LLMTranslationError, LLMTranslationCancelled, asyncio.CancelledError):
        raise
    except Exception:
        raise LLMTranslationError("FAILED", "翻译服务处理失败") from None
    finally:
        work.cancel()
        cancelled.cancel()
        await _settle(work)
        await _settle(cancelled)
        PRIVATE_HTTP.reset(token)
