"""One atomic settings document; immutable encrypted keys are prepared first."""

import asyncio
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..domain.schema import CONFIGURATION_SCHEMA_VERSION as SCHEMA_VERSION
from ..domain.schema import require_schema_version
from ..domain.settings import (
    GlobalSettings,
    default_definitions,
    initial_defaults,
    normalize_defaults,
)
from .atomic import atomic_write_json
from .secret_store import SecretStore


class SettingsConflict(ValueError):
    pass


class SettingsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION
    revision: int = Field(ge=0)
    global_: GlobalSettings = Field(alias="global")
    node_defaults: dict[str, dict[str, object]]
    credentials: dict[str, str] = Field(default_factory=dict)
    request_id: str | None = None
    request_digest: str | None = None


class SettingsStore:
    def __init__(self, data_dir, startup):
        self.path = Path(data_dir) / "settings-v4.json"
        self.secrets = SecretStore(data_dir)
        self.lock = asyncio.Lock()
        self.initial = GlobalSettings(
            upload_concurrency=startup.upload_concurrency, max_concurrent=startup.max_concurrent
        )

    def load(self):
        if self.path.is_symlink():
            raise ValueError("settings must not be a symlink")
        if not self.path.exists():
            return SettingsDocument(
                revision=0, **{"global": self.initial}, node_defaults=initial_defaults()
            )
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings must be an object")
        require_schema_version(raw.get("schema_version"), "settings")
        document = SettingsDocument.model_validate(raw)
        document.node_defaults = normalize_defaults(document.node_defaults, allow_missing=True)
        definitions = default_definitions()
        for key, ref in document.credentials.items():
            definition = definitions.get(key)
            binding = (
                (definition.presentation or {}).get("inline_connection_input")
                if definition
                else None
            )
            if not binding:
                raise ValueError("默认凭据节点无效")
            self.secrets.read_default(key, binding["provider_type"], ref)
        return document

    def save(self, body):
        current = self.load()
        if current.revision != body.expected_revision and not (
            current.revision == body.expected_revision + 1 and current.request_id == body.request_id
        ):
            raise SettingsConflict
        defaults = normalize_defaults(body.node_defaults)
        credentials = dict(current.credentials)
        seen = set()
        for update in body.credential_updates:
            definition = default_definitions().get(update.node_type)
            binding = (
                (definition.presentation or {}).get("inline_connection_input")
                if definition
                else None
            )
            if not binding or update.node_type in seen:
                raise ValueError("默认凭据更新无效")
            seen.add(update.node_type)
            if update.operation == "clear":
                credentials.pop(update.node_type, None)
            else:
                credentials[update.node_type] = self.secrets.create_default(
                    update.node_type,
                    binding["provider_type"],
                    update.api_key.get_secret_value(),
                    request_id=f"{body.request_id}:{body.expected_revision}",
                )
        payload = {
            "global": body.global_.model_dump(),
            "node_defaults": defaults,
            "credentials": credentials,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if current.revision != body.expected_revision:
            if (
                current.revision == body.expected_revision + 1
                and current.request_id == body.request_id
                and current.request_digest == digest
            ):
                return current
            raise SettingsConflict
        document = SettingsDocument(
            revision=current.revision + 1,
            request_id=body.request_id,
            request_digest=digest,
            **payload,
        )
        atomic_write_json(self.path, document.model_dump(mode="json", by_alias=True))
        return document
