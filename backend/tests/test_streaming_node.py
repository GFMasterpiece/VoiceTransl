import asyncio
import json
from dataclasses import replace

import pytest
from app.domain.inline_connection import InlineConnection
from app.domain.node_execution import NodeExecutionCancelled, NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS, node_catalog, validate_node_definition
from app.domain.transcript import Segment, Transcript
from app.main import create_app
from app.nodes.qwen_asr_streaming import normalize
from app.storage.secret_store import SecretStore
from httpx import ASGITransport, AsyncClient

from test_batches import finished, setup, submit
from test_inline_credentials import KEY_A, KEY_B, config_of, graph, run, save, update_body


def streaming_graph(document, config=None, node_type="qwen_asr_streaming"):
    result = graph(document)
    result["nodes"][1].update(type=node_type, config=config or {})
    return result


@pytest.fixture
def stream(monkeypatch):
    from app.adapters.qwen_asr_streaming import QwenStreamingASRAdapter

    state = {"calls": [], "fail": set(), "block": False, "started": asyncio.Event()}

    async def transcribe(self, **kwargs):
        state["calls"].append(kwargs)
        state["started"].set()
        if state["block"]:
            await kwargs["cancel_event"].wait()
            raise NodeExecutionCancelled("QWEN_STREAMING_CANCELLED", "本地音频推送已停止")
        if kwargs["audio_path"] in state["fail"]:
            raise NodeExecutionError("QWEN_STREAMING_FAILED", "测试识别失败")
        return Transcript(
            project_id=kwargs["project_id"],
            stage="raw",
            language=kwargs["language"],
            segments=[
                Segment(
                    id="s1",
                    start=0.25,
                    end=1.75,
                    source_text="Streaming result",
                    language=kwargs["language"],
                )
            ],
        )

    monkeypatch.setattr(QwenStreamingASRAdapter, "transcribe", transcribe)
    return state


