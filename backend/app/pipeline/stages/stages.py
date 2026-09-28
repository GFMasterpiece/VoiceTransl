from pathlib import Path
from ...domain import PipelineStageName, Transcript
from ...providers import TranslationContext
from ...exporters import render_srt, render_lrc
from ...storage import atomic_write_json
import os
from uuid import uuid4

class BaseStage:
    def __init__(self, name): self.name=name
class PreprocessStage(BaseStage):
    def __init__(self): super().__init__(PipelineStageName.PREPROCESS)
    async def run(self,c):
        c.check_cancelled(); p=c.project_dir/"cache"/"preprocess.json"; atomic_write_json(p,{"schema_version":"1.0","status":"mock"}); c.artifacts[self.name.value]=p
class ASRStage(BaseStage):
    def __init__(self, provider): super().__init__(PipelineStageName.ASR); self.provider=provider
    async def run(self,c): c.transcript=await self.provider.transcribe(c.project_dir/c.project.source.path,c.project); p=c.store.save_transcript(c.project.id,"raw",c.transcript); c.artifacts[self.name.value]=p
class AlignmentStage(BaseStage):
    def __init__(self, provider): super().__init__(PipelineStageName.ALIGNMENT); self.provider=provider
    async def run(self,c): c.transcript=await self.provider.align(c.project_dir/c.project.source.path,c.transcript); p=c.store.save_transcript(c.project.id,"aligned",c.transcript); c.artifacts[self.name.value]=p
class TranslationStage(BaseStage):
    def __init__(self, provider): super().__init__(PipelineStageName.TRANSLATION); self.provider=provider
    async def run(self,c):
        original=c.transcript.segments; result=await self.provider.translate(original,TranslationContext())
        if len(result)!=len(original) or [s.id for s in result] != [s.id for s in original]: raise ValueError("translator changed segment count or ids")
        for a,b in zip(original,result):
            if (a.start,a.end,a.source_text)!=(b.start,b.end,b.source_text) or not b.translated_text: raise ValueError("translator changed immutable fields or omitted translation")
        c.transcript=Transcript(project_id=c.project.id,stage="translated",language="zh-CN",duration=c.transcript.duration,segments=result); p=c.store.save_transcript(c.project.id,"translated",c.transcript); c.artifacts[self.name.value]=p
class ExportStage(BaseStage):
    def __init__(self): super().__init__(PipelineStageName.EXPORT)
    async def run(self,c):
        out=c.project_dir/"output"; out.mkdir(exist_ok=True); segs=c.transcript.segments
        files={"zh.srt":render_srt(segs),"ja-zh.srt":render_srt(segs,True),"zh.lrc":render_lrc(segs)}
        for name,text in files.items():
            target=out/name; temp=out/f".{name}.{uuid4().hex}.tmp"
            try:
                with temp.open("w",encoding="utf-8",newline="\n") as stream: stream.write(text); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp,target)
            finally: temp.unlink(missing_ok=True)
        c.artifacts[self.name.value]=out
