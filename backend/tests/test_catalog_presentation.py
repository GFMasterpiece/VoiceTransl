import asyncio
from dataclasses import replace

import pytest
from app.domain.node_registry import NODE_DEFINITIONS, node_catalog, validate_node_definition
from app.domain.workflow import workflow_semantic_digest, workflow_snapshot
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_settings import save_body
from test_workflow import create_project, save_graph


def test_debug_visibility_is_explicit_registry_metadata(monkeypatch):
    nodes = {node["type"]: node for node in node_catalog()}
    assert all(type(node["debug_only"]) is bool for node in nodes.values())
    assert {key for key, node in nodes.items() if node["debug_only"]} == {"asr", "preprocess"}
    template = NODE_DEFINITIONS["string_value"]
    for node_type, label, flag in (
        ("ordinary_extension", "Ordinary", True),
        ("mock_extension", "Mock", False),
    ):
        definition = replace(template, type=node_type, label=label, debug_only=flag)
        validate_node_definition(definition)
        monkeypatch.setitem(NODE_DEFINITIONS, node_type, definition)
    nodes = {node["type"]: node for node in node_catalog()}
    assert nodes["ordinary_extension"]["debug_only"] is True
    assert nodes["mock_extension"]["debug_only"] is False
    with pytest.raises(ValueError, match="debug_only"):
        validate_node_definition(replace(template, debug_only="true"))


@pytest.mark.asyncio
async def test_qwen_default_fields_follow_declared_order_and_connection_roles(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        settings = (await client.get("/api/v1/settings")).json()
        catalog = (await client.get("/api/v1/capabilities")).json()
    assert catalog["workflow"]["schema_version"] == "1.15"
    assert settings["schema_version"] == "1.0"
    expected = {
        "qwen_asr": [
            "base_url",
            "timeout_seconds",
            "region",
            "model",
            "language",
            "additional_parameters",
        ],
        "qwen_asr_streaming": [
            "endpoint",
            "timeout_seconds",
            "model",
            "language",
            "additional_parameters",
        ],
    }
    nodes = {node["type"]: node for node in catalog["workflow"]["nodes"]}
    for node_type, ordered in expected.items():
        fields = settings["node_defaults"][node_type]["fields"]
        assert [field["key"] for field in fields] == ordered
        binding = nodes[node_type]["presentation"]["inline_connection_input"]
        connection_keys = {
            binding[role]
            for role in ("base_url_key", "timeout_key", "region_key")
            if binding[role] is not None
        }
        assert [key for key in ordered if key in connection_keys] == ordered[: len(connection_keys)]
        assert [key for key in ordered if key not in connection_keys] == [
            "model",
            "language",
            "additional_parameters",
        ]
        assert binding["credential_ref_key"] not in ordered
        assert "api_key" not in ordered
        assert fields == [
            {field.key: field for field in NODE_DEFINITIONS[node_type].fields}[key].model_dump(
                mode="json"
            )
            for key in ordered
        ]


@pytest.mark.asyncio
async def test_display_metadata_changes_preserve_saved_data_and_checkpoint_semantics(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                {"id": "input", "type": "audio_input"},
                {"id": "pre", "type": "preprocess"},
                {"id": "asr", "type": "asr"},
                {"id": "output", "type": "text_output"},
            ],
            "edges": [
                {
                    "id": "a",
                    "source": "input",
                    "source_port": "audio",
                    "target": "pre",
                    "target_port": "audio",
                },
                {
                    "id": "b",
                    "source": "pre",
                    "source_port": "audio",
                    "target": "asr",
                    "target_port": "audio",
                },
                {
                    "id": "c",
                    "source": "asr",
                    "source_port": "transcript",
                    "target": "output",
                    "target_port": "transcript",
                },
            ],
        }
        document = await save_graph(client, pid, 0, graph)
        saved = await client.put(
            "/api/v1/settings", json=save_body((await client.get("/api/v1/settings")).json())
        )
        assert saved.status_code == 200
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["revision"]},
        )
        assert response.status_code == 202, response.text
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = app.state.store.load_workflow_run(pid, aid, response.json()["id"])
        assert run.status.value == "completed"
        snapshot = workflow_snapshot(app.state.store.load_workflow(pid))
        semantic = workflow_semantic_digest(snapshot.nodes, snapshot.edges)
        original = {
            path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        }
        qwen = NODE_DEFINITIONS["qwen_asr"]
        monkeypatch.setitem(
            NODE_DEFINITIONS,
            "qwen_asr",
            replace(qwen, settings_default_fields=tuple(reversed(qwen.settings_default_fields))),
        )
        monkeypatch.setitem(
            NODE_DEFINITIONS, "asr", replace(NODE_DEFINITIONS["asr"], debug_only=False)
        )
        metadata = (await client.get("/api/v1/settings")).json()
        assert [field["key"] for field in metadata["node_defaults"]["qwen_asr"]["fields"]] == list(
            reversed(qwen.settings_default_fields)
        )
        assert (
            metadata["node_defaults"]["qwen_asr"]["config"]
            == saved.json()["node_defaults"]["qwen_asr"]["config"]
        )
        assert (await client.get("/api/v1/capabilities")).status_code == 200
        plan = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/plan",
            json={
                "workflow_revision": document["revision"],
                "rerun": {"source_run_id": run.id, "from_node_id": "output"},
            },
        )
        assert plan.json()["valid"], plan.text
        snapshot = workflow_snapshot(app.state.store.load_workflow(pid))
        assert workflow_semantic_digest(snapshot.nodes, snapshot.edges) == semantic
        assert {
            path.relative_to(tmp_path): path.read_bytes()
            for path in tmp_path.rglob("*")
            if path.is_file()
        } == original
