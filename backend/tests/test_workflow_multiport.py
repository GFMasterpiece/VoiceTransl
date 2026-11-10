"""End-to-end contracts for extensible fan-out and join nodes."""

import asyncio
from dataclasses import replace

import pytest
from app.domain.node_contracts import NodeDefinition
from app.domain.node_execution import NodeExecutionCancelled, NodeExecutionError, NodeResult
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    PORT_DEFINITIONS,
    TYPE_DEFINITIONS,
    register_node,
    register_port,
    validate_node_definition,
    validate_node_result,
)
from app.domain.workflow import WorkflowEdge, WorkflowNode, validate_workflow_graph
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_workflow import create_project, save_graph


@pytest.fixture
def extension_registry():
    registries = (NODE_DEFINITIONS, PORT_DEFINITIONS, TYPE_DEFINITIONS)
    snapshots = [dict(registry) for registry in registries]
    calls = []
    for port in ("dry_audio", "wet_audio", "voice", "background_audio", "reference", "mixed_audio"):
        register_port(port, type_id="audio", label=port)

    async def split(context):
        calls.append(("split", tuple(context.inputs)))
        value = context.input("audio").value
        result = NodeResult()
        result.add("dry_audio", replace(value, media_type="audio/dry"))
        result.add("wet_audio", replace(value, media_type="audio/wet"))
        return result

    async def join(context):
        calls.append(("join", tuple(context.inputs)))
        assert context.input("voice").port == "voice"
        assert context.input("background_audio").port == "background_audio"
        assert context.input("voice").value.media_type == "audio/dry"
        assert context.input("background_audio").value.media_type == "audio/wet"
        result = NodeResult()
        result.add("mixed_audio", replace(context.input("voice").value, media_type="audio/wav"))
        return result

    register_node(
        NodeDefinition(
            type="test_split",
            label="Split",
            input_ports=("audio",),
            output_ports=("dry_audio", "wet_audio"),
            terminal=False,
            config_kind="none",
            execute=split,
        )
    )
    register_node(
        NodeDefinition(
            type="test_join",
            label="Join",
            input_ports=("voice", "background_audio", "reference"),
            output_ports=("mixed_audio",),
            required_input_ports=("voice", "background_audio"),
            terminal=False,
            config_kind="none",
            execute=join,
        )
    )
    try:
        yield calls
    finally:
        for registry, snapshot in zip(registries, snapshots, strict=True):
            registry.clear()
            registry.update(snapshot)


def graph(*, backup=False):
    nodes = [
        dict(id="input", type="audio_input", config={}),
        dict(id="split", type="test_split", config={}),
        dict(id="join", type="test_join", config={}),
        dict(id="output", type="audio_output", config={"filename_stem": "joined"}),
    ]
    edges = [
        dict(id="e1", source="input", source_port="audio", target="split", target_port="audio"),
        dict(id="e2", source="split", source_port="dry_audio", target="join", target_port="voice"),
        dict(
            id="e3",
            source="split",
            source_port="wet_audio",
            target="join",
            target_port="background_audio",
        ),
        dict(
            id="e4", source="join", source_port="mixed_audio", target="output", target_port="audio"
        ),
    ]
    if backup:
        nodes.append(dict(id="backup", type="audio_output", config={"filename_stem": "original"}))
        edges.append(
            dict(
                id="backup-edge",
                source="input",
                source_port="audio",
                target="backup",
                target_port="audio",
            )
        )
    return dict(nodes=nodes, edges=edges)


def validate(body):
    return validate_workflow_graph(
        [WorkflowNode.model_validate(node) for node in body["nodes"]],
        [WorkflowEdge.model_validate(edge) for edge in body["edges"]],
    )


@pytest.mark.asyncio
async def test_named_ports_fan_out_join_export_and_keep_history_readable(
    tmp_path, extension_registry
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, graph())
        root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
        started = await client.post(root, json={"workflow_revision": document["workflow_revision"]})
        assert started.status_code == 202, started.text
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (await client.get(root + "/" + started.json()["id"])).json()
        assert run["status"] == "completed", run
        assert all(node["status"] == "completed" for node in run["nodes"])
        assert extension_registry == [
            ("split", ("audio",)),
            ("join", ("voice", "background_audio")),
        ]
        output = next(node for node in run["nodes"] if node["node_id"] == "output")
        assert output["artifact"]["filename"] == "joined_00001.wav"
        source = (
            app.state.store.scope(pid, aid)
            .source_path(pid, app.state.store.audio(pid, aid).source.path)
            .read_bytes()
        )
        assert (await client.get(output["artifact"]["download_url"])).content == source
        assert run["schema_version"] == run["workflow"]["schema_version"] == "3.0"
        old_run = (await client.get(root + "/" + run["id"])).content
        body = graph()
        body["nodes"][1]["position"] = {"x": 100, "y": -120}
        saved = await save_graph(client, pid, document["revision"], body)
        assert saved["workflow_revision"] == document["workflow_revision"]
        assert (await client.get(root + "/" + run["id"])).content == old_run
        caps = (await client.get("/api/v1/capabilities")).json()
        join = next(node for node in caps["workflow"]["nodes"] if node["type"] == "test_join")
        assert join["input_ports"] == ["voice", "background_audio", "reference"]
        assert join["required_input_ports"] == ["voice", "background_audio"]
        assert join["required_inbound"] == 2
        NODE_DEFINITIONS.pop("test_join")
        assert (await client.get(root + "/" + run["id"])).content == old_run
        assert (await client.get(output["artifact"]["download_url"])).content == source


