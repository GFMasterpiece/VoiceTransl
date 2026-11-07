"""Read-only parameter outputs for the audio bound to the current run."""

from pathlib import PurePosixPath

from ..domain.node_categories import INPUT
from ..domain.node_config import normalize_empty_config
from ..domain.node_contracts import NodeDefinition
from ..domain.node_execution import NodeExecutionContext, NodeResult

PORT_LABELS = {
    "filename": "文件名",
    "stem": "不含扩展名的文件名",
    "source_language": "源语言",
    "relative_path": "相对路径",
    "root_name": "来源目录名",
}


async def execute(context: NodeExecutionContext) -> NodeResult:
    audio = next((item for item in context.project.audios if item.id == context.run.audio_id), None)
    if audio is None:
        raise FileNotFoundError(context.run.audio_id)
    source = audio.source
    result = NodeResult()
    result.add("filename", source.original_name)
    result.add("stem", PurePosixPath(source.original_name).stem)
    result.add("source_language", source.language)
    result.add(
        "relative_path", source.origin.relative_path if source.origin else source.original_name
    )
    result.add("root_name", source.origin.root_name if source.origin else "")
    return result


def definition() -> NodeDefinition:
    return NodeDefinition(
        type="source_info",
        label="当前文件信息",
        category=INPUT,
        input_ports=(),
        output_ports=tuple(PORT_LABELS),
        terminal=False,
        config_kind="none",
        required_input_ports=(),
        is_parameter_source=True,
        normalize_config=normalize_empty_config,
        validate_config=normalize_empty_config,
        execute=execute,
    )
