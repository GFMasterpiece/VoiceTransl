import asyncio

import pytest
from app.application.workflow_runner import AudioData, _ensure_runtime_ports
from app.domain.node_execution import NodeResult
from app.domain.node_registry import NODE_DEFINITIONS
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_capabilities_are_registry_driven_and_asr_failure_is_local(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "workflow", "asr", "export"}
    assert body["schema_version"] == "1.0"
    assert body["workflow"]["ports"] == ["audio", "transcript", "text"]
    node_by_type = {node["type"]: node for node in body["workflow"]["nodes"]}
    assert set(node_by_type) == set(NODE_DEFINITIONS)
    assert node_by_type["audio_input"]["max_instances"] == 1
    assert node_by_type["audio_input"]["removable"] is False
    asr = node_by_type["asr"]
    profile = next(
        field for field in asr["config"]["fields"] if field["key"] == "provider_profile_id"
    )
    assert profile["default"] is None
    assert asr["config"]["kind"] == "asr"
    assert body["asr"]["status"] in {"available", "unavailable"}
    assert all(
        item["task"] in {"asr", "tts", "translation", "music", "other"}
        for item in body["asr"]["backend_details"]
    )
    assert body["asr"]["providers"] == [
        {"id": "mock", "label": "Mock"},
        {"id": "crispasr", "label": "CrispASR"},
    ]
    assert {item["id"] for item in body["export"]["formats"]} == {"srt", "vtt", "lrc"}
    assert "txt" not in {item["id"] for item in body["export"]["formats"]}


@pytest.mark.asyncio
async def test_capabilities_keeps_workflow_when_asr_discovery_fails(tmp_path, monkeypatch):
    app = create_app(tmp_path)

    async def unavailable():
        raise RuntimeError("local asr unavailable")

    monkeypatch.setattr(app.state.local_asr_models, "get", unavailable)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["workflow"]["nodes"]
    assert body["asr"]["status"] == "unavailable"
    assert body["asr"]["backends"] == []
    assert body["asr"]["backend_details"] == []
    assert body["asr"]["message"]
    assert "Mock" in body["asr"]["message"]


