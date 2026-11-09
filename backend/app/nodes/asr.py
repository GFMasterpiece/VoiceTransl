"""Mock speech recognition node."""

from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_config import MockASRNodeConfig, validate_asr_config
from ..domain.node_constants import ASR_NODE_TYPE, AUDIO_PORT, TRANSCRIPT_PORT
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeResult


def normalize(config):
    return validate_asr_config(config).model_dump(mode="json")


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_asr import execute_asr_node

    artifact, transcript = await execute_asr_node(
        context.store,
        context.run,
        context.node,
        context.input(AUDIO_PORT).value.path,
        context.project,
        context.cancel_event,
        context.mock_delay_ms,
    )
    result = NodeResult(artifact=artifact)
    result.add(TRANSCRIPT_PORT, transcript)
    return result


def definition() -> NodeDefinition:
    return NodeDefinition(
        type=ASR_NODE_TYPE,
        label="语音识别（Mock）",
        category=SPEECH_RECOGNITION,
        input_ports=(AUDIO_PORT, "language"),
        required_input_ports=(AUDIO_PORT,),
        output_ports=(TRANSCRIPT_PORT,),
        terminal=False,
        config_kind="asr",
        settings_default_fields=("language",),
        fields=fields_from_model(
            MockASRNodeConfig,
            widgets={"provider": ("select", [{"value": "mock", "label": "Mock"}])},
            input_ports={"language": "language"},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
