"""Composed values use the same binding and codecs as real execution."""

import asyncio
from types import SimpleNamespace

import pytest
from app.application.workflow_reuse import execution_config
from app.domain.context_values import (
    ASROptions,
    JSONValue,
    TranslationContext,
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
    first = await evaluate("text_join", {"left": "背景", "right": "角色"})
    fallback = {"left": "不会变", "right": "要求", "separator": " | "}
    second = await evaluate("text_join", fallback, {"text_left": first.outputs["string"]})
    assert second.outputs["string"].value == "背景\n\n角色 | 要求"
    assert fallback["left"] == "不会变"
    with pytest.raises(ValueError):
        await evaluate("text_join", {"left": "a" * 32768, "right": "b" * 32768})


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
async def test_json_to_context_wire_accepts_business_terms_and_replaces_only_effective_config():
    glossary = await evaluate("json_value", {"value": {"password": "密码"}})
    config = {
        "target_language": "en",
        "glossary": {"fallback": "后备"},
        "background": "人物背景",
        "instructions": "保留语气",
    }
    result = await evaluate("translation_context", config, {"glossary": glossary.outputs["json"]})
    packet = result.outputs["translation_context"].value
    assert packet.glossary == {"password": "密码"}
    assert (
        packet.render_instructions()
        == "[Background]\n人物背景\n\n[Translation instructions]\n保留语气"
    )
    assert config["glossary"] == {"fallback": "后备"}
    copy = packet.glossary
    copy["new"] = "新"
    assert packet.glossary == {"password": "密码"}


@pytest.mark.asyncio
async def test_empty_target_is_a_valid_draft_but_cannot_materialize():
    assert NODE_DEFINITIONS["translation_context"].normalize_config({})["target_language"] == ""
    with pytest.raises(NodeExecutionError) as error:
        await evaluate("translation_context")
    assert error.value.code == "LLM_TARGET_LANGUAGE_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "type_id,value",
    [
        ("json_object", JSONValue.from_object({"b": [2], "a": 1})),
        ("translation_context", TranslationContext(target_language="zh")),
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
    with pytest.raises(ValueError):
        TranslationContext(background="a" * 4096, instructions="b" * 4096)
    assert TranslationContext(instructions="a" * 8192).render_instructions() == "a" * 8192


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
        WorkflowNode(id="terms", type="json_value", config={"value": {"password": "密码"}}),
        WorkflowNode(id="join", type="text_join", config={"left": "人物", "right": "世界"}),
        WorkflowNode(id="context", type="translation_context", config={"target_language": "en"}),
    ]
    edges = [
        WorkflowEdge(
            id="termscontext",
            source="terms",
            source_port="json",
            target="context",
            target_port="glossary",
        ),
        WorkflowEdge(
            id="joincontext",
            source="join",
            source_port="string",
            target="context",
            target_port="background",
        ),
    ]
    run = SimpleNamespace(
        workflow=WorkflowSnapshot(revision=0, workflow_revision=0, nodes=nodes, edges=edges)
    )
    result = await evaluate_configuration_graph(
        None, run, None, asyncio.Event(), required_ids={"context"}
    )
    packet = result["context"].outputs["translation_context"].value
    assert packet.glossary == {"password": "密码"}
    assert packet.background == "人物\n\n世界"
    assert nodes[-1].config == {"target_language": "en"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,target,error_code",
    [
        ("openai_chat", "", "LLM_TARGET_LANGUAGE_REQUIRED"),
        ("qwen_filetrans", "en", "MODEL_PROTOCOL_INCOMPATIBLE"),
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
            type="service_connection",
            config={
                "protocol": protocol,
                "endpoint": "https://example.test/v1",
                "credential_ref": "cred_" + "a" * 32,
            },
        ),
        WorkflowNode(id="model", type="model_config", config={"model_name": "model"}),
        WorkflowNode(id="context", type="translation_context", config={"target_language": target}),
        WorkflowNode(id="processor", type="llm_translate"),
    ]
    edges = [
        WorkflowEdge(
            id="sm",
            source="service",
            source_port="connection",
            target="model",
            target_port="connection",
        ),
        WorkflowEdge(
            id="mp", source="model", source_port="model", target="processor", target_port="model"
        ),
        WorkflowEdge(
            id="cp",
            source="context",
            source_port="translation_context",
            target="processor",
            target_port="translation_context",
        ),
    ]
    run = SimpleNamespace(
        project_id="p",
        workflow=WorkflowSnapshot(revision=0, workflow_revision=0, nodes=nodes, edges=edges),
    )
    with pytest.raises(NodeExecutionError) as error:
        await evaluate_configuration_graph(None, run, None, asyncio.Event())
    assert error.value.code == error_code