@pytest.mark.asyncio
async def test_registered_node_validator_and_executor_run_through_generic_runner(tmp_path):
    from app.domain.node_registry import NODE_DEFINITIONS, NodeDefinition, register_node

    def normalize(config):
        if config:
            raise ValueError("test node config must be empty")
        return {}

    async def execute(context):
        upstream = context.input("audio").value
        assert isinstance(upstream, AudioData)
        result = NodeResult()
        result.add("audio", upstream)
        return result

    definition = NodeDefinition(
        type="test_passthrough",
        label="Test",
        input_ports=("audio",),
        output_ports=("audio",),
        terminal=False,
        config_kind="none",
        required_inbound=1,
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
    _ensure_runtime_ports()
    try:
        register_node(definition)
        app = create_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF" + b"\0" * 40, "audio/wav")},
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            project = response.json()["project"]
            graph = {
                "nodes": [
                    {
                        "id": "input",
                        "type": "audio_input",
                        "config": {},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "id": "test",
                        "type": "test_passthrough",
                        "config": {},
                        "position": {"x": 1, "y": 0},
                    },
                    {
                        "id": "out",
                        "type": "audio_output",
                        "config": {"filename_stem": "out"},
                        "position": {"x": 2, "y": 0},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "input",
                        "source_port": "audio",
                        "target": "test",
                        "target_port": "audio",
                    },
                    {
                        "id": "e2",
                        "source": "test",
                        "source_port": "audio",
                        "target": "out",
                        "target_port": "audio",
                    },
                ],
            }
            saved = await client.put(
                f"/api/v1/projects/{project['id']}/workflow",
                json={"expected_revision": 0, **graph},
            )
            assert saved.status_code == 200, saved.text
            started = await client.post(
                f"/api/v1/projects/{project['id']}/audios/{project['audios'][0]['id']}/workflow-runs",
                json={"workflow_revision": saved.json()["workflow_revision"]},
            )
            assert started.status_code == 202, started.text
            await asyncio.gather(*app.state.jobs.tasks.values())
            detail = await client.get(
                f"/api/v1/projects/{project['id']}/audios/{project['audios'][0]['id']}/workflow-runs/{started.json()['id']}"
            )
            assert detail.json()["status"] == "completed"
            records = {item["node_id"]: item for item in detail.json()["nodes"]}
            assert records["test"]["status"] == "completed"
            assert records["out"]["artifact"]["media_type"] == "audio/wav"
    finally:
        NODE_DEFINITIONS.pop("test_passthrough", None)


@pytest.mark.asyncio
async def test_registered_terminal_counts_as_output_and_bad_result_is_rejected(tmp_path):
    from app.domain.node_execution import NodeData, NodeResult
    from app.domain.node_registry import (
        NODE_DEFINITIONS,
        NodeDefinition,
        register_node,
        validate_node_result,
    )

    async def bad_execute(_context):
        return NodeResult()

    bad = NodeDefinition(
        type="test_bad_result",
        label="Bad",
        input_ports=("audio",),
        output_ports=("audio",),
        terminal=False,
        config_kind="none",
        execute=bad_execute,
    )
    try:
        register_node(bad)
        with pytest.raises((TypeError, ValueError)):
            validate_node_result(
                bad, NodeResult(outputs={"audio": NodeData("transcript", object())})
            )
    finally:
        NODE_DEFINITIONS.pop("test_bad_result", None)

    async def terminal_execute(_context):
        return NodeResult()

    terminal = NodeDefinition(
        type="test_terminal",
        label="Terminal",
        input_ports=("audio",),
        output_ports=(),
        terminal=True,
        config_kind="none",
        execute=terminal_execute,
    )
    try:
        register_node(terminal)
        app = create_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF" + b"\0" * 40, "audio/wav")},
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            assert response.status_code == 201, response.text
            project = response.json()["project"]
            graph = {
                "nodes": [
                    {
                        "id": "input",
                        "type": "audio_input",
                        "config": {},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "id": "term",
                        "type": "test_terminal",
                        "config": {},
                        "position": {"x": 1, "y": 0},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "input",
                        "source_port": "audio",
                        "target": "term",
                        "target_port": "audio",
                    },
                ],
            }
            saved = await client.put(
                f"/api/v1/projects/{project['id']}/workflow",
                json={"expected_revision": 0, **graph},
            )
            assert saved.status_code == 200, saved.text
            started = await client.post(
                f"/api/v1/projects/{project['id']}/audios/{project['audios'][0]['id']}/workflow-runs",
                json={"workflow_revision": saved.json()["workflow_revision"]},
            )
            assert started.status_code == 202, started.text
            await asyncio.gather(*app.state.jobs.tasks.values())
            detail = await client.get(
                f"/api/v1/projects/{project['id']}/audios/{project['audios'][0]['id']}/workflow-runs/{started.json()['id']}"
            )
            assert detail.json()["status"] == "completed"
    finally:
        NODE_DEFINITIONS.pop("test_terminal", None)


@pytest.mark.asyncio
async def test_registered_node_is_visible_without_restart_and_registration_is_validated(tmp_path):
    _ensure_runtime_ports()
    from app.domain.node_registry import (
        NODE_DEFINITIONS,
        NodeDefinition,
        register_node,
        validate_node_definition,
    )

    def normalize(config):
        if config:
            raise ValueError("test node config must be empty")
        return {}

    async def execute(_context):
        from app.domain.node_execution import NodeResult

        return NodeResult()

    definition = NodeDefinition(
        type="test_passthrough",
        label="Test",
        input_ports=("audio",),
        output_ports=("audio",),
        terminal=False,
        config_kind="none",
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
    try:
        register_node(definition)
        assert "test_passthrough" in NODE_DEFINITIONS
        app = create_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/capabilities")
        node = next(
            item
            for item in response.json()["workflow"]["nodes"]
            if item["type"] == "test_passthrough"
        )
        assert node["input_ports"] == ["audio"]
        assert node["config"]["kind"] == "none"
        assert "executor" not in node and "validator" not in node
        with pytest.raises(ValueError):
            register_node(definition)
        with pytest.raises(ValueError):
            validate_node_definition(
                NodeDefinition(
                    type="Bad Type",
                    label="Bad",
                    input_ports=(),
                    output_ports=("Bad Port",),
                    terminal=False,
                    config_kind="none",
                )
            )
    finally:
        NODE_DEFINITIONS.pop("test_passthrough", None)
