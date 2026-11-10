from types import SimpleNamespace

import pytest
from app.adapters.qwen_context import context_messages
from app.domain.node_execution import NodeExecutionError
from app.nodes import qwen_asr, qwen_asr_streaming


@pytest.mark.parametrize("node", [qwen_asr, qwen_asr_streaming])
def test_nodes_preflight_context_limit_without_audio_or_model(node):
    context = SimpleNamespace(
        node=SimpleNamespace(config={}),
        inputs={"context": object()},
        input=lambda port: SimpleNamespace(value="😀" * 401),
    )
    assert "context" in node.definition().validation_input_ports
    with pytest.raises(NodeExecutionError) as error:
        node.validate_inputs(context)
    assert error.value.code == "QWEN_CONTEXT_TOO_LONG"


@pytest.mark.parametrize("value", [12, {}, [], "\ud800"])
def test_invalid_context_is_not_coerced(value):
    with pytest.raises(NodeExecutionError):
        context_messages(value)
