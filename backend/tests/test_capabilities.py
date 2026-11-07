from dataclasses import replace

import pytest
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    NodeCategory,
    NodeDefinition,
    node_catalog,
    register_node,
    validate_node_definition,
)
from app.domain.workflow import (
    WORKFLOW_GRAPH_CONSTRAINTS,
    default_workflow,
    workflow_semantic_digest,
    workflow_snapshot,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_capabilities_publish_registered_nodes_and_no_discovery_runtime(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "workflow", "asr", "export"}
    assert body["schema_version"] == "1.0"
    assert body["workflow"]["schema_version"] == "1.9"
    assert body["workflow"]["ports"][:3] == [
        {"id": "audio", "type_id": "audio", "label": "音频", "capability_requirements": []},
        {
            "id": "transcript",
            "type_id": "transcript",
            "label": "转写结果",
            "capability_requirements": [],
        },
        {"id": "text", "type_id": "text", "label": "文本产物", "capability_requirements": []},
    ]
    assert body["workflow"]["data_types"][:3] == [
        {"id": "audio", "label": "音频", "parent_type_id": None, "capabilities": []},
        {"id": "transcript", "label": "转写结果", "parent_type_id": None, "capabilities": []},
        {"id": "text", "label": "文本产物", "parent_type_id": None, "capabilities": []},
    ]
    nodes = {node["type"]: node for node in body["workflow"]["nodes"]}
    assert set(nodes) == set(NODE_DEFINITIONS)
    assert set(nodes) == {
        "audio_input",
        "preprocess",
        "asr",
        "text_output",
        "audio_output",
        "audio_preview",
        "text_preview",
        "string_value",
        "integer_value",
        "number_value",
        "boolean_value",
        "source_info",
        "qwen_asr",
    }
    assert {
        category: {node["type"] for node in nodes.values() if node["category"]["id"] == category}
        for category in {node["category"]["id"] for node in nodes.values()}
    } == {
        "input": {"audio_input", "source_info"},
        "preprocessing": {"preprocess"},
        "speech_recognition": {"asr", "qwen_asr"},
        "preview": {"audio_preview", "text_preview"},
        "output": {"audio_output", "text_output"},
        "parameters": {"string_value", "integer_value", "number_value", "boolean_value"},
    }
    assert nodes["asr"]["label"] == "语音识别（Mock）"
    assert nodes["asr"]["category"] == {"id": "speech_recognition", "label": "语音识别"}
    assert nodes["qwen_asr"]["category"] == nodes["asr"]["category"]
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
    naming_mode = next(
        field for field in nodes["text_output"]["config"]["fields"] if field["key"] == "naming_mode"
    )
    assert body["export"]["naming_modes"] == [
        {"id": option["value"], "label": option["label"]} for option in naming_mode["options"]
    ]
    assert naming_mode["default"] == "exact"
    assert naming_mode["creation_default"] == "prefix_sequence"
    assert nodes["audio_input"]["presentation"]["resource_input"] == {
        "resource_type": "audio",
        "config_key": "audio_id",
        "collection_key": "audio_ids",
    }
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
    assert all(
        node["is_parameter_source"] is (node_type.endswith("_value") or node_type == "source_info")
        for node_type, node in nodes.items()
    )
    assert all(
        "input_port" in field for node in nodes.values() for field in node["config"]["fields"]
    )
    assert all(
        "creation_default" in field for node in nodes.values() for field in node["config"]["fields"]
    )
    ports = {port["id"]: port for port in body["workflow"]["ports"]}
    for name in ("language", "filename_stem", "text_format", "string"):
        assert ports[name]["type_id"] == "string"
    types = {item["id"]: item for item in body["workflow"]["data_types"]}
    assert types["integer"]["parent_type_id"] == "number"
    assert asr_language["input_port"] == "language"
    assert output_stem["input_port"] == "filename_stem"
    assert text_format["input_port"] == "text_format"
    lowered = response.text.lower()
    assert "crispasr" not in lowered and "ffmpeg" not in lowered


@pytest.mark.asyncio
async def test_dynamic_categories_and_legacy_extension_are_display_only(tmp_path, monkeypatch):
    plugin = replace(
        NODE_DEFINITIONS["asr"],
        type="plugin_asr",
        category=NodeCategory("plugin_tools", "Plugin tools"),
    )
    legacy = NodeDefinition(
        type="legacy_extension",
        label="Legacy extension",
        input_ports=(),
        output_ports=("string",),
        terminal=False,
        config_kind="none",
        execute=NODE_DEFINITIONS["string_value"].execute,
    )
    assert legacy.category is None
    # Register through the public extension path, with automatic fixture cleanup.
    for definition in (plugin, legacy):
        monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
        NODE_DEFINITIONS.pop(definition.type)
        register_node(definition)
    document = default_workflow("category_test")
    before = workflow_snapshot(document).model_dump(mode="json")
    digest = workflow_semantic_digest(document.nodes, document.edges)
    monkeypatch.setitem(
        NODE_DEFINITIONS,
        "audio_output",
        replace(NODE_DEFINITIONS["audio_output"], category=plugin.category),
    )
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    nodes = {node["type"]: node for node in response.json()["workflow"]["nodes"]}
    assert nodes["plugin_asr"]["category"] == {"id": "plugin_tools", "label": "Plugin tools"}
    assert nodes["legacy_extension"]["category"] is None
    assert workflow_snapshot(document).model_dump(mode="json") == before
    assert workflow_semantic_digest(document.nodes, document.edges) == digest
    assert all("category" not in node for node in before["nodes"])


@pytest.mark.parametrize(
    "category",
    [
        NodeCategory("bad/id", "Bad"),
        NodeCategory("Uppercase", "Bad"),
        NodeCategory("valid", ""),
        NodeCategory("valid", " padded "),
        NodeCategory("valid", "control\nlabel"),
        NodeCategory("valid", "x" * 129),
        {"id": "valid", "label": "Untyped"},
    ],
)
def test_invalid_node_category_declarations_are_rejected(category):
    with pytest.raises(ValueError, match="invalid node category"):
        validate_node_definition(replace(NODE_DEFINITIONS["asr"], category=category))


def test_category_label_conflicts_are_rejected_at_registration_and_catalog(monkeypatch):
    conflict = replace(
        NODE_DEFINITIONS["asr"],
        type="conflicting_category",
        category=NodeCategory("speech_recognition", "Different label"),
    )
    with pytest.raises(ValueError, match="conflicting labels"):
        register_node(conflict)
    monkeypatch.setitem(NODE_DEFINITIONS, conflict.type, conflict)
    with pytest.raises(ValueError, match="conflicting labels"):
        node_catalog()


def test_inline_connection_mode_must_exist_in_declared_select_options():
    definition = NODE_DEFINITIONS["qwen_asr"]
    presentation = {
        **definition.presentation,
        "inline_connection_input": {
            **definition.presentation["inline_connection_input"],
            "mode_value": "unlisted_mode",
        },
    }
    invalid = replace(definition, type="invalid_inline_mode", presentation=presentation)
    with pytest.raises(ValueError, match="inline connection mode must be a declared option"):
        register_node(invalid)
    assert invalid.type not in NODE_DEFINITIONS


@pytest.mark.parametrize(
    "role",
    [
        "mode_key",
        "mode_value",
        "base_url_key",
        "timeout_key",
        "region_key",
        "api_key_env_key",
        "credential_ref_key",
    ],
)
@pytest.mark.parametrize("value", ["contains space", "x" * 65])
def test_inline_connection_binding_values_must_be_safe_identifiers(role, value):
    definition = NODE_DEFINITIONS["qwen_asr"]
    presentation = {
        **definition.presentation,
        "inline_connection_input": {
            **definition.presentation["inline_connection_input"],
            role: value,
        },
    }
    with pytest.raises(ValueError, match="invalid inline connection declaration"):
        validate_node_definition(replace(definition, presentation=presentation))


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