def test_join_validates_missing_and_duplicate_connections_per_input_port(extension_registry):
    assert validate(graph()) == []
    missing = graph()
    missing["edges"] = [
        edge for edge in missing["edges"] if edge["target_port"] != "background_audio"
    ]
    assert any(
        issue.code == "INVALID_NODE_INPUT_COUNT" and "background_audio" in issue.message
        for issue in validate(missing)
    )
    duplicate = graph()
    duplicate["edges"].append(
        dict(
            id="duplicate", source="input", source_port="audio", target="join", target_port="voice"
        )
    )
    assert any(
        issue.code == "INVALID_NODE_INPUT_COUNT" and "voice" in issue.message
        for issue in validate(duplicate)
    )
    optional = graph()
    optional["edges"].append(
        dict(
            id="optional",
            source="input",
            source_port="audio",
            target="join",
            target_port="reference",
        )
    )
    assert validate(optional) == []


@pytest.mark.parametrize("ports", [("missing",), ("voice", "voice")])
def test_node_registration_rejects_invalid_named_required_inputs(extension_registry, ports):
    with pytest.raises(ValueError, match="required input ports"):
        validate_node_definition(replace(NODE_DEFINITIONS["test_join"], required_input_ports=ports))


@pytest.mark.asyncio
async def test_failed_multiport_producer_blocks_join_but_preserves_independent_branch(
    tmp_path, extension_registry
):
    async def incomplete(context):
        result = NodeResult()
        result.add("dry_audio", context.input("audio").value)
        return result

    NODE_DEFINITIONS["test_split"] = replace(NODE_DEFINITIONS["test_split"], execute=incomplete)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, graph(backup=True))
        root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
        start = await client.post(root, json={"workflow_revision": document["workflow_revision"]})
        assert start.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (await client.get(root + "/" + start.json()["id"])).json()
        assert run["status"] == "partial"
        records = {node["node_id"]: node for node in run["nodes"]}
        assert records["split"]["status"] == "failed"
        assert records["split"]["error"]["type"] == "NODE_EXECUTION_FAILED"
        assert records["join"]["status"] == records["output"]["status"] == "blocked"
        assert records["backup"]["status"] == "completed"
        assert extension_registry == []


def test_multiport_results_check_every_declared_value(extension_registry):
    invalid = NodeResult()
    invalid.add("dry_audio", "invalid audio")
    invalid.add("wet_audio", "invalid audio")
    with pytest.raises(TypeError, match="invalid runtime type"):
        validate_node_result(NODE_DEFINITIONS["test_split"], invalid)


def test_edge_port_ids_accept_the_same_length_as_registered_ports(extension_registry):
    port = "audio_" + "x" * 58
    register_port(port, type_id="audio")
    edge = WorkflowEdge(id="long-port", source="split", source_port=port, target="output")
    assert edge.source_port == port
    with pytest.raises(ValueError):
        register_port(port + "x", type_id="audio")
    with pytest.raises(ValueError):
        WorkflowEdge(id="invalid-port", source="split", source_port=port + "x", target="output")


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_extensions_report_common_errors_and_cancellation(
    tmp_path, extension_registry, cancelled
):
    async def fail(context):
        if cancelled:
            raise NodeExecutionCancelled
        raise NodeExecutionError("EXTENSION_FAILED", "Extension request failed")

    NODE_DEFINITIONS["test_split"] = replace(NODE_DEFINITIONS["test_split"], execute=fail)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, graph())
        root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
        start = await client.post(root, json={"workflow_revision": document["workflow_revision"]})
        assert start.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (await client.get(root + "/" + start.json()["id"])).json()
        assert run["status"] == ("cancelled" if cancelled else "failed")
        records = {node["node_id"]: node for node in run["nodes"]}
        assert records["join"]["status"] == ("cancelled" if cancelled else "blocked")
        if not cancelled:
            assert records["split"]["error"] == {
                "type": "EXTENSION_FAILED",
                "message": "Extension request failed",
            }


@pytest.mark.asyncio
async def test_registered_source_uses_its_executor_without_scheduler_type_dispatch(
    tmp_path, extension_registry
):
    original = NODE_DEFINITIONS["audio_input"]
    calls = []

    async def source(context):
        calls.append(context.node.type)
        return await original.execute(context)

    register_node(replace(original, type="test_source", execute=source))
    body = graph()
    body["nodes"][0]["type"] = "test_source"
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, body)
        start = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert start.status_code == 202, start.text
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (
            await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{start.json()['id']}"
            )
        ).json()
        assert run["status"] == "completed"
        assert calls == ["test_source"]
