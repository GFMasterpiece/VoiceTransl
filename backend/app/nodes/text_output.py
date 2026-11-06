"""Transcript export node."""

from ..domain.node_config import TextOutputConfig, validate_text_output_config
from ..domain.node_constants import TEXT_OUTPUT_NODE_TYPE, TRANSCRIPT_PORT
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeResult


def normalize(config):
    return validate_text_output_config(config).model_dump(mode="json")


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_asr import execute_text_output_node

    artifact = await execute_text_output_node(
        context.store,
        context.run,
        context.node,
        context.input(TRANSCRIPT_PORT).value,
        context.cancel_event,
    )
    return NodeResult(artifact=artifact)


def definition() -> NodeDefinition:
    return NodeDefinition(
        type=TEXT_OUTPUT_NODE_TYPE,
        label="文本输出",
        input_ports=(TRANSCRIPT_PORT, "filename_stem", "text_format"),
        required_input_ports=(TRANSCRIPT_PORT,),
        output_ports=(),
        terminal=True,
        config_kind="text_output",
        fields=fields_from_model(
            TextOutputConfig,
            input_ports={"filename_stem": "filename_stem", "format": "text_format"},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
