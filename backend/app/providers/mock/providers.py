import asyncio
from datetime import datetime, timezone
from pathlib import Path

from ...domain import Project, Segment, Transcript
from ...domain.node_config import MockPreprocessNodeConfig


class MockPreprocessProvider:
    async def preprocess(
        self, audio: Path, output: Path, config: MockPreprocessNodeConfig
    ) -> dict[str, object]:
        return {
            "status": "mock",
            "provider": "mock",
            "source_path": audio.name,
            "output_path": audio.name,
        }


class MockASRProvider:
    def __init__(self, delay_ms=500):
        self.delay_ms = delay_ms

    async def transcribe(self, audio: Path, project: Project) -> Transcript:
        await asyncio.sleep(self.delay_ms / 1000)
        segs = [
            Segment(id="seg_001", start=0, end=2.5, source_text="こんにちは。"),
            Segment(id="seg_002", start=2.5, end=5, source_text="先輩、こっちを見て。"),
            Segment(id="seg_003", start=5, end=8, source_text="そう、いい子ですね。"),
        ]
        return Transcript(
            project_id=project.id,
            stage="raw",
            language="ja",
            duration=8,
            created_at=datetime.now(timezone.utc),
            segments=segs,
        )
