import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from app.domain.node_execution import NodeExecutionCancelled, NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS
from app.domain.transcript import Segment, Transcript
from app.main import create_app
from app.providers.mock.providers import MockASRProvider
from httpx import ASGITransport, AsyncClient

from test_settings import save_body
from test_workflow import create_project


@pytest.fixture
def translator(monkeypatch):
    from app.adapters import llm_translation

    state = {
        "calls": [],
        "fail": False,
        "wait": False,
        "entered": asyncio.Event(),
        "cleaned": asyncio.Event(),
    }

    async def translate(**kwargs):
        state["calls"].append(kwargs)
        if state["fail"]:
            raise NodeExecutionError("LLM_TRANSLATION_OUTPUT_INVALID", "翻译结果无效")
        if state["wait"]:
            state["entered"].set()
            try:
                await kwargs["cancel_event"].wait()
                raise NodeExecutionCancelled()
            finally:
                state["cleaned"].set()
        return llm_translation.TranslationResult(
            translations={
                item["id"]: kwargs["target_language"] + "|" + item["text"]
                for item in kwargs["items"]
            },
            request_count=1,
            usage={},
        )

    async def transcribe(self, audio, project):
        return Transcript(
            project_id=project.id,
            stage="raw",
            language="ja",
            segments=[
                Segment(
                    id="stable-id",
                    start=1,
                    end=2,
                    source_text="original",
                    normalized_text="normalized",
                    language="ja",
                    speaker="speaker",
                    channel="left",
                    confidence=0.9,
                    metadata={"words": [{"text": "original", "start": 1, "end": 2}]},
                )
            ],
        )

    monkeypatch.setattr(llm_translation, "translate", translate)
    monkeypatch.setattr(MockASRProvider, "transcribe", transcribe)
    return state


def config(target="en"):
    return {
        "base_url": "https://translation.example/v1",
        "model": "future-model",
        "target_language": target,
    }


async def setup(client, *, two=False, translation_config=None, keys=True, parameters=False):
    pid, aid = await create_project(client)
    nodes = [
        {"id": "input", "type": "audio_input"},
        {"id": "asr", "type": "asr"},
        {
            "id": "llm1",
            "type": "llm_translate",
            "config": config() if translation_config is None else translation_config,
        },
        {"id": "raw", "type": "text_preview"},
    ]
    edges = [
        dict(id="a", source="input", source_port="audio", target="asr", target_port="audio"),
        dict(
            id="b", source="asr", source_port="transcript", target="llm1", target_port="transcript"
        ),
        dict(
            id="raw", source="asr", source_port="transcript", target="raw", target_port="transcript"
        ),
    ]
    last = "llm1"
    if two:
        nodes.extend(
            [
                {
                    "id": "llm2",
                    "type": "llm_translate",
                    "config": {
                        **config("zh"),
                        "base_url": "https://second.example/api",
                        "model": "second-model",
                    },
                },
                {"id": "first", "type": "text_preview"},
            ]
        )
        edges.extend(
            [
                dict(
                    id="c",
                    source="llm1",
                    source_port="transcript",
                    target="llm2",
                    target_port="transcript",
                ),
                dict(
                    id="first",
                    source="llm1",
                    source_port="transcript",
                    target="first",
                    target_port="transcript",
                ),
            ]
        )
        last = "llm2"
    if parameters:
        for field, value in (
            ("model", "parameter-model"),
            ("target_language", "xx-target"),
            ("source_language", "xx-source"),
        ):
            nodes.append({"id": field, "type": "string_value", "config": {"value": value}})
            edges.append(
                dict(id=field, source=field, source_port="string", target="llm1", target_port=field)
            )
    nodes.extend(
        [
            {"id": "preview", "type": "text_preview"},
            {"id": "output", "type": "text_output", "config": {"format": "txt"}},
        ]
    )
    for target in ("preview", "output"):
        edges.append(
            dict(
                id=target,
                source=last,
                source_port="transcript",
                target=target,
                target_port="transcript",
            )
        )
    for processor in list(nodes):
        if processor["type"] != "llm_translate":
            continue
        saved = processor.pop("config", {})
        processor["config"] = {}
        ident = processor["id"]
        nodes.extend(
            [
                {
                    "id": ident + "_connection",
                    "type": "service_connection",
                    "config": {"protocol": "openai_chat", "endpoint": saved.get("base_url", "")},
                },
                {
                    "id": ident + "_model",
                    "type": "model_config",
                    "config": {
                        "model_name": saved.get("model", ""),
                        "additional_parameters": saved.get("additional_parameters", {}),
                    },
                },
                {
                    "id": ident + "_context",
                    "type": "translation_context",
                    "config": {
                        key: saved[key]
                        for key in (
                            "source_language",
                            "target_language",
                            "instructions",
                            "background",
                            "glossary",
                        )
                        if key in saved
                    },
                },
                {
                    "id": ident + "_options",
                    "type": "translation_options",
                    "config": {
                        key: saved[key]
                        for key in (
                            "timeout_seconds",
                            "batch_size",
                            "max_batch_characters",
                            "context_segments",
                            "max_attempts",
                        )
                        if key in saved
                    },
                },
            ]
        )
        for item in edges:
            if item["target"] == ident and item["target_port"] != "transcript":
                if item["target_port"] == "model":
                    item.update(target=ident + "_model", target_port="model_name")
                else:
                    item["target"] = ident + "_context"
        for source, source_port, target, target_port in (
            (ident + "_connection", "connection", ident + "_model", "connection"),
            (ident + "_model", "model", ident, "model"),
            (ident + "_context", "translation_context", ident, "translation_context"),
            (ident + "_options", "translation_options", ident, "translation_options"),
        ):
            edges.append(
                dict(
                    id=source + "-" + target,
                    source=source,
                    source_port=source_port,
                    target=target,
                    target_port=target_port,
                )
            )
    body = {"expected_revision": 0, "nodes": nodes, "edges": edges}
    if keys:
        body.update(
            request_id="node-keys",
            credential_updates=[
                {
                    "node_id": node["id"],
                    "operation": "set",
                    "api_key": "KEY-" + node["id"].removesuffix("_connection"),
                }
                for node in nodes
                if node["type"] == "service_connection"
            ],
        )
    response = await client.put(f"/api/v1/projects/{pid}/workflow", json=body)
    assert response.status_code == 200, response.text
    return pid, aid, response.json()


