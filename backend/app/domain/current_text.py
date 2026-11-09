"""Explicit text views, independent of the node that produced a transcript."""

from .node_execution import NodeExecutionError

CURRENT_TEXT_KEY = "current_text"
TEXT_FIELDS = frozenset({"source_text", "normalized_text", "translated_text"})


def read_current_text(transcript):
    from .workflow import validate_workflow_transcript

    validate_workflow_transcript(transcript)
    marker = transcript.metadata.get(CURRENT_TEXT_KEY)
    if CURRENT_TEXT_KEY not in transcript.metadata:
        field, language = "source_text", transcript.language
    elif (
        not isinstance(marker, dict)
        or set(marker) != {"version", "field", "language"}
        or marker.get("version") != "1"
        or not isinstance(marker.get("field"), str)
        or marker.get("field") not in TEXT_FIELDS
        or not isinstance(marker.get("language"), str)
        or not marker["language"]
        or len(marker["language"]) > 32
        or marker["language"] != marker["language"].strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in marker["language"])
    ):
        raise NodeExecutionError("CURRENT_TEXT_INVALID", "转写当前文本声明无效")
    else:
        field, language = marker["field"], marker["language"]
    items = []
    for segment in transcript.segments:
        text = getattr(segment, field)
        if not isinstance(text, str) or not text.strip():
            raise NodeExecutionError("CURRENT_TEXT_MISSING", "转写当前文本不完整")
        items.append({"id": segment.id, "text": text})
    return items, language


def with_current_text(transcript, field, language):
    result = transcript.model_copy(deep=True)
    result.metadata[CURRENT_TEXT_KEY] = {"version": "1", "field": field, "language": language}
    read_current_text(result)
    return result
