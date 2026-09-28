from typing import Protocol

from ..domain import PipelineStageName
from .context import PipelineContext


class PipelineStage(Protocol):
    name: PipelineStageName

    async def run(self, context: PipelineContext) -> None: ...