async def start(client, app, pid, aid, document, **extra):
    response = await client.post(
        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
        json={"workflow_revision": document["revision"], **extra},
    )
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.jobs.tasks.values())
    return app.state.store.load_workflow_run(pid, aid, response.json()["id"])


def artifact_bytes(store, run, node_id):
    record = next(record for record in run.nodes if record.node_id == node_id)
    return store.workflow_artifact_path(
        run.project_id, run.audio_id, run.id, node_id, record.artifact.filename
    ).read_bytes()


@pytest.mark.asyncio
async def test_translation_chain_branches_preserve_original_and_rerun_current_view(
    tmp_path, translator
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(client, two=True)
        run = await start(client, app, pid, aid, document)
        assert run.status.value == "completed", run.error
        first, second = translator["calls"]
        assert first["items"] == [{"id": "stable-id", "text": "original"}]
        assert (first["source_language"], first["target_language"], first["api_key"]) == (
            "ja",
            "en",
            "KEY-llm1",
        )
        assert (second["items"], second["source_language"], second["api_key"]) == (
            [{"id": "stable-id", "text": "en|original"}],
            "en",
            "KEY-llm2",
        )
        assert first["connection"].base_url != second["connection"].base_url
        assert artifact_bytes(app.state.store, run, "raw") == b"original\n"
        assert artifact_bytes(app.state.store, run, "first") == b"en|original\n"
        assert (
            artifact_bytes(app.state.store, run, "preview")
            == artifact_bytes(app.state.store, run, "output")
            == b"zh|en|original\n"
        )
        original = json.loads(artifact_bytes(app.state.store, run, "asr"))
        translated = json.loads(artifact_bytes(app.state.store, run, "llm2"))
        assert translated["language"] == original["language"] == "ja"
        assert translated["metadata"]["current_text"] == {
            "version": "1",
            "field": "translated_text",
            "language": "zh",
        }
        for key, value in original["segments"][0].items():
            if key != "translated_text":
                assert translated["segments"][0][key] == value
        old = {
            p.relative_to(tmp_path): p.read_bytes()
            for p in app.state.store.workflow_run_dir(pid, aid, run.id).rglob("*")
            if p.is_file()
        }
        rerun = await start(
            client,
            app,
            pid,
            aid,
            document,
            request_id="rerun-output",
            rerun={"source_run_id": run.id, "from_node_id": "output"},
        )
        assert rerun.status.value == "completed"
        assert len(translator["calls"]) == 2
        assert artifact_bytes(app.state.store, rerun, "output") == b"zh|en|original\n"
        assert {
            p.relative_to(tmp_path): p.read_bytes()
            for p in app.state.store.workflow_run_dir(pid, aid, run.id).rglob("*")
            if p.is_file()
        } == old
        for path in tmp_path.rglob("*.json"):
            assert b"KEY-llm1" not in path.read_bytes() and b"KEY-llm2" not in path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft,code",
    [
        ({"model": "custom", "target_language": "en"}, "INLINE_CONNECTION_INVALID"),
        ({"base_url": "https://example.com/v1", "target_language": "en"}, "MODEL_NAME_REQUIRED"),
        ({"base_url": "https://example.com/v1", "model": "custom"}, "LLM_TARGET_LANGUAGE_REQUIRED"),
    ],
)
async def test_empty_drafts_defaults_save_but_single_and_batch_submit_reject(
    tmp_path, translator, draft, code
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        settings = (await client.get("/api/v1/settings")).json()
        assert settings["node_defaults"]["service_connection"]["config"]["endpoint"] == ""
        assert (await client.put("/api/v1/settings", json=save_body(settings))).status_code == 200
        pid, aid, document = await setup(client, translation_config=draft, keys=True)
        single = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["revision"]},
        )
        batch = await client.post(
            f"/api/v1/projects/{pid}/batches",
            json={
                "workflow_revision": document["revision"],
                "audio_ids": [aid],
                "request_id": "batch-draft",
            },
        )
        for response in (single, batch):
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == code
        assert translator["calls"] == []


