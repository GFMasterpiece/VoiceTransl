from ..domain import Segment
from .srt import _stamp, _validate


def render_vtt(segments: list[Segment], bilingual=False) -> str:
    _validate(segments)
    blocks = []
    for index, segment in enumerate(segments, 1):
        if not segment.translated_text:
            raise ValueError("missing translated text")
        text = (
            f"{segment.source_text}\n{segment.translated_text}"
            if bilingual
            else segment.translated_text
        )
        start = _stamp(segment.start).replace(",", ".")
        end = _stamp(segment.end).replace(",", ".")
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")