@pytest.mark.asyncio
async def test_inline_only_draft_does_not_inherit_filetrans_profile_or_env(
    tmp_path, monkeypatch, stream
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY_B)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        old = await save(client, pid, graph(document))
        old = await save(client, pid, update_body(old))
        old_ref = config_of(old)["credential_ref"]
        draft = await save(client, pid, streaming_graph(old))
        config = config_of(draft)
        assert config["credential_ref"] is None
        assert not {"connection_mode", "provider_profile_id", "api_key_env", "region"} & set(config)
        assert config["endpoint"] == "wss://maas.qianwenaiapi.com/api-ws/v1/inference"
        assert "format" not in config and "sample_rate" not in config
        validated = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": draft["nodes"], "edges": draft["edges"]},
        )
        assert validated.json()["valid"]

        assert not hasattr(app.state.store, "load_provider_profiles")
        rejected = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": draft["workflow_revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "CREDENTIAL_REQUIRED"
        assert not stream["calls"]
        foreign = streaming_graph(draft, {"credential_ref": old_ref})
        rejected = await client.put(f"/api/v1/projects/{pid}/workflow", json=foreign)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "CREDENTIAL_OWNER_MISMATCH"
        saved = await save(client, pid, update_body(draft, request_id="stream-key"))
        assert (
            SecretStore(tmp_path).read(
                pid, "cloud", "qwen_asr_streaming", config_of(saved)["credential_ref"]
            )
            == KEY_A
        )
        completed = await run(client, app, pid, aids[0], saved)
        assert completed.status.value == "completed"
        assert completed.workflow.provider_profiles_snapshot is None
        assert stream["calls"][-1]["api_key"] == KEY_A
        assert isinstance(stream["calls"][-1]["connection"], InlineConnection)
        assert not hasattr(stream["calls"][-1]["connection"], "api_key_env")
        assert KEY_A not in json.dumps(completed.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_streaming_credentials_are_owned_by_each_node_instance(tmp_path, stream):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        first = await save(client, pid, streaming_graph(document))
        first = await save(client, pid, update_body(first))
        payload = streaming_graph(first)
        second = {"id": "second", "type": "qwen_asr_streaming", "config": {}}
        payload["nodes"].extend([second, {"id": "out2", "type": "text_output", "config": {}}])
        payload["edges"].extend(
            [
                {
                    "id": "c",
                    "source": "input",
                    "source_port": "audio",
                    "target": "second",
                    "target_port": "audio",
                },
                {
                    "id": "d",
                    "source": "second",
                    "source_port": "transcript",
                    "target": "out2",
                    "target_port": "transcript",
                },
            ]
        )
        payload["nodes"][1]["config"] = config_of(first)
        second["config"] = {"credential_ref": config_of(first)["credential_ref"]}
        rejected = await client.put(f"/api/v1/projects/{pid}/workflow", json=payload)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "CREDENTIAL_OWNER_MISMATCH"
        second["config"] = {}
        payload.update(
            request_id="second-key",
            credential_updates=[
                {"node_id": "second", "operation": "set", "api_key": KEY_B},
            ],
        )
        saved = await save(client, pid, payload)
        completed = await run(client, app, pid, aids[0], saved)
        assert completed.status.value == "completed"
        assert [call["api_key"] for call in stream["calls"]] == [KEY_A, KEY_B]


@pytest.mark.asyncio
async def test_streaming_batch_freezes_connection_key_and_retry_sequence(tmp_path, stream):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        saved = await save(client, pid, streaming_graph(document))
        saved = await save(client, pid, update_body(saved))
        stream["fail"].add(
            app.state.store.scope(pid, aids[1]).source_path(
                pid, app.state.store.audio(pid, aids[1]).source.path
            )
        )
        created = await submit(client, pid, aids, saved)
        rotated_payload = update_body(saved, KEY_B, "rotate")
        config_of(rotated_payload)["endpoint"] = "wss://new.example/inference"
        rotated = await save(client, pid, rotated_payload)
        app.state.jobs.semaphore.release()
        batch = await finished(app, pid, created["id"])
        assert batch.status == "partial"
        assert {call["api_key"] for call in stream["calls"]} == {KEY_A}
        assert {call["connection"].base_url for call in stream["calls"]} == {
            "wss://maas.qianwenaiapi.com/api-ws/v1/inference"
        }
        stream["fail"].clear()
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 202, response.text
        retried = await finished(app, pid, response.json()["id"])
        assert retried.status == "completed" and retried.items[0].sequence == 2
        assert stream["calls"][-1]["api_key"] == KEY_A
        assert config_of(rotated)["credential_ref"] != config_of(saved)["credential_ref"]
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_streaming_language_parameter_and_timestamp_export(tmp_path, stream):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        payload = streaming_graph(document, {"language": "ja", "model": "future-stream-model"})
        payload["nodes"][2]["config"]["format"] = "srt"
        payload["nodes"].append({"id": "lang", "type": "string_value", "config": {"value": "en"}})
        payload["edges"].append(
            {
                "id": "language",
                "source": "lang",
                "source_port": "string",
                "target": "cloud",
                "target_port": "language",
            }
        )
        saved = await save(client, pid, payload)
        saved = await save(client, pid, update_body(saved))
        completed = await run(client, app, pid, aids[0], saved)
        assert completed.status.value == "completed"
        assert stream["calls"][0]["language"] == "en"
        assert stream["calls"][0]["model"] == "future-stream-model"
        assert config_of(completed.workflow.model_dump(mode="json"))["language"] == "ja"
        output = next(record for record in completed.nodes if record.node_id == "output")
        downloaded = await client.get(output.artifact.download_url)
        assert downloaded.status_code == 200
        assert "00:00:00,250 --> 00:00:01,750" in downloaded.text


@pytest.mark.asyncio
async def test_streaming_cancel_uses_shared_cleanup_and_safe_history(tmp_path, stream):
    app = create_app(tmp_path)
    stream["block"] = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        saved = await save(client, pid, streaming_graph(document))
        saved = await save(client, pid, update_body(saved))
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert response.status_code == 202
        await asyncio.wait_for(stream["started"].wait(), 5)
        run_id = response.json()["id"]
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs/{run_id}/cancel"
        )
        assert response.status_code == 202, response.text
        await asyncio.gather(*app.state.jobs.tasks.values())
        cancelled = app.state.store.load_workflow_run(pid, aids[0], run_id)
        assert cancelled.status.value == "cancelled"
        cloud = next(record for record in cancelled.nodes if record.node_id == "cloud")
        assert cloud.error.type == "QWEN_STREAMING_CANCELLED"
        assert not app.state.jobs.reservations
    await app.state.jobs.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com/path",
        "ws://example.com/path",
        "ws://192.168.1.1/path",
        "wss://user:secret@example.com/path",
        "wss://example.com/path?key=secret",
        "wss://example.com/path#secret",
        " wss://example.com/path",
        "wss://example.com:bad/path",
        "wss://example.com:0/path",
        "wss://example.com/has space",
    ],
)
def test_streaming_rejects_unsafe_or_non_websocket_endpoints(endpoint):
    with pytest.raises(ValueError, match="Qwen Streaming配置无效"):
        normalize({"endpoint": endpoint})


