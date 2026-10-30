import pytest
from app.domain.node_registry import NODE_DEFINITIONS
from app.domain.workflow import WORKFLOW_GRAPH_CONSTRAINTS, default_workflow
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_capabilities_publish_only_mock_nodes_and_no_discovery_runtime(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "workflow", "asr", "export"}
    assert body["schema_version"] == "1.0"
    assert body["workflow"]["schema_version"] == "1.2"
    assert body["workflow"]["ports"] == [
        {"id": "audio", "type_id": "audio", "label": "音频", "capability_requirements": []},
        {"id": "transcript", "type_id": "transcript", "label": "转写结果", "capability_requirements": []},
        {"id": "text", "type_id": "text", "label": "文本产物", "capability_requirements": []},
    ]
    assert body["workflow"]["data_types"] == [
        {"id": "audio", "label": "音频", "parent_type_id": None, "capabilities": []},
        {"id": "transcript", "label": "转写结果", "parent_type_id": None, "capabilities": []},
        {"id": "text", "label": "文本产物", "parent_type_id": None, "capabilities": []},
    ]
    nodes = {node["type"]: node for node in body["workflow"]["nodes"]}
    assert set(nodes) == set(NODE_DEFINITIONS)
    assert set(nodes) == {"audio_input", "preprocess", "asr", "text_output", "audio_output"}
    assert body["workflow"]["constraints"] == WORKFLOW_GRAPH_CONSTRAINTS
    assert {node_type: node["is_source"] for node_type, node in nodes.items()} == {
        node_type: definition.is_source for node_type, definition in NODE_DEFINITIONS.items()
    }
    expected_graph = default_workflow("capabilities-default")
    assert body["workflow"]["default_graph"] == {
        "nodes": [node.model_dump(mode="json") for node in expected_graph.nodes],
        "edges": [edge.model_dump(mode="json") for edge in expected_graph.edges],
    }
    assert body["asr"] == {"providers": [{"id": "mock", "label": "Mock"}]}
    for node_type in ("preprocess", "asr"):
        provider = next(
            field for field in nodes[node_type]["config"]["fields"] if field["key"] == "provider"
        )
        assert provider["options"] == [{"value": "mock", "label": "Mock"}]
    text_format = next(
        field for field in nodes["text_output"]["config"]["fields"] if field["key"] == "format"
    )
    expected_formats = [
        {"id": option["value"], "label": option["label"]} for option in text_format["options"]
    ]
    assert {item["id"] for item in body["export"]["formats"]} == {
        "txt",
        "srt",
        "vtt",
        "lrc",
    }
    assert body["export"]["formats"] == expected_formats
    assert body["export"]["naming_modes"] == []
    asr_language = next(
        field for field in nodes["asr"]["config"]["fields"] if field["key"] == "language"
    )
    assert asr_language["min_length"] == 1
    assert asr_language["max_length"] == 32
    output_stem = next(
        field
        for field in nodes["text_output"]["config"]["fields"]
        if field["key"] == "filename_stem"
    )
    assert output_stem["min_length"] == 1
    assert output_stem["max_length"] == 128
    assert output_stem["validator"] == "safe_filename_stem"
    assert all(
        node["is_source"] is (node_type == "audio_input") for node_type, node in nodes.items()
    )
    lowered = response.text.lower()
    assert "crispasr" not in lowered and "ffmpeg" not in lowered


@pytest.mark.asyncio
async def test_removed_local_runtime_endpoints_and_legacy_runners_are_absent(tmp_path):
    app = create_app(tmp_path)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v1/settings/local-asr" not in paths
    assert "/api/v1/settings/local-asr/models" not in paths
    assert "/api/v1/projects/{pid}/run" not in paths
    assert "/api/v1/projects/{pid}/cancel" not in paths
    assert "/api/v1/projects/{pid}/audios/{aid}/run" not in paths
    assert "/api/v1/projects/{pid}/audios/{aid}/cancel" not in paths
    assert "/api/v1/projects/{pid}/audios/{aid}/outputs" not in paths
    assert "/api/v1/projects/{pid}/audios/{aid}/stages/{stage}" not in paths
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/settings/local-asr/models")).status_code == 404
        assert (await client.post("/api/v1/projects/missing/run", json={})).status_code in {
            404,
            405,
        }
