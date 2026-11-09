"""Explicit, versioned contracts for durable typed node outputs."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class CheckpointCodec:
    id: str
    version: str
    encode: Callable[[object, object], Awaitable[object]]
    decode: Callable[[object, object], Awaitable[object]]


class CheckpointDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = "1"
    fingerprint: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class CheckpointUnavailable(ValueError):
    """A fixed, safe reason explaining why saved output cannot be reused."""

    def __init__(self, code="CHECKPOINT_INVALID", message="节点结果不可复用，请重新运行上游节点"):
        self.code = code
        super().__init__(message)
