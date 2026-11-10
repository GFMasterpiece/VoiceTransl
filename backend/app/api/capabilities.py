"""Capabilities API assembled from the live workflow registry."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..domain.languages import LanguageCandidate, language_catalog
from ..domain.node_registry import (
    TEXT_OUTPUT_NODE_TYPE,
    asr_provider_catalog,
    node_catalog,
    port_catalog,
    type_catalog,
)
from ..domain.schema import SCHEMA_VERSION
from ..domain.workflow import (
    WORKFLOW_GRAPH_CONSTRAINTS,
    WorkflowEdge,
    WorkflowNode,
    default_workflow,
)

router = APIRouter(prefix="/api/v1")
WORKFLOW_CAPABILITY_SCHEMA_VERSION = "1.19"


class WorkflowGraphConstraints(BaseModel):
    model_config = {"extra": "forbid"}
    max_nodes: int
    max_edges: int
    min_sources: int
    max_sources: int
    min_terminals: int


class WorkflowDefaultGraph(BaseModel):
    model_config = {"extra": "forbid"}
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]


class CapabilitiesWorkflow(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: str = WORKFLOW_CAPABILITY_SCHEMA_VERSION
    ports: list[dict[str, object]]
    data_types: list[dict[str, object]]
    nodes: list[dict[str, object]]
    constraints: WorkflowGraphConstraints
    default_graph: WorkflowDefaultGraph
    execution: dict[str, object]


class ASRCapabilities(BaseModel):
    model_config = {"extra": "forbid"}
    providers: list[dict[str, str]]


class ExportCapabilities(BaseModel):
    model_config = {"extra": "forbid"}
    formats: list[dict[str, str]]
    naming_modes: list[dict[str, str]]


class CapabilitiesResponse(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: str = SCHEMA_VERSION
    languages: list[LanguageCandidate]
    workflow: CapabilitiesWorkflow
    asr: ASRCapabilities
    export: ExportCapabilities


def _text_output_options(nodes: list[dict[str, object]], key: str) -> list[dict[str, str]]:
    text_output = next(node for node in nodes if node["type"] == TEXT_OUTPUT_NODE_TYPE)
    config = text_output["config"]
    if not isinstance(config, dict):
        raise TypeError("text_output capability config must be an object")
    fields = config.get("fields")
    if not isinstance(fields, list):
        raise TypeError("text_output capability fields must be a list")
    selected_field = next(
        field for field in fields if isinstance(field, dict) and field.get("key") == key
    )
    options = selected_field.get("options")
    if not isinstance(options, list):
        raise TypeError(f"text_output {key} capability options must be a list")
    return [
        {"id": str(option["value"]), "label": str(option["label"])}
        for option in options
        if isinstance(option, dict) and "value" in option and "label" in option
    ]


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities():
    nodes = node_catalog()
    default_graph = default_workflow("capabilities-default")
    return {
        "schema_version": SCHEMA_VERSION,
        "languages": language_catalog(),
        "workflow": {
            "schema_version": WORKFLOW_CAPABILITY_SCHEMA_VERSION,
            "ports": port_catalog(),
            "data_types": type_catalog(),
            "nodes": nodes,
            "constraints": WORKFLOW_GRAPH_CONSTRAINTS,
            "execution": {
                "scope": "source_branch",
                "target_kinds": ["source", "canvas"],
                "cross_source_merge": False,
            },
            "default_graph": {
                "nodes": default_graph.nodes,
                "edges": default_graph.edges,
            },
        },
        "asr": {"providers": asr_provider_catalog()},
        "export": {
            "formats": _text_output_options(nodes, "format"),
            "naming_modes": _text_output_options(nodes, "naming_mode"),
        },
    }


__all__ = ["router", "CapabilitiesResponse"]
