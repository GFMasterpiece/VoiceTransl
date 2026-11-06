"""Independent preview nodes with persistent per-run artifacts."""

from ..domain.node_config import normalize_empty_config
from ..domain.node_constants import AUDIO_PORT, TRANSCRIPT_PORT
from ..domain.node_contracts import NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeResult


async def audio_execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_preview import execute_audio_preview

    return await execute_audio_preview(context)


async def text_execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_preview import execute_text_preview

    return await execute_text_preview(context)


def definitions() -> tuple[NodeDefinition, ...]:
    return tuple(
        NodeDefinition(
            type=node_type,
            label=label,
            input_ports=(port,),
            output_ports=(),
            terminal=True,
            config_kind="none",
            normalize_config=normalize_empty_config,
            validate_config=normalize_empty_config,
            presentation={"preview": {"renderer": renderer}},
            execute=execute,
        )
        for node_type, label, port, renderer, execute in (
            ("audio_preview", "音频预览", AUDIO_PORT, "audio", audio_execute),
            ("text_preview", "文本预览", TRANSCRIPT_PORT, "text", text_execute),
        )
    )
