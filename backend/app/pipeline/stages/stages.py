from copy import deepcopy

from ...domain import PipelineConfig, PipelineStageName, Transcript
from ...exporters import output_names, render_lrc, render_srt, render_vtt
from ...providers import PreprocessProvider, TranslationContext


class BaseStage:
    def __init__(self, name):
        self.name = name


class PreprocessStage(BaseStage):
    def __init__(self, provider: PreprocessProvider | None = None):
        super().__init__(PipelineStageName.PREPROCESS)
        if provider is None:
            from ...providers import MockPreprocessProvider

            provider = MockPreprocessProvider()
        self.provider = provider

    async def run(self, c):
        c.check_cancelled()
        source = c.store.source_path(c.project.id, c.project.source.path)
        output = c.store.resolve_project_path(c.project.id, "cache/preprocessed.wav")
        config = c.config or PipelineConfig()
        metadata = await self.provider.preprocess(source, output, config.preprocess)
        if metadata.get("provider") == "ffmpeg":
            c.audio_path = output
            metadata["source_path"] = source.relative_to(c.project_dir).as_posix()
            metadata["output_path"] = output.relative_to(c.project_dir).as_posix()
        else:
            c.audio_path = source
            metadata["source_path"] = source.relative_to(c.project_dir).as_posix()
            metadata["output_path"] = source.relative_to(c.project_dir).as_posix()
        metadata["schema_version"] = "1.0"
        # The source upload is immutable in every mode. ``preserve_original``
        # records the requested policy for future providers without allowing a
        # preprocessing stage to overwrite or delete user data.
        metadata["preserve_original"] = config.preprocess.preserve_original
        p = c.store.save_cache_json(c.project.id, "preprocess.json", metadata)
        c.artifacts[self.name.value] = p


class ASRStage(BaseStage):
    def __init__(self, provider):
        super().__init__(PipelineStageName.ASR)
        self.provider = provider

    async def run(self, c):
        try:
            c.transcript = await self.provider.transcribe(c.input_audio_path(), c.project)
        except Exception as exc:
            # A cancellable external provider reports its stable cancellation
            # code; convert it into the runner's cooperative cancellation path.
            if getattr(exc, "code", None) == "ASR_CANCELLED":
                c.check_cancelled()
            raise
        p = c.store.save_transcript(
            c.project.id, "raw", c.transcript, overwrite=c.allow_transcript_overwrite
        )
        c.artifacts[self.name.value] = p


class AlignmentStage(BaseStage):
    def __init__(self, provider):
        super().__init__(PipelineStageName.ALIGNMENT)
        self.provider = provider

    async def run(self, c):
        c.transcript = await self.provider.align(c.input_audio_path(), c.transcript)
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
        try:
            result = await self.provider.translate(
                provider_input,
                TranslationContext(
                    source_language=config.source_language if config else c.project.source.language,
                    target_language=config.target_language if config else "zh-CN",
                    glossary=config.translation.glossary if config else {},
                    instructions=config.translation.instructions if config else None,
                    batch_size=config.translation.batch_size if config else 8,
                    temperature=config.translation.temperature if config else 0.2,
                ),
            )
        except Exception as exc:
            if getattr(exc, "code", None) == "TRANSLATION_CANCELLED":
                c.check_cancelled()
            raise
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
        out = c.store.resolve_project_path(c.project.id, "output")
        out.mkdir(exist_ok=True)
        segs = c.transcript.segments
        config = c.config or PipelineConfig()
        files = {}
        for name in output_names(config):
            bilingual = name.startswith("ja-zh.")
            extension = name.rsplit(".", 1)[1]
            renderer = {"srt": render_srt, "vtt": render_vtt, "lrc": render_lrc}[extension]
            files[name] = renderer(segs) if extension == "lrc" else renderer(segs, bilingual)
        for name, text in files.items():
            c.store.save_output_text(c.project.id, name, text)
        c.store.prune_generated_outputs(c.project.id, set(files))
        c.artifacts[self.name.value] = out
