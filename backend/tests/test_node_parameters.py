"""Parameter inputs preserve literals, graph snapshots and runtime validation."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from app.application.workflow_runner import _execution_node
from app.domain.node_contracts import CapabilityField, NodeDefinition, fields_from_model
from app.domain.node_execution import NodeData, NodeExecutionError, NodeResult
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    PORT_DEFINITIONS,
    TYPE_DEFINITIONS,
    ports_are_compatible,
    register_node,
    register_port,
    validate_node_definition,
    validate_port_value,
)
from app.domain.workflow import (
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRecord,
    WorkflowParameterSource,
    validate_workflow_graph,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ConfigDict, ValidationError

from test_workflow import create_project, get_default_workflow, save_graph


@pytest.fixture
def registry():
    registries = (NODE_DEFINITIONS, PORT_DEFINITIONS, TYPE_DEFINITIONS)
    snapshots = [dict(item) for item in registries]
    yield
    for item, snapshot in zip(registries, snapshots, strict=True):
        item.clear()
        item.update(snapshot)


def edge(name, source, source_port, target, target_port):
    return dict(
        id=name, source=source, source_port=source_port, target=target, target_port=target_port
    )


def parameter_graph():
    return {
        "nodes": [
            dict(id="input", type="audio_input", config={}),
            dict(id="asr", type="asr", config={}),
            dict(
                id="text",
                type="text_output",
                config={"filename_stem": "manual", "format": "txt", "naming_mode": "exact"},
            ),
            dict(id="language", type="string_value", config={"value": "en-US"}),
            dict(id="name", type="string_value", config={"value": "wired"}),
            dict(id="format", type="string_value", config={"value": "srt"}),
            dict(
                id="backup",
                type="audio_output",
                config={"filename_stem": "backup", "naming_mode": "exact"},
            ),
            dict(
                id="asr_context",
                type="text_join",
                config={"items": [{"id": "a1", "value": "ja"}], "separator": ""},
            ),
        ],
        "edges": [
            edge("audio-asr", "input", "audio", "asr", "audio"),
            edge("transcript", "asr", "transcript", "text", "transcript"),
            edge("language-wire", "language", "string", "asr_context", "text_a1"),
            edge("name-wire", "name", "string", "text", "filename_stem"),
            edge("format-wire", "format", "string", "text", "text_format"),
            edge("backup-audio", "input", "audio", "backup", "audio"),
            edge("context", "asr_context", "string", "backup", "filename_stem"),
        ],
    }


def graph_issues(graph):
    return validate_workflow_graph(
        [WorkflowNode.model_validate(item) for item in graph["nodes"]],
        [WorkflowEdge.model_validate(item) for item in graph["edges"]],
    )


async def run_graph(app, client, pid, aid, document):
    root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
    response = await client.post(root, json={"workflow_revision": document["workflow_revision"]})
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.jobs.tasks.values())
    result = (await client.get(root + "/" + response.json()["id"])).json()
    return result, {item["node_id"]: item for item in result["nodes"]}


@pytest.mark.asyncio
async def test_wire_values_drive_outputs_and_disconnect_restores_saved_literals(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = parameter_graph()
        document = await save_graph(client, pid, 0, graph)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed", run
        assert records["asr_context"]["config"]["items"][0]["value"] == "ja"
        assert records["asr_context"]["effective_config"]["items"][0]["value"] == "en-US"
        assert records["asr_context"]["parameter_sources"]["items.a1.value"] == {
            "kind": "connection",
            "edge_id": "language-wire",
            "source_node_id": "language",
            "source_port": "string",
            "target_port": "text_a1",
        }
        assert records["asr_context"]["parameter_sources"]["items.a1.value"]["kind"] == "connection"
        assert records["text"]["artifact"]["filename"] == "wired.srt"
        assert records["backup"]["artifact"]["filename"] == "en-US.wav"
        transcript = (await client.get(records["asr"]["artifact"]["download_url"])).json()
        assert transcript["language"] == "ja"
        assert all(segment["language"] == "ja" for segment in transcript["segments"])
        assert (
            next(item for item in run["workflow"]["nodes"] if item["id"] == "asr_context")[
                "config"
            ]["items"][0]["value"]
            == "ja"
        )
        old_bytes = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}")
        ).content

        # Disconnected roots are removed with their outgoing parameter edges.
        graph["nodes"] = [
            node for node in graph["nodes"] if node["id"] not in {"language", "format", "name"}
        ]
        graph["edges"] = [
            item
            for item in graph["edges"]
            if item["id"] not in {"language-wire", "format-wire", "name-wire"}
        ]
        document = await save_graph(client, pid, document["revision"], graph)
        restored, restored_records = await run_graph(app, client, pid, aid, document)
        assert restored["status"] == "completed"
        assert restored_records["asr_context"]["effective_config"]["items"][0]["value"] == "ja"
        assert restored_records["asr_context"]["parameter_sources"]["items.a1.value"] == {
            "kind": "config"
        }
        assert restored_records["text"]["artifact"]["filename"] == "manual.txt"
        assert (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}")
        ).content == old_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_id,value",
    [("format", "ass"), ("name", "../escape"), ("name", "CON"), ("language", "x" * 65537)],
)
async def test_invalid_wired_candidates_fail_without_fallback_or_artifact(tmp_path, node_id, value):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = parameter_graph()
        next(item for item in graph["nodes"] if item["id"] == node_id)["config"]["value"] = value
        document = await save_graph(client, pid, 0, graph)
        if node_id == "language":
            rejected = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert rejected.status_code == 422
            assert rejected.json()["error"]["code"] == "CONFIGURATION_INVALID"
            assert not app.state.jobs.tasks
            return
        run, records = await run_graph(app, client, pid, aid, document)
        target = "text"
        assert records[target]["status"] == "failed", run
        assert records[target]["error"]["type"] == "INVALID_NODE_CONFIG"
        assert records[target]["artifact"] is None
        key = {"language": "language", "format": "format", "name": "filename_stem"}[node_id]
        assert records[target]["effective_config"][key] == value
        if node_id != "name":
            assert run["status"] == "partial"
            assert records["backup"]["status"] == "completed"


@pytest.mark.parametrize(
    "node_type,bad",
    [
        ("string_value", 1),
        ("integer_value", True),
        ("integer_value", 1.5),
        ("number_value", True),
        ("number_value", float("nan")),
        ("number_value", float("inf")),
        ("boolean_value", 1),
        ("boolean_value", "false"),
    ],
)
def test_primitive_configuration_rejects_coercions_and_nonfinite_values(node_type, bad):
    with pytest.raises(ValueError):
        NODE_DEFINITIONS[node_type].validate_config({"value": bad})


def test_parameter_graph_rejects_bad_types_duplicates_and_invalid_manual_fallback():
    graph = parameter_graph()
    graph["edges"].append(edge("duplicate", "name", "string", "asr_context", "text_a1"))
    assert any(item.code == "INVALID_NODE_INPUT_COUNT" for item in graph_issues(graph))
    graph = parameter_graph()
    graph["nodes"][3] = dict(id="language", type="integer_value", config={"value": 42})
    graph["edges"][2]["source_port"] = "integer"
    assert any(item.code == "INVALID_PORT" for item in graph_issues(graph))
    graph = parameter_graph()
    graph["nodes"][-1]["config"]["items"][0]["value"] = "x" * 65537
    assert any(item.code == "INVALID_NODE_CONFIG" for item in graph_issues(graph))


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
async def test_raw_json_nonfinite_configuration_returns_stable_422(tmp_path, token):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        graph = parameter_graph()
        graph["nodes"][3] = dict(id="language", type="number_value", config={"value": "TOKEN"})
        graph["edges"][2]["source_port"] = "number"
        body = json.dumps({"expected_revision": 0, **graph}).replace('"TOKEN"', token)
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "INVALID_WORKFLOW"
        assert not (app.state.store.project_dir(pid) / "workflow-v3.json").exists()


@pytest.mark.asyncio
async def test_queued_run_freezes_origins_and_values_before_editing_graph(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = parameter_graph()
        document = await save_graph(client, pid, 0, graph)
        root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
        await app.state.jobs.semaphore.acquire()
        try:
            response = await client.post(
                root, json={"workflow_revision": document["workflow_revision"]}
            )
            assert response.status_code == 202
            queued = response.json()
            assert all(item["effective_config"] is None for item in queued["nodes"])
            graph["nodes"][3]["config"]["value"] = "fr"
            graph["edges"][2]["id"] = "new-language-edge"
            updated = await save_graph(client, pid, document["revision"], graph)
            assert updated["workflow_revision"] > document["workflow_revision"]
        finally:
            app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (await client.get(root + "/" + queued["id"])).json()
        record = next(item for item in run["nodes"] if item["node_id"] == "asr_context")
        assert record["effective_config"]["items"][0]["value"] == "en-US"
        assert record["parameter_sources"]["items.a1.value"]["edge_id"] == "language-wire"


@pytest.mark.asyncio
async def test_failed_parameter_root_blocks_consumers_and_keeps_independent_branch(
    tmp_path, registry
):
    async def fail(_context):
        raise NodeExecutionError("PARAMETER_UNAVAILABLE", "参数源不可用")

    NODE_DEFINITIONS["string_value"] = replace(
        NODE_DEFINITIONS["string_value"],
        execute=fail,
        evaluation_kind="processing",
        evaluate_config=None,
    )
    graph = parameter_graph()
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "name"]
    graph["edges"] = [item for item in graph["edges"] if item["id"] != "name-wire"]
    next(item for item in graph["edges"] if item["id"] == "context")["target"] = "text"
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, graph)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "partial"
        assert records["language"]["error"]["type"] == "PARAMETER_UNAVAILABLE"
        assert records["asr"]["status"] == "completed"
        assert records["text"]["status"] == "blocked"
        assert records["asr_context"]["effective_config"] is None
        assert records["backup"]["status"] == "completed"


@pytest.mark.asyncio
async def test_custom_number_and_boolean_parameters_use_generic_scheduler(tmp_path, registry):
    class GainConfig(BaseModel):
        model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)
        scale: float = 1.0
        enabled: bool = False
        offset: float = 0.0

    def normalize(config):
        return GainConfig.model_validate(config, strict=True).model_dump(mode="json")

    for port, type_id in (
        ("gain_value", "number"),
        ("flag_value", "boolean"),
        ("offset_value", "number"),
    ):
        register_port(port, type_id=type_id)
    observed = []

    async def execute(context):
        observed.append(deepcopy(context.node.config))
        result = NodeResult()
        result.add("audio", context.input("audio").value)
        return result

    register_node(
        NodeDefinition(
            type="test_gain",
            label="Gain",
            input_ports=("audio", "gain_value", "flag_value", "offset_value"),
            required_input_ports=("audio",),
            output_ports=("audio",),
            terminal=False,
            config_kind="object",
            fields=fields_from_model(
                GainConfig,
                input_ports={
                    "scale": "gain_value",
                    "enabled": "flag_value",
                    "offset": "offset_value",
                },
            ),
            normalize_config=normalize,
            validate_config=normalize,
            execute=execute,
        )
    )
    graph = {
        "nodes": [
            dict(id="input", type="audio_input", config={}),
            dict(id="gain", type="test_gain", config={}),
            dict(id="integer", type="integer_value", config={"value": 2}),
            dict(id="number", type="number_value", config={"value": 3}),
            dict(id="flag", type="boolean_value", config={"value": True}),
            dict(id="output", type="audio_output", config={}),
        ],
        "edges": [
            edge("e1", "input", "audio", "gain", "audio"),
            edge("e2", "integer", "integer", "gain", "gain_value"),
            edge("e3", "flag", "boolean", "gain", "flag_value"),
            edge("e4", "gain", "audio", "output", "audio"),
            edge("e5", "number", "number", "gain", "offset_value"),
        ],
    }
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, graph)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed", run
        assert observed == [{"scale": 2.0, "enabled": True, "offset": 3.0}]
        assert type(records["gain"]["effective_config"]["scale"]) is float
        assert type(records["number"]["effective_config"]["value"]) is float
        assert records["gain"]["config"] == {"scale": 1.0, "enabled": False, "offset": 0.0}
        assert ports_are_compatible("integer", "gain_value")
        assert not ports_are_compatible("boolean", "gain_value")
        assert not validate_port_value("number", True)
        assert not validate_port_value("number", float("nan"))


@pytest.mark.parametrize(
    "change",
    [
        {"is_source": True},
        {"input_ports": ("string",)},
        {"terminal": True},
        {"presentation": {"resource_input": {"config_key": "value"}}},
    ],
)
def test_parameter_root_registration_rejects_resource_or_input_roles(change):
    with pytest.raises(ValueError):
        validate_node_definition(replace(NODE_DEFINITIONS["string_value"], **change))


@pytest.mark.parametrize(
    "fields,required",
    [
        ((CapabilityField(key="filename_stem", type="string", input_port="audio"),), ("audio",)),
        ((CapabilityField(key="filename_stem", type="string", input_port="missing"),), ("audio",)),
        (
            (CapabilityField(key="filename_stem", type="integer", input_port="filename_stem"),),
            ("audio",),
        ),
        (
            (
                CapabilityField(key="a", type="string", input_port="filename_stem"),
                CapabilityField(key="b", type="string", input_port="filename_stem"),
            ),
            ("audio",),
        ),
        (
            (CapabilityField(key="a", type="string"), CapabilityField(key="a", type="string")),
            ("audio",),
        ),
    ],
)
def test_registration_rejects_invalid_parameter_bindings(fields, required):
    with pytest.raises(ValueError):
        validate_node_definition(
            replace(NODE_DEFINITIONS["audio_output"], fields=fields, required_input_ports=required)
        )


@pytest.mark.parametrize(
    "options",
    [
        [],
        [{"value": 1}],
        [{"value": 1.5}],
        [{"value": True}],
        [{"value": "txt"}, {"value": 1}],
        [{"label": "Missing value"}],
    ],
)
def test_connectable_select_rejects_options_outside_string_protocol(options):
    field = CapabilityField(key="format", type="select", input_port="text_format", options=options)
    with pytest.raises(ValueError, match="non-empty string options"):
        validate_node_definition(replace(NODE_DEFINITIONS["text_output"], fields=(field,)))


def test_structured_parameter_origins_reject_partial_or_mixed_metadata():
    assert WorkflowParameterSource(kind="config").model_dump(mode="json") == {"kind": "config"}
    with pytest.raises(ValidationError):
        WorkflowParameterSource(kind="connection", edge_id="edge")
    with pytest.raises(ValidationError):
        WorkflowParameterSource(kind="config", source_port="string")


@pytest.mark.parametrize("candidate", [float("nan"), float("inf"), object()])
def test_non_json_candidate_fails_without_poisoning_execution_record(candidate):
    node = WorkflowNode(
        id="asr_context",
        type="text_join",
        config={"items": [{"id": "a1", "value": "ja"}], "separator": ""},
    )
    record = WorkflowNodeRecord(node_id=node.id, node_type=node.type, config=node.config)
    with pytest.raises(NodeExecutionError, match="执行配置无效") as error:
        _execution_node(
            node,
            NODE_DEFINITIONS["text_join"],
            {"text_a1": NodeData("text_a1", candidate)},
            record,
        )
    assert error.value.code == "INVALID_NODE_CONFIG"
    assert record.effective_config is None
    assert json.loads(record.model_dump_json())["config"] == {
        "items": [{"id": "a1", "value": "ja"}],
        "separator": "",
    }
    assert node.config == {"items": [{"id": "a1", "value": "ja"}], "separator": ""}


@pytest.mark.asyncio
async def test_current_run_optional_parameter_metadata_reads_without_rewrite(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed"
        download_url = records["output"]["artifact"]["download_url"]
        expected_bytes = (await client.get(download_url)).content
    run_path = app.state.store.workflow_run_dir(pid, aid, run["id"]) / "run.json"
    old_payload = json.loads(run_path.read_text(encoding="utf-8"))
    for item in old_payload["nodes"]:
        del item["parameter_sources"]
        del item["effective_config"]
    run_path.write_text(json.dumps(old_payload, ensure_ascii=False), encoding="utf-8")
    legacy_bytes = run_path.read_bytes()
    restarted = create_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}"
        )
        assert response.status_code == 200, response.text
        assert response.json()["schema_version"] == "3.0"
        assert all(
            item["parameter_sources"] is None and item["effective_config"] is None
            for item in response.json()["nodes"]
        )
        assert (await client.get(download_url)).content == expected_bytes
    assert run_path.read_bytes() == legacy_bytes


@pytest.mark.asyncio
async def test_cancelled_parameter_graph_never_resolves_queued_values(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, parameter_graph())
        root = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs"
        await app.state.jobs.semaphore.acquire()
        try:
            queued = await client.post(
                root, json={"workflow_revision": document["workflow_revision"]}
            )
            assert queued.status_code == 202
            run_url = root + "/" + queued.json()["id"]
            cancelled = await client.post(run_url + "/cancel")
            assert cancelled.status_code == 202
        finally:
            app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (await client.get(run_url)).json()
        assert run["status"] == "cancelled"
        assert all(
            item["status"] == "cancelled" and item["effective_config"] is None
            for item in run["nodes"]
        )
        assert all(item["parameter_sources"] is not None for item in run["nodes"])


def test_only_declared_parameter_roots_are_additional_roots(registry):
    NODE_DEFINITIONS["string_value"] = replace(
        NODE_DEFINITIONS["string_value"], is_parameter_source=False, can_start_without_inputs=False
    )
    assert any(item.code == "UNREACHABLE_NODE" for item in graph_issues(parameter_graph()))
