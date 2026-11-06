"""Project audio source node."""

from ..domain.node_config import normalize_input_config
from ..domain.node_constants import AUDIO_PORT, INPUT_NODE_TYPE
from ..domain.node_contracts import CapabilityField, NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeResult


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_media import _source_audio

    audio = next((item for item in context.project.audios if item.id == context.run.audio_id), None)
    if audio is None:
        raise FileNotFoundError(context.run.audio_id)
    result = NodeResult()
    result.add(AUDIO_PORT, _source_audio(context.store, context.run, audio))
    return result


def definition() -> NodeDefinition:
    return NodeDefinition(
        type=INPUT_NODE_TYPE,
        label="音频输入",
        input_ports=(),
        output_ports=(AUDIO_PORT,),
        terminal=False,
        config_kind="none",
        normalize_config=normalize_input_config,
        validate_config=normalize_input_config,
        fields=(CapabilityField(key="audio_id", type="string"),),
        presentation={"resource_input": {"resource_type": "audio", "config_key": "audio_id"}},
        execute=execute,
        max_instances=1,
        required_inbound=0,
        is_source=True,
        removable=False,
    )