@pytest.mark.parametrize(
    "endpoint",
    [
        "wss://future.example/custom/path",
        "ws://localhost:8014/inference",
        "ws://127.0.0.1:8014/inference",
        "ws://[::1]:8014/inference",
    ],
)
def test_streaming_supports_complete_endpoint_and_local_test_transport(endpoint):
    assert normalize({"endpoint": endpoint})["endpoint"] == endpoint


def test_streaming_catalog_is_independent_and_has_honest_data_handling():
    node = next(item for item in node_catalog() if item["type"] == "qwen_asr_streaming")
    assert "provider_profile_input" not in node["presentation"]
    binding = node["presentation"]["inline_connection_input"]
    assert binding["provider_type"] == "qwen_asr_streaming"
    assert binding["region_key"] is None
    assert not {"mode_key", "mode_value", "api_key_env_key"} & set(binding)
    assert binding["data_handling"] == {
        "temporary_upload_retention_hours": None,
        "remote_cancel_pending_only": False,
        "cancel_behavior": "close_stream",
    }
    fields = {field["key"]: field for field in node["config"]["fields"]}
    assert fields["language"]["input_port"] == "language"
    assert "format" not in fields and "sample_rate" not in fields
    assert all(
        fields[key]["input_port"] is None
        for key in ("endpoint", "timeout_seconds", "credential_ref")
    )
    assert "api_key" not in fields and "api_key_env" not in fields


@pytest.mark.parametrize(
    "change",
    [
        {"mode_key": "mode"},
        {"mode_value": "inline"},
        {"base_url_key": None},
        {"credential_ref_key": None},
        {"label": " "},
        {"provider_type": "unsafe/type"},
        {
            "data_handling": {
                "temporary_upload_retention_hours": None,
                "remote_cancel_pending_only": False,
                "cancel_behavior": "unknown",
            }
        },
    ],
)
def test_inline_only_declarations_reject_invalid_roles_or_metadata(change):
    original = NODE_DEFINITIONS["qwen_asr_streaming"]
    presentation = {
        "inline_connection_input": {
            **original.presentation["inline_connection_input"],
            **change,
        }
    }
    with pytest.raises(ValueError):
        validate_node_definition(replace(original, presentation=presentation))


@pytest.mark.asyncio
async def test_independent_inline_provider_extension_needs_no_profile_or_scheduler_changes(
    tmp_path, monkeypatch, stream
):
    original = NODE_DEFINITIONS["qwen_asr_streaming"]
    plugin = replace(
        original,
        type="stream_extension",
        presentation={
            "inline_connection_input": {
                **original.presentation["inline_connection_input"],
                "provider_type": "stream_extension",
                "label": "Extension stream",
            }
        },
    )
    validate_node_definition(plugin)
    monkeypatch.setitem(NODE_DEFINITIONS, plugin.type, plugin)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        saved = await save(client, pid, streaming_graph(document, node_type=plugin.type))
        saved = await save(client, pid, update_body(saved))
        assert (await run(client, app, pid, aids[0], saved)).status.value == "completed"
        assert stream["calls"][-1]["connection"].provider_type == "stream_extension"
