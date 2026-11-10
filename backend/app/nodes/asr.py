"""Mock recognition using the current audio's declared language."""

from ..domain.context_values import (
    normalize_processor_config,
    resolve_audio_language,
)
from ..domain.node_categories import SPEECH_RECOGNITION
from ..domain.node_constants import ASR_NODE_TYPE, AUDIO_PORT, TRANSCRIPT_PORT
from ..domain.node_contracts import NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeResult

normalize = normalize_processor_config


def validate_inputs(context):
    normalize(context.node.config)


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_asr import execute_asr_node

    validate_inputs(context)
    language = resolve_audio_language(context)
    execution_node = context.node.model_copy(
        update={"config": {"provider": "mock", "language": language}}, deep=True
    )
    artifact, transcript = await execute_asr_node(
        context.store,
        context.run,
        execution_node,
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
        input_ports=(AUDIO_PORT,),
        required_input_ports=(AUDIO_PORT,),
        output_ports=(TRANSCRIPT_PORT,),
        terminal=False,
        config_kind="none",
        debug_only=True,
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        validate_inputs=validate_inputs,
        execution_version="3",
    )
