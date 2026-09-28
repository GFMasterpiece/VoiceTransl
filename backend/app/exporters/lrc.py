import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..domain import Segment


def render_lrc(segments: list[Segment]) -> str:
    rows = []
    for s in segments:
        if (
            s.start is None
            or s.end is None
            or not math.isfinite(s.start)
            or not math.isfinite(s.end)
            or s.start < 0
            or s.end < s.start
            or not s.translated_text
        ):
            raise ValueError("segments need valid time and translation")
        try:
            centi = int(
                (Decimal(str(s.start)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
        except (InvalidOperation, ValueError):
            raise ValueError("timestamp must be finite")
        mm, cc = divmod(centi, 6000)
        ss, xx = divmod(cc, 100)
        rows.append(f"[{mm:02d}:{ss:02d}.{xx:02d}]{s.translated_text}")
    return "\n".join(rows) + ("\n" if rows else "")