@pytest.mark.asyncio
async def test_parameter_inputs_resolve_before_validation_and_record_effective_config(
    tmp_path, translator
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(
            client, translation_config={"base_url": "https://custom.example/v1"}, parameters=True
        )
        run = await start(client, app, pid, aid, document)
        assert run.status.value == "completed", run.error
        assert translator["calls"][0]["model"] == "parameter-model"
        assert translator["calls"][0]["source_language"] == "xx-source"
        assert translator["calls"][0]["target_language"] == "xx-target"
        record = next(record for record in run.nodes if record.node_id == "llm1_model")
        assert record.config["model_name"] == ""
        assert record.effective_config["model_name"] == "parameter-model"


@pytest.mark.asyncio
async def test_empty_connected_required_value_blocks_before_api_request(tmp_path, translator):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(client, parameters=True)
        next(node for node in document["nodes"] if node["id"] == "target_language")["config"][
            "value"
        ] = ""
        saved = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        assert saved.status_code == 200
        rejected = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": saved.json()["revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "LLM_TARGET_LANGUAGE_REQUIRED"
        assert translator["calls"] == []


@pytest.mark.asyncio
async def test_node_directory_and_no_environment_credential_fallback(
    tmp_path, translator, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "ENV-KEY-MUST-NOT-BE-USED")
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = (await client.get("/api/v1/capabilities")).json()
        node = next(
            node for node in catalog["workflow"]["nodes"] if node["type"] == "llm_translate"
        )
        assert node["category"] == {"id": "translation", "label": "翻译"}
        assert node["debug_only"] is False
        assert node["config"]["fields"] == []
        connection = next(
            value for value in catalog["workflow"]["nodes"] if value["type"] == "service_connection"
        )
        binding = connection["presentation"]["inline_connection_input"]
        assert binding["timeout_key"] is None and binding["provider_type"] == "service_connection"
        context = next(
            value
            for value in catalog["workflow"]["nodes"]
            if value["type"] == "translation_context"
        )
        fields = {field["key"]: field for field in context["config"]["fields"]}
        assert (
            fields["source_language"]["suggestion_source"]
            == fields["target_language"]["suggestion_source"]
            == "languages"
        )
        pid, aid, document = await setup(client, keys=False)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["revision"]},
        )
        assert (
            response.status_code == 422
            and response.json()["error"]["code"] == "CREDENTIAL_REQUIRED"
        )
        assert translator["calls"] == []
    from app.nodes.llm_translate import normalize

    with pytest.raises(ValueError):
        normalize({**config(), "api_key_env": "OPENAI_API_KEY"})


