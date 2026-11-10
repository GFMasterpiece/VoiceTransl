"""ASR has a dedicated model family and no text-context input."""

import inspect

import pytest
from app.adapters import qwen_asr, qwen_asr_streaming, qwen_protocol, qwen_streaming_protocol
from app.domain.node_registry import ports_are_compatible
from app.nodes import asr
from app.nodes import qwen_asr as file_node
from app.nodes import qwen_asr_streaming as stream_node


@pytest.mark.parametrize("node", [file_node, stream_node])
def test_asr_nodes_have_only_audio_model_options(node):
    definition = node.definition()
    assert definition.input_ports == ("audio", "asr_model", "asr_options")
    assert definition.required_ports == ("audio", "asr_model")
    assert "context" not in definition.validation_input_ports
    assert definition.fields == ()
    assert definition.normalize_config({}) == {}
    with pytest.raises(ValueError):
        definition.normalize_config({"context": "legacy context"})


def test_mock_has_only_audio():
    assert asr.definition().input_ports == ("audio",)


@pytest.mark.parametrize(
    "adapter", [qwen_asr.transcribe, qwen_asr_streaming.QwenStreamingASRAdapter().transcribe]
)
@pytest.mark.asyncio
async def test_asr_adapters_reject_old_context_keyword(adapter):
    assert "context" not in inspect.signature(adapter).parameters
    with pytest.raises(TypeError, match="context"):
        await adapter(context="legacy context")


@pytest.mark.parametrize("protocol", [qwen_protocol, qwen_streaming_protocol])
@pytest.mark.parametrize("key", ["context", "Context", "con-text", "input"])
def test_context_cannot_be_reintroduced_through_additional_parameters(protocol, key):
    with pytest.raises(ValueError):
        protocol.validate_additional_parameters({key: [{"role": "user"}]})


@pytest.mark.parametrize(
    "source,target",
    [
        ("llm_model", "asr_model"),
        ("asr_model", "llm_model"),
        ("llm_connection", "asr_connection"),
        ("asr_connection", "llm_connection"),
        ("string", "asr_model"),
    ],
)
def test_asr_llm_families_cannot_cross_connect(source, target):
    assert not ports_are_compatible(source, target)


@pytest.mark.parametrize("node_type", ["qwen_asr", "qwen_asr_streaming"])
def test_old_context_edge_is_rejected_at_graph_validation(node_type):
    from app.domain.workflow import WorkflowEdge, WorkflowNode, validate_workflow_graph

    nodes = [
        WorkflowNode(id="input", type="audio_input", config={}),
        WorkflowNode(id="cloud", type=node_type, config={}),
        WorkflowNode(id="old_context", type="string_value", config={"value": "old text"}),
    ]
    edges = [
        WorkflowEdge(
            id="old_context_edge",
            source="old_context",
            source_port="string",
            target="cloud",
            target_port="context",
        )
    ]
    issues = validate_workflow_graph(nodes, edges)
    assert any(
        issue.code == "INVALID_PORT" and issue.edge_id == "old_context_edge" for issue in issues
    )
