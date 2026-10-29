"""Capabilities API assembled from the live backend registry and discovery."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..application.local_asr_models import LocalASRModels
from ..domain.node_registry import asr_provider_catalog, node_catalog, workflow_ports
from ..domain.pipeline_config import EXPORT_FORMATS, EXPORT_NAMING_MODES
from ..domain.schema import SCHEMA_VERSION

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("voicetransl.capabilities")


class CapabilitiesWorkflow(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: str = SCHEMA_VERSION
    ports: list[str]
    nodes: list[dict[str, object]]


class ASRCapabilities(BaseModel):
    model_config = {"extra": "forbid"}
    providers: list[dict[str, str]]
    backends: list[str]
    backend_details: list[dict[str, object]] = Field(default_factory=list)
    status: str
    message: str | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


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


def _error(code: str, message: str, status: int = 503) -> HTTPException:
    return HTTPException(status, detail={"error": {"code": code, "message": message}})


async def _asr_capabilities(models: LocalASRModels) -> dict[str, object]:
    try:
        discovered = await models.get()
    except Exception:
        logger.warning("capability discovery failed", exc_info=True)
        return {
            "providers": asr_provider_catalog(),
            "backends": [],
            "backend_details": [],
            "status": "unavailable",
            "message": "当前无法读取本地 ASR 能力；Mock 节点仍可用",
            "truncated": False,
            "warnings": [],
        }
    executable = discovered.get("executable")
    executable_ok = isinstance(executable, dict) and executable.get("ok") is True
    raw_backends = discovered.get("backends", [])
    raw_details = discovered.get("backend_details", [])
    backends = [item for item in raw_backends if isinstance(item, str)]
    details = [dict(item) for item in raw_details if isinstance(item, dict)]
    return {
        "providers": asr_provider_catalog(),
        "backends": backends if executable_ok else [],
        "backend_details": details if executable_ok else [],
        "status": "available" if executable_ok else "unavailable",
        "message": (None if executable_ok else "当前 CrispASR 程序不可用或未配置；Mock 节点仍可用"),
        "truncated": bool(discovered.get("truncated", False)),
        "warnings": list(discovered.get("warnings", [])),
    }


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(request: Request):
    state = request.app.state
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow": {
            "schema_version": "1.0",
            "ports": list(workflow_ports()),
            "nodes": node_catalog(),
        },
        "asr": await _asr_capabilities(state.local_asr_models),
        "export": {
            "formats": [{"id": value, "label": value.upper()} for value in EXPORT_FORMATS],
            "naming_modes": [
                {"id": value, "label": "按语言命名" if value == "language" else value}
                for value in EXPORT_NAMING_MODES
            ],
        },
    }


__all__ = ["router", "CapabilitiesResponse"]
