"""HTTP translation providers with protocol-specific request adapters."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..domain import Segment
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


class _ChatCompletionsAdapter(_ProtocolAdapter):
    def request(
        self, model: str, messages: list[dict[str, str]], context: TranslationContext
    ) -> dict:
        return {
            "model": model,
            "messages": messages,
            "temperature": context.temperature,
            "response_format": {"type": "json_object"},
        }

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
        return {"model": model, "input": messages, "temperature": context.temperature}

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
        payload = {"model": model, "max_tokens": 4_096, "messages": user_messages}
        if system:
            payload["system"] = system
        return payload

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
        self.http_call = http_call or self._default_http_call
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

    @staticmethod
    def _default_http_call(url: str, headers: dict[str, str], payload: dict, timeout: int) -> dict:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured endpoint
            return json.loads(response.read().decode("utf-8"))

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

    async def _request(self, payload: dict) -> dict:
        if self.cancel_event and self.cancel_event.is_set():
            raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.http_call,
                    self._endpoint(),
                    self._headers(),
                    payload,
                    self.config.timeout_seconds,
                ),
                timeout=self.config.timeout_seconds,
            )
            if self.cancel_event and self.cancel_event.is_set():
                raise TranslationProviderError("TRANSLATION_CANCELLED", "翻译已取消")
            if not isinstance(response, dict):
                raise TranslationProviderError(
                    "TRANSLATION_INVALID_RESPONSE", "翻译服务响应格式无效"
                )
            return response
        except asyncio.TimeoutError as exc:
            raise TranslationProviderError("TRANSLATION_TIMEOUT", "翻译服务请求超时") from exc
        except asyncio.CancelledError:
            raise
        except TranslationProviderError:
            raise
        except HTTPError as exc:
            raise TranslationProviderError("TRANSLATION_HTTP_ERROR", "翻译服务请求失败") from exc
        except (URLError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise TranslationProviderError("TRANSLATION_HTTP_ERROR", "翻译服务不可用") from exc

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
