import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from ..domain import Job, Project, Transcript


class PipelineCancelled(Exception):
    pass


@dataclass
class PipelineContext:
    project: Project
    project_dir: Path
    job: Job
    preset: dict
    store: object
    cancel_event: asyncio.Event
    transcript: Transcript | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    allow_transcript_overwrite: bool = False

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise PipelineCancelled()
