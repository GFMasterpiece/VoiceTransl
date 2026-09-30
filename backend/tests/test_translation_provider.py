import asyncio
import json
import time
from urllib.error import URLError

import pytest
from app.domain import JobStatus, Segment, TranslationConfig
from app.main import create_app
from app.providers.protocols import TranslationContext
from app.providers.translation import HTTPTranslationProvider, TranslationProviderError
from httpx import ASGITransport, AsyncClient


def make_segments(count: int = 3) -> list[Segment]:
    return [Segment(id=f"seg_{index}", source_text=f"原文 {index}") for index in range(count)]


def make_config(provider: str, **overrides) -> TranslationConfig:
    values = {
        "provider": provider,
        "base_url": "https://translation.example/v1",
        "api_key_env": "TEST_TRANSLATION_KEY",
        "model": "test-model",
        "target_language": "zh-CN",
    }
    values.update(overrides)
    return TranslationConfig(**values)


def response_for(provider: str, text: str) -> dict:
    if provider == "openai_chat":
        return {"choices": [{"message": {"content": text}}]}
    if provider == "openai_responses":
        return {"output_text": text}
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai_chat", "openai_responses", "anthropic_messages"])
async def test_protocol_adapters_build_requests_and_extract_official_responses(
    monkeypatch, provider
):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    calls = []
    text = json.dumps(
        {"translations": [{"id": "seg_0", "translation": "译文"}]}, ensure_ascii=False
    )

    def fake_http(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        return response_for(provider, text)

    result = await HTTPTranslationProvider(make_config(provider), http_call=fake_http).translate(
        make_segments(1), TranslationContext()
    )

    assert result[0].translated_text == "译文"
    assert calls[0][0].endswith(
        {
            "openai_chat": "/chat/completions",
            "openai_responses": "/responses",
            "anthropic_messages": "/messages",
        }[provider]
    )
    if provider == "anthropic_messages":
        assert calls[0][1]["x-api-key"] == "secret"
        assert "system" in calls[0][2]
        assert calls[0][2]["temperature"] == 0.2
        assert calls[0][2]["messages"][0]["role"] == "user"
    else:
        assert calls[0][1]["authorization"] == "Bearer secret"
        assert calls[0][2]["model"] == "test-model"


@pytest.mark.asyncio
async def test_responses_adapter_accepts_nested_output_message_content(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    text = json.dumps({"translations": [{"id": "seg_0", "translation": "译文"}]})

    def fake_http(_url, _headers, _payload, _timeout):
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}

    result = await HTTPTranslationProvider(
        make_config("openai_responses"), http_call=fake_http
    ).translate(make_segments(1), TranslationContext())
    assert result[0].translated_text == "译文"


@pytest.mark.asyncio
async def test_translation_batches_requests_and_preserves_segment_order(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    calls = []

    def fake_http(_url, _headers, payload, _timeout):
        calls.append(payload)
        messages = payload.get("messages", payload.get("input"))
        items = json.loads(messages[-1]["content"])
        text = json.dumps(
            {
                "translations": [
                    {"id": item["id"], "translation": f"译-{item['id']}"} for item in items
                ]
            }
        )
        return {"choices": [{"message": {"content": text}}]}

    provider = HTTPTranslationProvider(make_config("openai_chat"), http_call=fake_http)
    result = await provider.translate(
        make_segments(5), TranslationContext(batch_size=2, source_language="ja")
    )

    assert len(calls) == 3
    assert [segment.id for segment in result] == [f"seg_{i}" for i in range(5)]
    assert [segment.translated_text for segment in result] == [f"译-seg_{i}" for i in range(5)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": '{"translations": []}'}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": '{"translations": [{"id": "seg_0", "translation": "a"}, '
                        '{"id": "seg_0", "translation": "b"}]}'
                    }
                }
            ]
        },
    ],
)
async def test_translation_rejects_malformed_or_incomplete_response(monkeypatch, body):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    provider = HTTPTranslationProvider(make_config("openai_chat"), http_call=lambda *_args: body)
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_INVALID_RESPONSE"


def test_translation_requires_api_key_from_configured_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-key")
    monkeypatch.delenv("TEST_TRANSLATION_KEY", raising=False)
    with pytest.raises(TranslationProviderError) as error:
        HTTPTranslationProvider(make_config("openai_chat"), http_call=lambda *_args: {})
    assert error.value.code == "TRANSLATION_API_KEY_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("translation_overrides", "expected_code"),
    [
        (
            {"base_url": None, "api_key_env": "MISSING_TRANSLATION_KEY"},
            "TRANSLATION_PROVIDER_NOT_CONFIGURED",
        ),
        (
            {
                "base_url": "https://translation.example/v1",
                "api_key_env": "MISSING_TRANSLATION_KEY",
            },
            "TRANSLATION_API_KEY_MISSING",
        ),
    ],
)
async def test_manager_persists_translation_initialization_error(
    tmp_path, monkeypatch, translation_overrides, expected_code
):
    monkeypatch.delenv("MISSING_TRANSLATION_KEY", raising=False)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        project_id = response.json()["project"]["id"]
        config = app.state.store.load_pipeline_config(project_id)
        translation = config.translation.model_copy(
            update={"provider": "openai_chat", "model": "test-model", **translation_overrides}
        )
        app.state.store.save_pipeline_config(
            project_id, config.model_copy(update={"translation": translation})
        )

        await app.state.jobs.start(project_id)
        await app.state.jobs.tasks[project_id]

    job = app.state.store.load_job(project_id)
    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert job.error.type == expected_code


@pytest.mark.asyncio
async def test_translation_honors_cancellation_before_and_during_request(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    cancel_event = asyncio.Event()
    provider = HTTPTranslationProvider(
        make_config("openai_chat"), cancel_event=cancel_event, http_call=lambda *_args: {}
    )
    cancel_event.set()
    with pytest.raises(TranslationProviderError, match="翻译已取消"):
        await provider.translate(make_segments(1), TranslationContext())

    cancel_event.clear()

    def fake_http(*_args):
        cancel_event.set()
        return response_for(
            "openai_chat", '{"translations": [{"id": "seg_0", "translation": "译文"}]}'
        )

    provider.http_call = fake_http
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_CANCELLED"


@pytest.mark.asyncio
async def test_translation_maps_http_and_timeout_errors(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    provider = HTTPTranslationProvider(
        make_config("openai_chat"), http_call=lambda *_args: (_ for _ in ()).throw(URLError("down"))
    )
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_HTTP_ERROR"

    def slow_http(*_args):
        time.sleep(1.2)
        return {}

    provider = HTTPTranslationProvider(
        make_config("openai_chat", timeout_seconds=1), http_call=slow_http
    )
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_TIMEOUT"
