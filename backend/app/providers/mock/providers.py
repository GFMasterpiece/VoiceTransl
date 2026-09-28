import asyncio
from pathlib import Path
from datetime import datetime, timezone
from ..protocols import TranslationContext
from ...domain import Project, Transcript, Segment

class MockASRProvider:
    def __init__(self, delay_ms=500): self.delay_ms=delay_ms
    async def transcribe(self, audio: Path, project: Project) -> Transcript:
        await asyncio.sleep(self.delay_ms/1000)
        segs=[Segment(id="seg_001",start=0,end=2.5,source_text="こんにちは。"),Segment(id="seg_002",start=2.5,end=5,source_text="先輩、こっちを見て。"),Segment(id="seg_003",start=5,end=8,source_text="そう、いい子ですね。")]
        return Transcript(project_id=project.id,stage="raw",language="ja",duration=8,created_at=datetime.now(timezone.utc),segments=segs)

class MockAlignerProvider:
    def __init__(self, delay_ms=500): self.delay_ms=delay_ms
    async def align(self, audio: Path, transcript: Transcript) -> Transcript:
        await asyncio.sleep(self.delay_ms/1000); copy=transcript.model_copy(deep=True); copy.stage="aligned"
        for s in copy.segments:
            if s.start is None or s.end is None or s.end < s.start: raise ValueError("invalid alignment time range")
        return copy

class MockTranslatorProvider:
    translations={"seg_001":"你好。","seg_002":"前辈，看这边。","seg_003":"对，真是个乖孩子。"}
    def __init__(self, delay_ms=500): self.delay_ms=delay_ms
    async def translate(self, segments, context: TranslationContext):
        await asyncio.sleep(self.delay_ms/1000)
        return [s.model_copy(update={"translated_text":self.translations.get(s.id, s.source_text)}) for s in segments]
