"""Composed values use the same binding and codecs as real execution."""

import asyncio
from types import SimpleNamespace

import pytest
from app.application.workflow_reuse import execution_config
from app.domain.context_values import (
    ASROptions,
    JSONValue,
    TranslationOptions,
)
from app.domain.node_execution import NodeData, NodeExecutionContext, NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS, TYPE_DEFINITIONS
from app.domain.workflow import WorkflowNode
from pydantic import ValidationError


async def evaluate(node_type, config=None, inputs=None):
    definition = NODE_DEFINITIONS[node_type]
    node = WorkflowNode(id="value", type=node_type, config=config or {})
    inputs = inputs or {}
    effective = node.model_copy(update={"config": execution_config(node, inputs)})
    context = NodeExecutionContext(None, None, effective, inputs, None, asyncio.Event())
    return await definition.evaluate_config(context)


@pytest.mark.asyncio
async def test_text_chain_has_explicit_order_and_preserves_fallback():
    first = await evaluate(
        "text_join", {"items": [{"id": "a1", "value": "背景"}, {"id": "b2", "value": "角色"}]}
    )
    fallback = {
        "items": [{"id": "a1", "value": "不会变"}, {"id": "b2", "value": "要求"}],
        "separator": " | ",
    }
    second = await evaluate("text_join", fallback, {"text_a1": first.outputs["string"]})
    assert second.outputs["string"].value == "背景\n\n角色 | 要求"
    assert fallback["items"][0]["value"] == "不会变"
    with pytest.raises(ValueError):
        await evaluate(
            "text_join",
            {"items": [{"id": "a1", "value": "a" * 32768}, {"id": "a2", "value": "b" * 32768}]},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "expected"),
    [("left_wins", {"nested": {"left": 1}}), ("right_wins", {"nested": {"right": 2}})],
)
async def test_json_merge_is_top_level_and_projects_fresh_dict(policy, expected):
    left = JSONValue.from_object({"nested": {"left": 1}})
    right = JSONValue.from_object({"nested": {"right": 2}})
    result = await evaluate(
        "json_merge",
        {"conflict_policy": policy},
        {"json_left": NodeData("json_left", left), "json_right": NodeData("json_right", right)},
    )
    assert result.outputs["json"].value.value == expected
    assert left.value == {"nested": {"left": 1}}
    with pytest.raises(NodeExecutionError, match="重复键"):
        await evaluate(
            "json_merge",
            {},
            {"json_left": NodeData("json_left", left), "json_right": NodeData("json_right", right)},
        )


@pytest.mark.asyncio
async def test_plain_context_has_no_dedicated_node_and_requires_nonempty_string():
    assert "translation_context" not in NODE_DEFINITIONS
    assert "asr_context" not in NODE_DEFINITIONS
    assert "translation_context" not in TYPE_DEFINITIONS
    assert "asr_context" not in TYPE_DEFINITIONS
    result = await evaluate(
        "text_join",
        {
            "items": [
                {"id": "x", "value": "English"},
                {"id": "y", "value": "terms"},
                {"id": "z", "value": ""},
            ],
            "separator": "|",
        },
    )
    assert result.outputs["string"].value == "English|terms|"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "type_id,value",
    [
        ("json_object", JSONValue.from_object({"b": [2], "a": 1})),
        ("translation_options", TranslationOptions(timeout_seconds=9)),
        ("asr_options", ASROptions(timeout_seconds=7)),
    ],
)
async def test_configuration_codecs_roundtrip_and_reject_bad_versions(type_id, value):
    codec = TYPE_DEFINITIONS[type_id].checkpoint_codec
    payload = await codec.encode(value, SimpleNamespace())
    assert await codec.decode(payload, SimpleNamespace()) == value
    payload["version"] = "999"
    with pytest.raises(ValueError):
        await codec.decode(payload, SimpleNamespace())
    with pytest.raises(ValidationError):
        value.version = "2"


@pytest.mark.parametrize(
    "value", [[], {"bad": float("inf")}, {"big": "x" * 16384}, {"a": [[[[[[[[1]]]]]]]]}]
)
def test_json_rejects_invalid_size_and_depth(value):
    with pytest.raises(ValueError):
        JSONValue.from_object(value)


