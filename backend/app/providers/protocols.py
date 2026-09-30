from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from ..domain import PreprocessConfig, Project, Segment, Transcript


class TranslationContext(BaseModel):
    source_language: str = "ja"
    target_language: str = "zh-CN"
    title: str | None = None
    character: str | None = None
    glossary: dict[str, str] = Field(default_factory=dict)
    instructions: str | None = None
    batch_size: int = Field(default=8, ge=1, le=128)
    temperature: float = Field(default=0.2, ge=0, le=2)


class PreprocessProvider(Protocol):
    async def preprocess(
        self, audio: Path, output: Path, config: PreprocessConfig
    ) -> dict[str, object]: ...


class ASRProvider(Protocol):
    async def transcribe(self, audio: Path, project: Project) -> Transcript: ...


class AlignerProvider(Protocol):
    async def align(self, audio: Path, transcript: Transcript) -> Transcript: ...


class TranslatorProvider(Protocol):
    async def translate(
        self, segments: list[Segment], context: TranslationContext
    ) -> list[Segment]: ...
