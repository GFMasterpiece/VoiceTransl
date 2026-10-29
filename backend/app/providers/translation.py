"""HTTP translation providers with protocol-specific request adapters."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Callable

import httpx

from ..domain import Segment
from ..domain.pipeline_config import merge_translation_parameters
from .protocols import TranslationContext


class TranslationProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_json_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        for key in ("content", "output_text", "message", "output"):
            if key in value:
                text = _json_text(value[key])
                if text:
                    return text
    return ""


def _parse_translation_text(text: str) -> dict[str, str]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise TranslationProviderError(
            "TRANSLATION_INVALID_RESPONSE", "翻译服务返回的 JSON 无效"
        ) from exc
    if isinstance(value, dict):
        value = value.get("translations", value.get("segments"))
    if not isinstance(value, list):
        raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "翻译服务返回格式无效")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "翻译结果缺少片段 ID")
        translated = item.get("translation", item.get("translated_text"))
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "翻译结果缺少译文")
        if item["id"] in result:
            raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "翻译结果包含重复片段")
        result[item["id"]] = translated.strip()
    return result


@dataclass(frozen=True)
class _ProtocolAdapter:
    path: str

    def request(
        self, model: str, messages: list[dict[str, str]], context: TranslationContext
    ) -> dict:
        raise NotImplementedError

    def extract_text(self, payload: dict) -> str:
        raise NotImplementedError


def _with_additional_parameters(payload: dict, parameters: dict[str, object]) -> dict:
    return merge_translation_parameters(payload, parameters)


class _ChatCompletionsAdapter(_ProtocolAdapter):
    def request(
        self, model: str, messages: list[dict[str, str]], context: TranslationContext
    ) -> dict:
        return _with_additional_parameters(
            {"model": model, "messages": messages}, context.additional_parameters
        )

    def extract_text(self, payload: dict) -> str:
        try:
            text = _json_text(payload["choices"][0]["message"]["content"])
            if not text:
                raise TranslationProviderError(
                    "TRANSLATION_INVALID_RESPONSE", "Chat Completions 响应格式无效"
                )
            return text
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationProviderError(
                "TRANSLATION_INVALID_RESPONSE", "Chat Completions 响应格式无效"
            ) from exc


class _ResponsesAdapter(_ProtocolAdapter):
    def request(
        self, model: str, messages: list[dict[str, str]], context: TranslationContext
    ) -> dict:
        return _with_additional_parameters(
            {"model": model, "input": messages}, context.additional_parameters
        )

    def extract_text(self, payload: dict) -> str:
        text = _json_text(payload.get("output_text")) or _json_text(payload.get("output"))
        if not text:
            raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "Responses 响应格式无效")
        return text


class _AnthropicAdapter(_ProtocolAdapter):
    def request(
        self, model: str, messages: list[dict[str, str]], context: TranslationContext
    ) -> dict:
        system = next((item["content"] for item in messages if item["role"] == "system"), None)
        user_messages = [item for item in messages if item["role"] != "system"]
        if "max_tokens" not in context.additional_parameters:
            raise TranslationProviderError(
                "TRANSLATION_PARAMETER_REQUIRED",
                "Anthropic Messages 需要配置附加参数 max_tokens",
            )
        payload = {"model": model, "messages": user_messages}
        if system:
            payload["system"] = system
        return _with_additional_parameters(payload, context.additional_parameters)

    def extract_text(self, payload: dict) -> str:
        text = _json_text(payload.get("content"))
        if not text:
            raise TranslationProviderError(
                "TRANSLATION_INVALID_RESPONSE", "Anthropic Messages 响应格式无效"
            )
        return text


ADAPTERS = {
    "openai_chat": _ChatCompletionsAdapter("/chat/completions"),
    "openai_responses": _ResponsesAdapter("/responses"),
    "anthropic_messages": _AnthropicAdapter("/messages"),
}


class HTTPTranslationProvider:
    def __init__(
        self,
        config,
        *,
        cancel_event: asyncio.Event | None = None,
        http_call: Callable | None = None,
    ):
        self.config = config
        self.cancel_event = cancel_event
        self.http_call = http_call
        self.adapter = ADAPTERS.get(config.provider)
        if self.adapter is None:
            raise TranslationProviderError(
                "TRANSLATION_PROVIDER_UNSUPPORTED", "不支持的翻译 Provider"
            )
        if not config.base_url:
            raise TranslationProviderError(
                "TRANSLATION_PROVIDER_NOT_CONFIGURED", "翻译服务未配置 base_url"
            )
        key_name = config.api_key_env or self._default_key_name(config.provider)
        self.api_key = os.getenv(key_name)
        if not self.api_key:
            raise TranslationProviderError("TRANSLATION_API_KEY_MISSING", "翻译服务 API Key 未配置")

    @staticmethod
    def _default_key_name(provider: str) -> str:
        return "ANTHROPIC_API_KEY" if provider == "anthropic_messages" else "OPENAI_API_KEY"

    def _endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + self.adapter.path

    def _headers(self) -> dict[str, str]:
        if self.config.provider == "anthropic_messages":
            return {
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
        return {"content-type": "application/json", "authorization": f"Bearer {self.api_key}"}

    async def _default_http_call(
        self, url: str, headers: dict[str, str], payload: dict, timeout: int
    ) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise TranslationProviderError(
                    "TRANSLATION_INVALID_RESPONSE", "翻译服务响应格式无效"
                )
            return value

    @staticmethod
    async def _cancel_and_wait(task: asyncio.Task) -> None:
        if task.done():
            return
        task.cancel()
        # A repeated cancellation of the provider task must not skip waiting
        # for the transport task to finish closing its connection. Keep the
        # cleanup itself bounded so a broken transport cannot hang shutdown.
        cleanup = asyncio.create_task(asyncio.wait({task}))
        deadline = asyncio.get_running_loop().time() + 5
        try:
            while not cleanup.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    task.cancel()
                    break
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), remaining)
                except asyncio.CancelledError:
                    continue
                except asyncio.TimeoutError:
                    task.cancel()
                    break
        finally:
            if not cleanup.done():
                cleanup.cancel()
            await asyncio.gather(cleanup, return_exceptions=True)

    async def _request(self, payload: dict) -> dict:
        if self.cancel_event and self.cancel_event.is_set():
            raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
        call = self.http_call or self._default_http_call
        request_task = asyncio.create_task(
            call(self._endpoint(), self._headers(), payload, self.config.timeout_seconds)
        )
        cancel_task = asyncio.create_task(self.cancel_event.wait()) if self.cancel_event else None
        has_result = False
        try:
            pending = {request_task}
            if cancel_task is not None:
                pending.add(cancel_task)
            done, _ = await asyncio.wait(
                pending,
                timeout=self.config.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError
            if cancel_task is not None and cancel_task in done and cancel_task.result():
                await self._cancel_and_wait(request_task)
                raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
            response = await request_task
            has_result = True
            if self.cancel_event and self.cancel_event.is_set():
                raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
            if not isinstance(response, dict):
                raise TranslationProviderError(
                    "TRANSLATION_INVALID_RESPONSE", "翻译服务响应格式无效"
                )
            return response
        except asyncio.TimeoutError as exc:
            await self._cancel_and_wait(request_task)
            raise TranslationProviderError("TRANSLATION_TIMEOUT", "翻译服务请求超时") from exc
        except asyncio.CancelledError:
            if not has_result:
                await self._cancel_and_wait(request_task)
            raise
        except TranslationProviderError:
            raise
        except httpx.HTTPError as exc:
            raise TranslationProviderError("TRANSLATION_HTTP_ERROR", "翻译服务请求失败") from exc
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationProviderError("TRANSLATION_HTTP_ERROR", "翻译服务不可用") from exc
        finally:
            if not request_task.done():
                await self._cancel_and_wait(request_task)
            if cancel_task is not None:
                await self._cancel_and_wait(cancel_task)

    async def translate(
        self, segments: list[Segment], context: TranslationContext
    ) -> list[Segment]:
        result: list[Segment] = []
        for offset in range(0, len(segments), context.batch_size):
            if self.cancel_event and self.cancel_event.is_set():
                raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
            batch = segments[offset : offset + context.batch_size]
            system = (
                f"Translate from {context.source_language} to {context.target_language}. "
                'Return only JSON: {"translations":[{"id":"...","translation":"..."}]}'
            )
            if context.glossary:
                system += f" Glossary: {json.dumps(context.glossary, ensure_ascii=False)}"
            if context.instructions:
                system += f" Instructions: {context.instructions}"
            user = json.dumps(
                [{"id": item.id, "text": item.source_text} for item in batch], ensure_ascii=False
            )
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            payload = self.adapter.request(self.config.model, messages, context)
            response = await self._request(payload)
            translations = _parse_translation_text(self.adapter.extract_text(response))
            expected = {item.id for item in batch}
            if set(translations) != expected:
                raise TranslationProviderError("TRANSLATION_INVALID_RESPONSE", "翻译结果片段不完整")
            result.extend(
                item.model_copy(update={"translated_text": translations[item.id]}) for item in batch
            )
        return result


__all__ = ["HTTPTranslationProvider", "TranslationProviderError"]
