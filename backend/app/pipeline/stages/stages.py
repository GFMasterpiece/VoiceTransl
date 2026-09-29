from copy import deepcopy

from ...domain import PipelineStageName, Transcript
from ...exporters import render_lrc, render_srt
from ...providers import TranslationContext


class BaseStage:
    def __init__(self, name):
        self.name = name


class PreprocessStage(BaseStage):
    def __init__(self):
        super().__init__(PipelineStageName.PREPROCESS)

    async def run(self, c):
        c.check_cancelled()
        p = c.store.save_cache_json(
            c.project.id, "preprocess.json", {"schema_version": "1.0", "status": "mock"}
        )
        c.artifacts[self.name.value] = p


class ASRStage(BaseStage):
    def __init__(self, provider):
        super().__init__(PipelineStageName.ASR)
        self.provider = provider

    async def run(self, c):
        c.transcript = await self.provider.transcribe(
            c.store.source_path(c.project.id, c.project.source.path), c.project
        )
        p = c.store.save_transcript(
            c.project.id, "raw", c.transcript, overwrite=c.allow_transcript_overwrite
        )
        c.artifacts[self.name.value] = p


class AlignmentStage(BaseStage):
    def __init__(self, provider):
        super().__init__(PipelineStageName.ALIGNMENT)
        self.provider = provider

    async def run(self, c):
        c.transcript = await self.provider.align(
            c.store.source_path(c.project.id, c.project.source.path), c.transcript
        )
        p = c.store.save_transcript(
            c.project.id, "aligned", c.transcript, overwrite=c.allow_transcript_overwrite
        )
        c.artifacts[self.name.value] = p


class TranslationStage(BaseStage):
    def __init__(self, provider):
        super().__init__(PipelineStageName.TRANSLATION)
        self.provider = provider

    async def run(self, c):
        original = deepcopy(c.transcript.segments)
        provider_input = deepcopy(original)
        config = c.config
        result = await self.provider.translate(
            provider_input,
            TranslationContext(
                source_language=config.source_language if config else c.project.source.language,
                target_language=config.target_language if config else "zh-CN",
                glossary=config.translation.glossary if config else {},
                instructions=config.translation.instructions if config else None,
            ),
        )
        if len(result) != len(original) or [s.id for s in result] != [s.id for s in original]:
            raise ValueError("translator changed segment count or ids")
        for a, b in zip(original, result):
            immutable_a = a.model_dump(exclude={"translated_text"})
            immutable_b = b.model_dump(exclude={"translated_text"})
            if immutable_a != immutable_b or not b.translated_text:
                raise ValueError("translator changed immutable fields or omitted translation")
        c.transcript = Transcript(
            project_id=c.project.id,
            stage="translated",
            language=config.target_language if config else "zh-CN",
            duration=c.transcript.duration,
            segments=result,
        )
        p = c.store.save_transcript(
            c.project.id, "translated", c.transcript, overwrite=c.allow_transcript_overwrite
        )
        c.artifacts[self.name.value] = p


class ExportStage(BaseStage):
    def __init__(self):
        super().__init__(PipelineStageName.EXPORT)

    async def run(self, c):
        out = c.project_dir / "output"
        out.mkdir(exist_ok=True)
        segs = c.transcript.segments
        files = {
            "zh.srt": render_srt(segs),
            "ja-zh.srt": render_srt(segs, True),
            "zh.lrc": render_lrc(segs),
        }
        for name, text in files.items():
            c.store.save_output_text(c.project.id, name, text)
        c.artifacts[self.name.value] = out
