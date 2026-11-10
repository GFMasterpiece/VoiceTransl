"""Map plain recognition context to the documented Qwen message format."""

from ..domain.node_execution import NodeExecutionError

MAX_CONTEXT_CHARACTERS = 400


def context_messages(value=None):
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        raise NodeExecutionError("QWEN_CONTEXT_INVALID", "Qwen上下文必须为普通文本")
    if len(value) > MAX_CONTEXT_CHARACTERS:
        raise NodeExecutionError("QWEN_CONTEXT_TOO_LONG", "Qwen上下文不能超过400个字符")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise NodeExecutionError(
            "QWEN_CONTEXT_INVALID", "Qwen上下文必须为有效Unicode文本"
        ) from None
    return [{"role": "user", "content": [{"type": "input_text", "text": value}]}]


def input_context(context):
    value = context.input("context").value if "context" in context.inputs else None
    context_messages(value)
    return value
