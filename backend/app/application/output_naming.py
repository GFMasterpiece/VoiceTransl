"""Pure output naming shared by audio and text export nodes."""

from ..domain.node_config import AudioOutputConfig, valid_filename_stem
from ..domain.node_execution import NodeExecutionError


def output_filename(
    config: AudioOutputConfig,
    suffix: str,
    *,
    batch_id: str | None = None,
    batch_item_sequence: int | None = None,
) -> str:
    valid, message = valid_filename_stem(config.filename_stem)
    if not valid:
        raise NodeExecutionError("INVALID_NODE_CONFIG", message or "输出名称 / 前缀无效")
    stem = config.filename_stem
    if config.naming_mode == "prefix_sequence":
        sequence = 1 if batch_id is None else batch_item_sequence
        if type(sequence) is not int or sequence < 1:
            raise NodeExecutionError(
                "OUTPUT_SEQUENCE_UNAVAILABLE", "批次缺少有效的固定文件序号，无法生成输出名称"
            )
        # Validate the user prefix before suffixing. Its existing 128-character
        # limit does not include the generated separator and sequence.
        stem = f"{stem}_{sequence:05d}"
    return f"{stem}{suffix}"
