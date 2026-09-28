from ..domain import Segment
def render_lrc(segments: list[Segment]) -> str:
    rows=[]
    for s in segments:
        if s.start is None or s.end is None or not s.translated_text: raise ValueError("segments need time and translation")
        centi=round(s.start*100); mm,cc=divmod(centi,6000); ss,xx=divmod(cc,100); rows.append(f"[{mm:02d}:{ss:02d}.{xx:02d}]{s.translated_text}")
    return "\n".join(rows)+("\n" if rows else "")
