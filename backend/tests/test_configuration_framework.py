"""Public binding, namespace and recursive reuse boundaries for configuration graphs."""

import asyncio
from types import SimpleNamespace

import pytest
from app.application.configuration_evaluator import evaluate_configuration_graph
from app.domain.node_registry import TYPE_DEFINITIONS, register_type
from app.domain.schema import validate_schema_payload
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import setup
from test_inline_credentials import KEY_A, KEY_B, graph, run, save, update_body
from test_inline_credentials import cloud as cloud


def test_schema_checks_documents_without_scanning_business_json():
    validate_schema_payload(
        {"schema_version": "2.0", "nodes": [{"config": {"schema_version": "business-v9"}}]},
        "workflow",
    )
    validate_schema_payload(
        {"schema_version": "1.0", "metadata": {"schema_version": 99}}, "transcript"
    )


@pytest.mark.asyncio
async def test_old_settings_graph_run_and_batch_namespaces_are_ignored_and_untouched(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, _ = await setup(client, 1)
    root = app.state.store.project_dir(pid)
    paths = [
        tmp_path / "settings.json",
        root / "workflow.json",
        root / "workflow-revisions" / "1.json",
        root / "workflow-runs" / ("wfr_" + "1" * 32) / "run.json",
        root / "batches" / ("bat_" + "1" * 32 + ".json"),
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unreadable old document")
    before = {path: path.read_bytes() for path in paths}
    restarted = create_app(tmp_path)
    restarted.state.store.mark_workflow_runs_interrupted()
    restarted.state.batches.store.mark_interrupted()
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/settings")).json()["revision"] == 0
        document = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
        assert document["schema_version"] == "2.0" and document["revision"] == 0
        assert [node["type"] for node in document["nodes"]] == ["audio_input", "audio_output"]
        assert (await client.get(f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs")).json()[
            "total"
        ] == 0
        assert (await client.get(f"/api/v1/projects/{pid}/batches")).json()["total"] == 0
        assert (await client.get(f"/api/v1/projects/{pid}")).json()["schema_version"] == "1.0"
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.asyncio
async def test_explicit_shared_connection_and_key_rotation_rebuild_processing_dependency(
    tmp_path, cloud
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        body["nodes"].extend(
            [
                {"id": "second", "type": "qwen_asr", "config": {}},
                {"id": "second_output", "type": "text_output", "config": {}},
            ]
        )
        body["edges"].extend(
            [
                {
                    "id": "a2",
                    "source": "input",
                    "source_port": "audio",
                    "target": "second",
                    "target_port": "audio",
                },
                {
                    "id": "m2",
                    "source": "cloud_model",
                    "source_port": "model",
                    "target": "second",
                    "target_port": "model",
                },
                {
                    "id": "o2",
                    "source": "second",
                    "source_port": "transcript",
                    "target": "second_output",
                    "target_port": "transcript",
                },
            ]
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        source = await run(client, app, pid, aids[0], document)
        assert source.status.value == "completed"
        assert [call["api_key"] for call in cloud["calls"]] == [KEY_A, KEY_A]
        document = await save(client, pid, update_body(document, KEY_B, "rotate"))
        endpoint = f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs/plan"
        payload = {
            "workflow_revision": document["workflow_revision"],
            "rerun": {"source_run_id": source.id, "from_node_id": "output"},
        }
        blocked = await client.post(endpoint, json=payload)
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["issues"][0]["code"] == "RERUN_UPSTREAM_CHANGED"
        payload["rerun"]["from_node_id"] = "cloud"
        permitted = await client.post(endpoint, json=payload)
        assert permitted.json()["valid"], permitted.text
        assert set(permitted.json()["execute_node_ids"]) == {
            "cloud_connection",
            "cloud_model",
            "cloud",
            "output",
        }
        assert "second" in permitted.json()["skipped_node_ids"]
        assert len(cloud["calls"]) == 2
        public = source.model_dump_json()
        assert KEY_A not in public and KEY_B not in public


@pytest.mark.asyncio
async def test_binding_codecs_reject_forged_owner_connection_and_effective_model(tmp_path, cloud):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save(client, pid, graph(document))
        document = await save(client, pid, update_body(document))
        completed = await run(client, app, pid, aids[0], document)
    event = asyncio.Event()
    results = await evaluate_configuration_graph(
        app.state.store,
        completed,
        app.state.store.load_project(pid),
        event,
        required_ids={"cloud_model"},
    )
    context = SimpleNamespace(store=app.state.store, run=completed, cancel_event=event)
    connection = results["cloud_connection"].outputs["connection"].value.model_dump(mode="json")
    connection["owner_node_id"] = "cloud"
    with pytest.raises(Exception):
        await TYPE_DEFINITIONS["connection_binding"].checkpoint_codec.decode(connection, context)
    model = results["cloud_model"].outputs["model"].value.model_dump(mode="json")
    model["model_name"] = "forged-model"
    with pytest.raises(Exception):
        await TYPE_DEFINITIONS["model_binding"].checkpoint_codec.decode(model, context)


@pytest.mark.parametrize(
    "metadata", [{"color": "red"}, {"color": "#fff"}, {"color": "#ffffff", "url": "bad"}]
)
def test_type_presentation_rejects_undeclared_or_unsafe_color(metadata):
    with pytest.raises(ValueError, match="presentation"):
        register_type("invalid_configuration_color", presentation=metadata)