def test_json_encoded_duplicate_keys_and_combined_text_limits():
    with pytest.raises(ValueError):
        JSONValue(value_json='{"a":1,"a":2}')
    from app.domain.context_values import validate_text

    with pytest.raises(ValueError):
        validate_text("x" * 65537)
    assert validate_text("x" * 65536) == "x" * 65536


@pytest.mark.asyncio
async def test_integer_options_bind_without_mutating_fallback():
    config = {"timeout_seconds": 120, "batch_size": 20}
    result = await evaluate(
        "translation_options", config, {"batch_size": NodeData("batch_size", 3)}
    )
    assert result.outputs["translation_options"].value.batch_size == 3
    assert config["batch_size"] == 20


@pytest.mark.asyncio
async def test_actual_configuration_graph_materializes_json_and_text_ancestors():
    from app.application.configuration_evaluator import evaluate_configuration_graph
    from app.domain.workflow import WorkflowEdge, WorkflowSnapshot

    nodes = [
        WorkflowNode(id="terms", type="string_value", config={"value": "terms"}),
        WorkflowNode(
            id="join",
            type="text_join",
            config={"items": [{"id": "a1", "value": "fallback"}, {"id": "a2", "value": "world"}]},
        ),
        WorkflowNode(
            id="context",
            type="text_join",
            config={"items": [{"id": "x", "value": "English"}, {"id": "y", "value": ""}]},
        ),
    ]
    edges = [
        WorkflowEdge(
            id="termsjoin",
            source="terms",
            source_port="string",
            target="join",
            target_port="text_a1",
        ),
        WorkflowEdge(
            id="joincontext",
            source="join",
            source_port="string",
            target="context",
            target_port="text_y",
        ),
    ]
    run = SimpleNamespace(
        workflow=WorkflowSnapshot(revision=0, workflow_revision=0, nodes=nodes, edges=edges)
    )
    result = await evaluate_configuration_graph(
        None, run, None, asyncio.Event(), required_ids={"context"}
    )
    assert result["context"].outputs["string"].value == "English\n\nterms\n\nworld"
    assert nodes[1].config["items"][0]["value"] == "fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,target,error_code",
    [
        ("openai_chat", "", "LLM_CONTEXT_INVALID"),
        ("qwen_filetrans", "en", "CONNECTION_BINDING_INVALID"),
    ],
)
async def test_configuration_preflight_rejects_target_and_protocol_without_processing(
    protocol, target, error_code
):
    from app.application.configuration_evaluator import evaluate_configuration_graph
    from app.domain.workflow import WorkflowEdge, WorkflowSnapshot

    nodes = [
        WorkflowNode(
            id="service",
            type="llm_service" if protocol == "openai_chat" else "asr_service",
            config={
                **({"protocol": protocol} if protocol != "openai_chat" else {}),
                "endpoint": "https://example.test/v1",
                "credential_ref": "cred_" + "a" * 32,
            },
        ),
        WorkflowNode(id="model", type="llm_model", config={"model_name": "model"}),
        WorkflowNode(id="context", type="string_value", config={"value": target}),
        WorkflowNode(id="processor", type="llm_translate"),
    ]
    edges = [
        WorkflowEdge(
            id="sm",
            source="service",
            source_port="llm_connection" if protocol == "openai_chat" else "asr_connection",
            target="model",
            target_port="llm_connection",
        ),
        WorkflowEdge(
            id="mp",
            source="model",
            source_port="llm_model",
            target="processor",
            target_port="llm_model",
        ),
        WorkflowEdge(
            id="cp",
            source="context",
            source_port="string",
            target="processor",
            target_port="context",
        ),
    ]
    run = SimpleNamespace(
        project_id="p",
        workflow=WorkflowSnapshot(revision=0, workflow_revision=0, nodes=nodes, edges=edges),
    )
    with pytest.raises(NodeExecutionError) as error:
        await evaluate_configuration_graph(None, run, None, asyncio.Event())
    assert error.value.code == error_code
