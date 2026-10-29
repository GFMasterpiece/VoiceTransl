import asyncio
import json

import httpx
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

    async def fake_http(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        return response_for(provider, text)

    context = TranslationContext()
    if provider == "anthropic_messages":
        context = TranslationContext(additional_parameters={"max_tokens": 4096})
    result = await HTTPTranslationProvider(make_config(provider), http_call=fake_http).translate(
        make_segments(1), context
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
        assert calls[0][2]["max_tokens"] == 4096
        assert calls[0][2]["messages"][0]["role"] == "user"
    else:
        assert calls[0][1]["authorization"] == "Bearer secret"
        assert calls[0][2]["model"] == "test-model"


@pytest.mark.asyncio
async def test_responses_adapter_accepts_nested_output_message_content(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    text = json.dumps({"translations": [{"id": "seg_0", "translation": "译文"}]})

    async def fake_http(_url, _headers, _payload, _timeout):
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}

    result = await HTTPTranslationProvider(
        make_config("openai_responses"), http_call=fake_http
    ).translate(make_segments(1), TranslationContext())
    assert result[0].translated_text == "译文"


@pytest.mark.asyncio
async def test_translation_batches_requests_and_preserves_segment_order(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    calls = []

    async def fake_http(_url, _headers, payload, _timeout):
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

    async def fake_http(*_args):
        return body

    provider = HTTPTranslationProvider(make_config("openai_chat"), http_call=fake_http)
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
            "/api/v1/projects",
            files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            data={"source_language": "ja", "target_language": "zh-CN"},
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

    async def empty_http(*_args):
        return {}

    provider = HTTPTranslationProvider(
        make_config("openai_chat"), cancel_event=cancel_event, http_call=empty_http
    )
    cancel_event.set()
    with pytest.raises(TranslationProviderError, match="翻译已取消"):
        await provider.translate(make_segments(1), TranslationContext())

    cancel_event.clear()

    async def fake_http(*_args):
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

    async def failing_http(*_args):
        raise httpx.ConnectError("down")

    provider = HTTPTranslationProvider(make_config("openai_chat"), http_call=failing_http)
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_HTTP_ERROR"

    async def slow_http(*_args):
        await asyncio.sleep(1.2)
        return {}

    provider = HTTPTranslationProvider(
        make_config("openai_chat", timeout_seconds=1), http_call=slow_http
    )
    with pytest.raises(TranslationProviderError) as error:
        await provider.translate(make_segments(1), TranslationContext())
    assert error.value.code == "TRANSLATION_TIMEOUT"


@pytest.mark.asyncio
async def test_additional_parameters_are_optional_and_protected(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    calls = []
    text = '{"translations": [{"id": "seg_0", "translation": "译文"}]}'

    async def fake_http(_url, _headers, payload, _timeout):
        calls.append(payload)
        return response_for("openai_chat", text)

    context = TranslationContext(additional_parameters={"temperature": 0.1, "max_tokens": 128})
    await HTTPTranslationProvider(make_config("openai_chat"), http_call=fake_http).translate(
        make_segments(1), context
    )
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["max_tokens"] == 128
    assert "response_format" not in calls[0]

    calls.clear()
    await HTTPTranslationProvider(make_config("openai_chat"), http_call=fake_http).translate(
        make_segments(1), TranslationContext()
    )
    assert "temperature" not in calls[0]
    assert "max_tokens" not in calls[0]
    assert "response_format" not in calls[0]

    for forbidden in ({"model": "other"}, {"Authorization": "x"}, {"api_key": "x"}):
        with pytest.raises(ValueError):
            TranslationConfig(additional_parameters=forbidden)
    with pytest.raises(ValueError):
        TranslationConfig(additional_parameters={"temperature": float("nan")})
    with pytest.raises(ValueError):
        TranslationConfig(additional_parameters={"nested": "x" * 70000})


def test_legacy_temperature_migrates_to_additional_parameters():
    config = TranslationConfig.model_validate({"temperature": 0.35})
    assert config.additional_parameters == {"temperature": 0.35}
    assert config.temperature == 0.35
    default = TranslationConfig()
    assert default.additional_parameters == {}


@pytest.mark.asyncio
async def test_async_http_cancel_closes_inflight_request(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_request):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    cancel_event = asyncio.Event()
    calls = []

    async def slow_call(url, headers, payload, timeout):
        calls.append(url)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(url, headers=headers, json=payload)
            return response.json()

    provider = HTTPTranslationProvider(
        make_config("openai_chat"), cancel_event=cancel_event, http_call=slow_call
    )
    task = asyncio.create_task(provider.translate(make_segments(1), TranslationContext()))
    await asyncio.wait_for(started.wait(), 1)
    cancel_event.set()
    with pytest.raises(TranslationProviderError) as error:
        await task
    assert error.value.code == "TRANSLATION_CANCELLED"
    await asyncio.wait_for(cancelled.wait(), 1)
    assert calls


@pytest.mark.asyncio
async def test_async_http_timeout_reclaims_request(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "secret")
    started = asyncio.Event()

    async def handler(_request):
        started.set()
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)

    async def slow_call(_url, _headers, _payload, _timeout):
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(_url, json=_payload)
            return response.json()

    provider = HTTPTranslationProvider(
        make_config("openai_chat", timeout_seconds=1), http_call=slow_call
    )
    task = asyncio.create_task(provider.translate(make_segments(1), TranslationContext()))
    await asyncio.wait_for(started.wait(), 1)
    with pytest.raises(TranslationProviderError) as error:
        await task
    assert error.value.code == "TRANSLATION_TIMEOUT"