@pytest.mark.asyncio
async def test_real_adapter_protocol_renders_current_translation_without_auth_leak(
    tmp_path, monkeypatch, caplog
):
    from app.adapters import llm_translation

    requests = []

    async def reply(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert set(payload) == {"model", "messages"}
        assert payload["model"] == "future-model"
        assert request.headers["authorization"] == "Bearer KEY-llm1"
        user = json.loads(payload["messages"][1]["content"])
        assert user["source_language"] == "ja" and user["target_language"] == "en"
        translations = [
            {"id": item["id"], "translation": "translated-" + item["text"]}
            for item in user["items"]
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"translations": translations})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        llm_translation, "_client", lambda: AsyncClient(transport=httpx.MockTransport(reply))
    )
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(client)
        run = await start(client, app, pid, aid, document)
        assert run.status.value == "completed", [
            (record.node_id, record.error) for record in run.nodes
        ]
        assert (
            requests and str(requests[0].url) == "https://translation.example/v1/chat/completions"
        )
        output = artifact_bytes(app.state.store, run, "output").decode()
        assert output.startswith("translated-")
        assert "KEY-llm1" not in caplog.text


@pytest.mark.asyncio
async def test_failed_translation_never_publishes_complete_artifact_or_checkpoint(
    tmp_path, translator, caplog
):
    translator["fail"] = True
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(client)
        run = await start(client, app, pid, aid, document)
        record = next(record for record in run.nodes if record.node_id == "llm1")
        assert record.status.value == "failed"
        assert record.artifact is record.checkpoint is None
        assert record.error.type == "LLM_TRANSLATION_OUTPUT_INVALID"
        path = app.state.store.workflow_artifact_path(pid, aid, run.id, "llm1", "transcript.json")
        assert not path.exists()
        assert "KEY-llm1" not in caplog.text and "original" not in caplog.text


@pytest.mark.asyncio
async def test_translation_cancel_cleans_up_and_publishes_no_partial_result(tmp_path, translator):
    translator["wait"] = True
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid, document = await setup(client)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["revision"]},
        )
        assert response.status_code == 202
        await asyncio.wait_for(translator["entered"].wait(), 5)
        rid = response.json()["id"]
        app.state.jobs.cancel_workflow(pid, aid, rid)
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert translator["cleaned"].is_set()
        run = app.state.store.load_workflow_run(pid, aid, rid)
        record = next(record for record in run.nodes if record.node_id == "llm1")
        assert run.status.value == "cancelled"
        assert record.artifact is record.checkpoint is None


def test_only_affected_text_renderers_change_execution_version():
    assert NODE_DEFINITIONS["text_output"].execution_version == "2"
    assert NODE_DEFINITIONS["text_preview"].execution_version == "2"
    assert NODE_DEFINITIONS["audio_preview"].execution_version == "1"
    assert NODE_DEFINITIONS["asr"].execution_version == "2"
    with pytest.raises(ValueError, match="validate_execution_config"):
        from app.domain.node_registry import validate_node_definition

        validate_node_definition(
            replace(NODE_DEFINITIONS["llm_translate"], validate_execution_config=3)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "segments,code",
    [
        ([], "LLM_TRANSLATION_INPUT_EMPTY"),
        ([Segment(id="empty", source_text="")], "LLM_TRANSLATION_INPUT_INVALID"),
    ],
)
async def test_empty_or_invalid_transcript_never_reaches_translation_api(
    translator, tmp_path, segments, code
):
    from app.application.workflow_asr import TranscriptData
    from app.domain.context_values import TranslationContext
    from app.domain.node_execution import NodeData, NodeExecutionContext
    from app.domain.workflow import WorkflowNode
    from app.nodes.llm_translate import execute

    value = Transcript(project_id="test", stage="raw", language="ja", segments=segments)
    context = NodeExecutionContext(
        store=None,
        run=None,
        node=WorkflowNode(id="llm", type="llm_translate", config={}),
        inputs={
            "translation_context": NodeData(
                "translation_context", TranslationContext(target_language="en")
            ),
            "transcript": NodeData(
                "transcript", TranscriptData(path=tmp_path / "none.json", transcript=value)
            ),
        },
        project=None,
        cancel_event=asyncio.Event(),
    )
    with pytest.raises(NodeExecutionError) as error:
        await execute(context)
    assert error.value.code == code
    assert translator["calls"] == []
