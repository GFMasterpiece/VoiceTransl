from typing import Protocol
from .context import PipelineContext
from ..domain import PipelineStageName

class PipelineStage(Protocol):
    name: PipelineStageName
    async def run(self, context: PipelineContext) -> None: ...
