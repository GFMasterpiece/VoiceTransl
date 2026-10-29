"""Capabilities API assembled from the live workflow registry."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..domain.node_registry import (
    TEXT_OUTPUT_NODE_TYPE,
    asr_provider_catalog,
    node_catalog,
    workflow_ports,
)
from ..domain.schema import SCHEMA_VERSION

router = APIRouter(prefix="/api/v1")


class CapabilitiesWorkflow(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: str = SCHEMA_VERSION
    ports: list[str]
    nodes: list[dict[str, object]]


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
    workflow: CapabilitiesWorkflow
    asr: ASRCapabilities
    export: ExportCapabilities


def _text_output_formats(nodes: list[dict[str, object]]) -> list[dict[str, str]]:
    text_output = next(node for node in nodes if node["type"] == TEXT_OUTPUT_NODE_TYPE)
    config = text_output["config"]
    if not isinstance(config, dict):
        raise TypeError("text_output capability config must be an object")
    fields = config.get("fields")
    if not isinstance(fields, list):
        raise TypeError("text_output capability fields must be a list")
    format_field = next(
        field for field in fields if isinstance(field, dict) and field.get("key") == "format"
    )
    options = format_field.get("options")
    if not isinstance(options, list):
        raise TypeError("text_output format capability options must be a list")
    return [
        {"id": str(option["value"]), "label": str(option["label"])}
        for option in options
        if isinstance(option, dict) and "value" in option and "label" in option
    ]


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities():
    nodes = node_catalog()
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow": {
            "schema_version": "1.0",
            "ports": list(workflow_ports()),
            "nodes": nodes,
        },
        "asr": {"providers": asr_provider_catalog()},
        "export": {
            "formats": _text_output_formats(nodes),
            "naming_modes": [],
        },
    }


__all__ = ["router", "CapabilitiesResponse"]
