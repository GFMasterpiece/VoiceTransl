"""Self-describing node contracts, independent of built-in implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .checkpoint import CheckpointCodec
from .node_execution import NodeExecutionContext, NodeResult


class CapabilityField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    type: str
    required: bool = False
    default: object = None
    creation_default: object = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    description: str | None = None
    validator: str | None = None
    suggestion_source: Literal["languages"] | None = None
    input_port: str | None = None
    input_type_id: str | None = None
    widget: Literal["multiline"] | None = None
    options: list[dict[str, object]] = Field(default_factory=list)


class NodeConfigCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "object"
    fields: list[CapabilityField] = Field(default_factory=list)


ConfigCallable = Callable[[dict[str, object]], dict[str, object]]
ExecutionConfigCallable = Callable[[dict[str, object], frozenset[str]], None]
ExecuteCallable = Callable[[NodeExecutionContext], Awaitable[NodeResult]]


@dataclass(frozen=True)
class NodeCategory:
    id: str
    label: str


@dataclass(frozen=True)
class NodeDefinition:
    type: str
    label: str
    input_ports: tuple[str, ...]
    output_ports: tuple[str, ...]
    terminal: bool
    config_kind: str
    fields: tuple[CapabilityField, ...] = field(default_factory=tuple)
    max_instances: int | None = None
    required_inbound: int = 1
    removable: bool = True
    is_source: bool = False
    normalize_config: ConfigCallable | None = None
    validate_config: ConfigCallable | None = None
    execute: ExecuteCallable | None = None
    presentation: dict[str, object] | None = None
    required_input_ports: tuple[str, ...] | None = None
    is_parameter_source: bool = False
    category: NodeCategory | None = None
    settings_default_fields: tuple[str, ...] = ()
    execution_version: str = "1"
    debug_only: bool = False
    validate_execution_config: ExecutionConfigCallable | None = None
    evaluation_kind: Literal["configuration", "processing"] = "processing"
    evaluate_config: ExecuteCallable | None = None
    can_start_without_inputs: bool = False
    binding_inputs: dict[str, str] = field(default_factory=dict)
    validate_inputs: Callable[[NodeExecutionContext], None] | None = None
    validation_input_ports: tuple[str, ...] = ()

    @property
    def required_ports(self) -> tuple[str, ...]:
        if self.required_input_ports is not None:
            return self.required_input_ports
        return self.input_ports if self.required_inbound else ()

    @property
    def config(self) -> NodeConfigCapability:
        return NodeConfigCapability(kind=self.config_kind, fields=list(self.fields))


@dataclass(frozen=True)
class DataTypeDefinition:
    """Runtime data contract independent from a graph port identifier.

    A concrete type may declare a parent logical type. This lets values such as
    decoded, streamed, or remote audio satisfy a node that requests ``audio``
    without making every implementation inherit one application class.
    """

    type_id: str
    label: str
    is_valid: Callable[[object], bool]
    parent_type_id: str | None = None
    capabilities: frozenset[str] = frozenset()
    checkpoint_codec: CheckpointCodec | None = None
    config_value: Callable[[object], object] | None = None
    presentation: dict[str, str] | None = None


class PortDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    port_id: str
    type_id: str
    label: str
    capability_requirements: frozenset[str] = frozenset()
    # Kept as compatibility properties for existing extensions. New code
    # should validate through the referenced DataTypeDefinition.
    data_type: type = object
    is_valid: Callable[[object], bool] | None = None


def fields_from_model(
    model,
    *,
    widgets: dict[str, tuple[str, list[dict[str, object]] | None]] | None = None,
    excluded: set[str] | None = None,
    input_ports: dict[str, str] | None = None,
) -> tuple[CapabilityField, ...]:
    schema = model.model_json_schema()
    required = set(schema.get("required", ()))
    overrides = widgets or {}
    excluded = excluded or set()
    fields = []
    for key, spec in schema.get("properties", {}).items():
        if key in excluded:
            continue
        type_name = {
            "integer": "integer",
            "boolean": "boolean",
            "number": "number",
            "string": "string",
        }.get(spec.get("type"), "string")
        field_type, options = overrides.get(key, (type_name, None))
        if options is None and isinstance(spec.get("enum"), list):
            options = [{"value": value, "label": str(value).upper()} for value in spec["enum"]]
            field_type = "select"
        fields.append(
            CapabilityField(
                key=key,
                type=field_type,
                required=key in required,
                default=spec.get("default"),
                creation_default=spec.get("creation_default"),
                suggestion_source=spec.get("suggestion_source"),
                minimum=spec.get("minimum"),
                maximum=spec.get("maximum"),
                min_length=spec.get("minLength"),
                max_length=spec.get("maxLength"),
                pattern=spec.get("pattern"),
                description=spec.get("description"),
                validator="safe_filename_stem" if key == "filename_stem" else None,
                input_port=(input_ports or {}).get(key),
                input_type_id=spec.get("input_type_id"),
                widget=spec.get("widget"),
                options=options or [],
            )
        )
    return tuple(fields)
