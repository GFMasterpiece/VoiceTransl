"""Audio export node."""

from ..domain.node_config import AudioOutputConfig
from ..domain.node_constants import AUDIO_OUTPUT_NODE_TYPE, AUDIO_PORT
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeResult


def normalize(config):
    return AudioOutputConfig.model_validate(config, strict=True).model_dump(mode="json")


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.output_naming import output_filename
    from ..application.workflow_media import write_audio_artifact

    config = AudioOutputConfig.model_validate(context.node.config, strict=True)
    audio = context.input(AUDIO_PORT).value
    artifact = await write_audio_artifact(
        context.store,
        context.run,
        context.node.id,
        audio,
        context.cancel_event,
        output_filename(
            config,
            audio.suffix,
            batch_id=getattr(context.run, "batch_id", None),
            batch_item_sequence=getattr(context.run, "batch_item_sequence", None),
        ),
    )
    return NodeResult(artifact=artifact)


def definition() -> NodeDefinition:
    return NodeDefinition(
        type=AUDIO_OUTPUT_NODE_TYPE,
        label="音频输出",
        input_ports=(AUDIO_PORT, "filename_stem"),
        required_input_ports=(AUDIO_PORT,),
        output_ports=(),
        terminal=True,
        config_kind="audio_output",
        fields=fields_from_model(AudioOutputConfig, input_ports={"filename_stem": "filename_stem"}),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
