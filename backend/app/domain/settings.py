"""Registry-derived defaults used only when explicitly creating new nodes."""

from pydantic import BaseModel, ConfigDict, Field

from .node_contracts import CapabilityField
from .node_registry import NODE_DEFINITIONS


class GlobalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    upload_concurrency: int = Field(default=3, ge=1, le=32)
    max_concurrent: int = Field(default=1, ge=1)


def global_fields():
    from .node_contracts import fields_from_model

    return list(fields_from_model(GlobalSettings))


def default_definitions():
    return {key: item for key, item in NODE_DEFINITIONS.items() if item.settings_default_fields}


def default_fields(definition):
    fields = {field.key: field for field in definition.fields}
    return [fields[key] for key in definition.settings_default_fields]


def normalize_defaults(values, *, allow_missing=False):
    definitions = default_definitions()
    if allow_missing:
        values = {
            key: value
            for key, value in values.items()
            if key not in NODE_DEFINITIONS or NODE_DEFINITIONS[key].settings_default_fields
        }
    if set(values) - set(definitions) or (not allow_missing and set(values) != set(definitions)):
        raise ValueError("默认节点类型必须与目录一致")
    result = {}
    for key, definition in definitions.items():
        config = values.get(key, {})
        allowed = set(definition.settings_default_fields)
        if (
            not isinstance(config, dict)
            or set(config) - allowed
            or (not allow_missing and set(config) != allowed)
        ):
            raise ValueError("默认配置字段必须与目录一致")
        if allow_missing:
            base = definition.normalize_config({})
            config = {field: base[field] for field in allowed} | config
        normalized = definition.validate_config(config)
        result[key] = {field: normalized[field] for field in definition.settings_default_fields}
    return result


def initial_defaults():
    result = {}
    for key, definition in default_definitions().items():
        normalized = definition.normalize_config({})
        result[key] = {field: normalized[field] for field in definition.settings_default_fields}
    return result


class SettingsNodeDefaults(BaseModel):
    label: str
    fields: list[CapabilityField]
    config: dict[str, object]
    credential_configured: bool
    credential_version: str | None


class SettingsResponse(BaseModel):
    schema_version: str
    revision: int
    global_fields: list[CapabilityField]
    global_: GlobalSettings = Field(alias="global")
    node_defaults: dict[str, SettingsNodeDefaults]
