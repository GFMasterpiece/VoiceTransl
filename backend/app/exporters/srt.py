from ..domain import Segment
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math

def _stamp(seconds: float) -> str:
    try:
        value = Decimal(str(seconds))
    except (InvalidOperation, ValueError):
        raise ValueError("timestamp must be finite")
    if not value.is_finite() or value < 0:
        raise ValueError("timestamp must be finite and non-negative")
    ms = int((value * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000); return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
def _validate(segments):
    if any(s.start is None or s.end is None or not math.isfinite(s.start) or not math.isfinite(s.end) or s.start < 0 or s.end < s.start for s in segments): raise ValueError("all segments need valid time ranges")
def render_srt(segments: list[Segment], bilingual=False) -> str:
    _validate(segments); blocks=[]
    for i,s in enumerate(segments,1):
        if not s.translated_text: raise ValueError("missing translated text")
        text=f"{s.source_text}\n{s.translated_text}" if bilingual else s.translated_text
        blocks.append(f"{i}\n{_stamp(s.start)} --> {_stamp(s.end)}\n{text}")
    return "\n\n".join(blocks)+("\n" if blocks else "")
