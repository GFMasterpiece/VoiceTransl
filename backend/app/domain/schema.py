"""Supported persisted document versions."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
CONFIGURATION_SCHEMA_VERSION = "2.0"


def document_version(document):
    return (
        CONFIGURATION_SCHEMA_VERSION
        if document.split(".")[0]
        in {
            "workflow",
            "workflow snapshot",
            "workflow request",
            "workflow validation request",
            "workflow run",
            "batch",
            "settings",
            "settings request",
        }
        else SCHEMA_VERSION
    )


class SchemaVersionError(ValueError):
    def __init__(self, document: str, version: object):
        self.document = document
        self.version = version
        super().__init__(
            f"{document} 的 schema_version 必须为 {document_version(document)!r}，收到 {version!r}"
        )


def require_schema_version(value: object, document: str = "document") -> str:
    """Accept only an explicit current version."""

    expected = document_version(document)
    if not isinstance(value, str) or value != expected:
        raise SchemaVersionError(document, value)
    return expected


def _require_nested(payload: Any, path: str, document: str) -> None:
    if not isinstance(payload, dict):
        raise SchemaVersionError(f"{document}.{path}", None)
    require_schema_version(payload.get("schema_version"), f"{document}.{path}")


def validate_schema_payload(payload: Any, document: str = "document") -> None:
    if not isinstance(payload, dict):
        raise SchemaVersionError(document, None)
    require_schema_version(payload.get("schema_version"), document)
    if document == "job":
        if "config_snapshot" in payload and payload["config_snapshot"] is not None:
            _require_nested(payload["config_snapshot"], "config_snapshot", document)
    elif document in {"workflow run", "batch"}:
        _require_nested(payload.get("workflow"), "workflow", document)


__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "require_schema_version",
    "validate_schema_payload",
]
