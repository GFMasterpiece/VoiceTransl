from pathlib import Path
from typing import Protocol
from pydantic import BaseModel, Field
from ..domain import Project, Transcript, Segment

class TranslationContext(BaseModel):
    source_language: str = "ja"; target_language: str = "zh-CN"; title: str | None = None; character: str | None = None; glossary: dict[str,str] = Field(default_factory=dict); instructions: str | None = None
class ASRProvider(Protocol):
    async def transcribe(self, audio: Path, project: Project) -> Transcript: ...
class AlignerProvider(Protocol):
    async def align(self, audio: Path, transcript: Transcript) -> Transcript: ...
class TranslatorProvider(Protocol):
    async def translate(self, segments: list[Segment], context: TranslationContext) -> list[Segment]: ...
