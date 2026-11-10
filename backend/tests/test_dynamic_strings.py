"""Repeatable string inputs are registry contracts, independent of built-in types."""

from copy import deepcopy
from dataclasses import replace

import pytest
from app.application.workflow_reuse import execution_config
from app.domain.node_execution import NodeData
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    PORT_DEFINITIONS,
    resolve_port,
    resolved_fields,
    validate_dynamic_config,
    validate_node_definition,
)
from app.domain.workflow import WorkflowNode, validate_workflow_graph
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import setup
from test_inline_credentials import cloud as cloud  # noqa: F401
from test_inline_credentials import run, save, update_body
from test_workflow_reuse import graph, plan, rerun_body, submit


@pytest.mark.parametrize("prefix", ["slot", "x" * 32 + "_", "a.", "_"])
def test_invalid_dynamic_prefixes_are_rejected(prefix):
    original = NODE_DEFINITIONS["text_join"]
    with pytest.raises(ValueError):
        validate_node_definition(
            replace(original, dynamic_inputs={**original.dynamic_inputs, "port_prefix": prefix})
        )


def test_dynamic_items_validate_shape_values_and_do_not_crash_graph():
    original = NODE_DEFINITIONS["text_join"]
    for items in (
        None,
        1,
        [],
        [{"id": "a", "value": ""}, {"id": "a", "value": ""}],
        [{"id": "bad.id", "value": ""}],
        [{"id": "a", "value": 1}],
    ):
        config = {"items": items}
        with pytest.raises(ValueError):
            validate_dynamic_config(original, config)
        issues = validate_workflow_graph(
            [WorkflowNode(id="value", type="text_join", config=config)], []
        )
        assert any(issue.code == "INVALID_NODE_CONFIG" for issue in issues)
    limited = replace(
        original,
        dynamic_inputs={
            **original.dynamic_inputs,
            "field": {**original.dynamic_inputs["field"], "max_length": 1},
        },
    )
    with pytest.raises(ValueError):
        validate_dynamic_config(limited, {"items": [{"id": "a", "value": "long"}]})
    with pytest.raises(ValueError, match="overlap"):
        validate_node_definition(replace(original, input_ports=("text_fixed",)))


@pytest.mark.asyncio
async def test_custom_dynamic_node_projects_stable_ids_and_reuses_checkpoints(
    tmp_path, monkeypatch, cloud
):
    original = NODE_DEFINITIONS["text_join"]
    custom = replace(
        original,
        type="ordered_strings",
        dynamic_inputs={**deepcopy(original.dynamic_inputs), "port_prefix": "slot_"},
    )
    monkeypatch.setitem(NODE_DEFINITIONS, custom.type, custom)
    validate_node_definition(custom)
    node = WorkflowNode(
        id="custom",
        type=custom.type,
        config={
            "items": [{"id": "a1", "value": "fallback"}, {"id": "b2", "value": "tail"}],
            "separator": "|",
        },
    )
    # A globally registered port with this ID must not override the node instance.
    monkeypatch.setitem(PORT_DEFINITIONS, "slot_a1", PORT_DEFINITIONS["integer"])
    assert resolve_port("slot_a1", node).type_id == "string"
    config = execution_config(node, {"slot_a1": NodeData("slot_a1", "wired", "string")})
    assert config["items"][0]["value"] == "wired"
    assert node.config["items"][0]["value"] == "fallback"
    assert resolved_fields(custom, config)[1].input_port == "slot_a1"
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        context = next(item for item in body["nodes"] if item["id"] == "cloud_context")
        context.update(type=custom.type, config=node.config)
        body["nodes"].append({"id": "words", "type": "string_value", "config": {"value": "wired"}})
        body["edges"].append(
            {
                "id": "words_context",
                "source": "words",
                "source_port": "string",
                "target": "cloud_context",
                "target_port": "slot_a1",
            }
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        source = await run(client, app, pid, aids[0], document)
        assert source.status.value == "completed"
        record = next(item for item in source.nodes if item.node_id == "cloud_context")
        assert record.effective_config["items"][0]["value"] == "wired"
        assert record.checkpoint is not None
        preview = await plan(client, pid, aids[0], rerun_body(document, source))
        assert preview["valid"]
        result = await submit(client, app, pid, aids[0], rerun_body(document, source))
        assert result.status.value == "completed"
        assert len(cloud["calls"]) == 1
