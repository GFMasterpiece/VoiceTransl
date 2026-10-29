"""Supported persisted document versions."""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"


class SchemaVersionError(ValueError):
    def __init__(self, document: str, version: object):
        self.document = document
        self.version = version
        super().__init__(
            f"{document} 的 schema_version 必须为 {SCHEMA_VERSION!r}，收到 {version!r}"
        )


def require_schema_version(value: object, document: str = "document") -> str:
    """Accept only an explicit current version."""

    if not isinstance(value, str) or value != SCHEMA_VERSION:
        raise SchemaVersionError(document, value)
    return SCHEMA_VERSION


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
        profiles = payload.get("provider_profiles_snapshot")
        if profiles is not None:
            if not isinstance(profiles, dict):
                raise SchemaVersionError(f"{document}.provider_profiles_snapshot", None)
            for profile_id, profile in profiles.items():
                _require_nested(profile, f"provider_profiles_snapshot.{profile_id}", document)
    elif document == "workflow run":
        _require_nested(payload.get("workflow"), "workflow", document)
    elif document == "provider profiles":
        profiles = payload.get("profiles")
        if not isinstance(profiles, list):
            raise SchemaVersionError(f"{document}.profiles", None)
        for index, profile in enumerate(profiles):
            _require_nested(profile, f"profiles.{index}", document)


__all__ = [
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "require_schema_version",
    "validate_schema_payload",
]
