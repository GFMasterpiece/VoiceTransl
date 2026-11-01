"""Port-based execution contracts shared by the workflow runner and node registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workflow import WorkflowPreview


@dataclass(frozen=True)
class NodeData:
    port: str
    value: object

    def __post_init__(self):
        if not self.port or "/" in self.port or "\\" in self.port:
            raise ValueError("node data port is invalid")


@dataclass
class NodeResult:
    outputs: dict[str, NodeData] = field(default_factory=dict)
    artifact: object | None = None
    preview: WorkflowPreview | None = None

    def add(self, port: str, value: object) -> None:
        if port in self.outputs:
            raise ValueError(f"duplicate node output port: {port}")
        self.outputs[port] = NodeData(port=port, value=value)


@dataclass(frozen=True)
class NodeExecutionContext:
    store: object
    run: object
    node: object
    inputs: dict[str, NodeData]
    project: object
    cancel_event: object
    mock_delay_ms: int = 0

    def input(self, port: str) -> NodeData:
        try:
            return self.inputs[port]
        except KeyError as exc:
            raise ValueError(f"missing input port: {port}") from exc


__all__ = ["NodeData", "NodeResult", "NodeExecutionContext"]
