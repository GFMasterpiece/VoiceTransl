"""Generic node/type registration and catalog derivation, without built-in dispatch."""

from __future__ import annotations

import math
import re
from typing import Callable

from .node_constants import BINDING_IDENTIFIER_PATTERN, NODE_TYPE_PATTERN, PORT_PATTERN
from .node_contracts import CapabilityField as CapabilityField
from .node_contracts import DataTypeDefinition as DataTypeDefinition
from .node_contracts import NodeCategory as NodeCategory
from .node_contracts import NodeConfigCapability as NodeConfigCapability
from .node_contracts import NodeDefinition as NodeDefinition
from .node_contracts import PortDefinition as PortDefinition
from .node_execution import NodeResult
from .provider_profile import ProviderProfile

TYPE_DEFINITIONS: dict[str, DataTypeDefinition] = {}
PORT_DEFINITIONS: dict[str, PortDefinition] = {}


def _validate_identifier(value: str, message: str) -> None:
    if not re.fullmatch(PORT_PATTERN, value):
        raise ValueError(message)


def register_type(
    type_id: str,
    *,
    label: str | None = None,
    validator: Callable[[object], bool] | None = None,
    data_type: type = object,
    parent_type_id: str | None = None,
    capabilities: set[str] | frozenset[str] = frozenset(),
) -> DataTypeDefinition:
    """Register a logical runtime type used by one or more ports."""

    _validate_identifier(type_id, "type id must be a lowercase identifier")
    if parent_type_id is not None and parent_type_id not in TYPE_DEFINITIONS:
        raise ValueError(f"parent type is not registered: {parent_type_id}")
    if type_id in TYPE_DEFINITIONS:
        raise ValueError("type already registered")
    check = validator or (lambda value: isinstance(value, data_type))
    definition = DataTypeDefinition(
        type_id=type_id,
        label=label or type_id,
        is_valid=check,
        parent_type_id=parent_type_id,
        capabilities=frozenset(capabilities),
    )
    TYPE_DEFINITIONS[type_id] = definition
    return definition


def register_port(
    port_id: str,
    data_type: type = object,
    *,
    type_id: str | None = None,
    label: str | None = None,
    validator: Callable[[object], bool] | None = None,
    capability_requirements: set[str] | frozenset[str] = frozenset(),
) -> PortDefinition:
    """Register a stable port ID and bind it to a logical data type.

    ``data_type`` and ``validator`` remain accepted so older node extensions
    continue to work while migrating to an explicit ``type_id``.
    """

    _validate_identifier(port_id, "port name must be a lowercase identifier")
    if port_id in PORT_DEFINITIONS:
        raise ValueError("port already registered")
    resolved_type_id = type_id or port_id
    if resolved_type_id not in TYPE_DEFINITIONS:
        register_type(
            resolved_type_id,
            label=resolved_type_id,
            validator=validator,
            data_type=data_type,
        )
    elif validator is not None or data_type is not object:
        raise ValueError("registered types own their runtime validator")
    type_definition = TYPE_DEFINITIONS[resolved_type_id]
    definition = PortDefinition(
        port_id=port_id,
        type_id=resolved_type_id,
        label=label or type_definition.label,
        capability_requirements=frozenset(capability_requirements),
        data_type=data_type,
        is_valid=type_definition.is_valid,
    )
    PORT_DEFINITIONS[port_id] = definition
    return definition


def type_is_compatible(source_type_id: str, target_type_id: str) -> bool:
    """Return whether a source value can be consumed by a target type."""

    if source_type_id == target_type_id:
        return True
    current = TYPE_DEFINITIONS.get(source_type_id)
    visited: set[str] = set()
    while current is not None and current.type_id not in visited:
        visited.add(current.type_id)
        if current.parent_type_id == target_type_id:
            return True
        current = TYPE_DEFINITIONS.get(current.parent_type_id or "")
    return False


def ports_are_compatible(source_port_id: str, target_port_id: str) -> bool:
    source = PORT_DEFINITIONS.get(source_port_id)
    target = PORT_DEFINITIONS.get(target_port_id)
    return bool(
        source
        and target
        and type_is_compatible(source.type_id, target.type_id)
        and target.capability_requirements <= TYPE_DEFINITIONS[source.type_id].capabilities
    )


def validate_port_value(port_id: str, value: object) -> bool:
    definition = PORT_DEFINITIONS.get(port_id)
    if definition is None:
        return False
    type_definition = TYPE_DEFINITIONS.get(definition.type_id)
    return bool(type_definition and type_definition.is_valid(value))


NODE_DEFINITIONS: dict[str, NodeDefinition] = {}


def node_ports():
    return {
        node_type: (frozenset(item.input_ports), frozenset(item.output_ports))
        for node_type, item in NODE_DEFINITIONS.items()
    }


def output_node_types():
    return frozenset(node_type for node_type, item in NODE_DEFINITIONS.items() if item.terminal)


def workflow_ports() -> tuple[str, ...]:
    """Return the live protocol ports in registration order."""

    return tuple(PORT_DEFINITIONS)


def port_catalog() -> list[dict[str, str]]:
    """Publish stable port IDs separately from their logical data types."""

    return [
        {
            "id": definition.port_id,
            "type_id": definition.type_id,
            "label": definition.label,
            "capability_requirements": sorted(definition.capability_requirements),
        }
        for definition in PORT_DEFINITIONS.values()
    ]


def type_catalog() -> list[dict[str, object]]:
    """Publish logical types so clients can mirror compatibility feedback."""

    return [
        {
            "id": definition.type_id,
            "label": definition.label,
            "parent_type_id": definition.parent_type_id,
            "capabilities": sorted(definition.capabilities),
        }
        for definition in TYPE_DEFINITIONS.values()
    ]


def _resource_binding(definition: NodeDefinition):
    binding = (definition.presentation or {}).get("resource_input")
    if binding is None:
        return None
    if (
        not isinstance(binding, dict)
        or set(binding) - {"resource_type", "config_key", "collection_key"}
        or any(
            not isinstance(binding.get(key), str) or not binding[key]
            for key in ("resource_type", "config_key")
        )
    ):
        raise ValueError("invalid resource input declaration")
    collection_key = binding.get("collection_key")
    if collection_key is not None and (
        not isinstance(collection_key, str)
        or not collection_key
        or collection_key == binding["config_key"]
    ):
        raise ValueError("invalid resource collection key")
    fields = {item.key: item for item in definition.fields}
    single = fields.get(binding["config_key"])
    collection = fields.get(collection_key) if collection_key is not None else None
    if (
        single is None
        or single.type != "string"
        or (collection_key is not None and (collection is None or collection.type != "json"))
    ):
        raise ValueError("resource bindings require declared string/json fields")
    return {**binding, "collection_key": collection_key}


def _catalog_presentation(definition):
    if definition.presentation is None:
        return None
    presentation = dict(definition.presentation)
    binding = _resource_binding(definition)
    if binding is not None:
        presentation["resource_input"] = binding
    profile_binding = _provider_profile_binding(definition)
    if profile_binding is not None:
        presentation["provider_profile_input"] = profile_binding
    inline = _inline_connection_binding(definition)
    if inline is not None:
        presentation["inline_connection_input"] = inline
    return presentation


def _inline_connection_binding(definition):
    binding = (definition.presentation or {}).get("inline_connection_input")
    if binding is None:
        return None
    roles = {
        "mode_key",
        "base_url_key",
        "timeout_key",
        "region_key",
        "api_key_env_key",
        "credential_ref_key",
    }
    if (
        not isinstance(binding, dict)
        or set(binding) != roles | {"mode_value"}
        or any(
            not isinstance(value, str) or not re.fullmatch(BINDING_IDENTIFIER_PATTERN, value)
            for value in binding.values()
        )
    ):
        raise ValueError("invalid inline connection declaration")
    if len({binding[key] for key in roles}) != len(roles):
        raise ValueError("inline connection fields must be distinct")
    fields = {item.key: item for item in definition.fields}
    for role in roles:
        item = fields.get(binding[role])
        expected = (
            "integer" if role == "timeout_key" else "select" if role == "mode_key" else "string"
        )
        if item is None or item.type != expected or item.input_port is not None:
            raise ValueError("inline connection fields cannot be connected parameters")
    mode = fields[binding["mode_key"]]
    if not any(option.get("value") == binding["mode_value"] for option in mode.options):
        raise ValueError("inline connection mode must be a declared option")
    if _provider_profile_binding(definition) is None:
        raise ValueError("inline connections require a declared provider type")
    return dict(binding)


def _provider_profile_binding(definition):
    binding = (definition.presentation or {}).get("provider_profile_input")
    if binding is None:
        return None
    if (
        not isinstance(binding, dict)
        or set(binding) - {"config_key", "provider_type", "label", "defaults", "data_handling"}
        or not {"config_key", "provider_type", "label", "defaults"} <= set(binding)
        or any(
            not isinstance(binding.get(key), str) or not binding[key].strip()
            for key in ("config_key", "provider_type", "label")
        )
    ):
        raise ValueError("invalid provider profile input declaration")
    defaults = binding["defaults"]
    if (
        not isinstance(defaults, dict)
        or set(defaults) != {"base_url", "api_key_env", "default_timeout"}
        or not isinstance(defaults["base_url"], str)
        or not isinstance(defaults["api_key_env"], str)
        or type(defaults["default_timeout"]) is not int
    ):
        raise ValueError("invalid provider profile defaults")
    ProviderProfile(
        id="connection_defaults",
        provider_type=binding["provider_type"],
        display_name=binding["label"],
        **defaults,
    )
    field = next((item for item in definition.fields if item.key == binding["config_key"]), None)
    if field is None or field.type != "string":
        raise ValueError("provider profile binding requires a declared string field")
    if field.input_port is not None:
        raise ValueError("provider profile bindings cannot be connected parameters")
    result = {**binding, "defaults": dict(defaults)}
    if "data_handling" in binding:
        handling = binding["data_handling"]
        if (
            not isinstance(handling, dict)
            or set(handling) != {"temporary_upload_retention_hours", "remote_cancel_pending_only"}
            or type(handling["remote_cancel_pending_only"]) is not bool
        ):
            raise ValueError("invalid provider data handling declaration")
        hours = handling["temporary_upload_retention_hours"]
        try:
            valid = hours is None or (
                type(hours) in {int, float} and hours > 0 and math.isfinite(hours)
            )
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("upload retention hours must be a positive finite number or null")
        result["data_handling"] = dict(handling)
    return result


def _category(definition):
    value = definition.category
    if value is None:
        return None
    if (
        not isinstance(value, NodeCategory)
        or not isinstance(value.id, str)
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value.id)
        or not isinstance(value.label, str)
        or not 1 <= len(value.label) <= 128
        or value.label != value.label.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value.label)
    ):
        raise ValueError("invalid node category declaration")
    return {"id": value.id, "label": value.label}


def _validate_categories(definitions):
    labels = {}
    for definition in definitions:
        category = _category(definition)
        if category is None:
            continue
        previous = labels.setdefault(category["id"], category["label"])
        if previous != category["label"]:
            raise ValueError("node category ID has conflicting labels")


def validate_node_definition(definition: NodeDefinition) -> None:
    _validate_categories((*NODE_DEFINITIONS.values(), definition))
    if not re.fullmatch(NODE_TYPE_PATTERN, definition.type):
        raise ValueError("node type must be a lowercase identifier")
    all_ports = set(definition.input_ports) | set(definition.output_ports)
    if any(not re.fullmatch(PORT_PATTERN, port) for port in all_ports):
        raise ValueError("node ports must be lowercase identifiers")
    if len(set(definition.input_ports)) != len(definition.input_ports):
        raise ValueError("node input ports must be unique")
    if len(set(definition.output_ports)) != len(definition.output_ports):
        raise ValueError("node output ports must be unique")
    if definition.required_inbound < 0:
        raise ValueError("required_inbound must not be negative")
    if definition.max_instances is not None and definition.max_instances < 1:
        raise ValueError("max_instances must be positive")
    if len(set(definition.required_ports)) != len(definition.required_ports):
        raise ValueError("required input ports must be unique")
    if not set(definition.required_ports) <= set(definition.input_ports):
        raise ValueError("required input ports must be declared inputs")
    if definition.is_source and definition.input_ports:
        raise ValueError("source nodes cannot declare graph input ports")
    if definition.is_parameter_source and (
        definition.is_source
        or definition.input_ports
        or definition.terminal
        or (definition.presentation or {}).get("resource_input") is not None
    ):
        raise ValueError("parameter sources must be independent non-terminal roots")
    field_keys = [item.key for item in definition.fields]
    if len(set(field_keys)) != len(field_keys):
        raise ValueError("configuration field keys must be unique")
    resource_input = _resource_binding(definition)
    _provider_profile_binding(definition)
    _inline_connection_binding(definition)
    bound_ports = [item.input_port for item in definition.fields if item.input_port is not None]
    if len(set(bound_ports)) != len(bound_ports):
        raise ValueError("parameter input bindings must be unique")
    for item in definition.fields:
        if item.input_port is None:
            continue
        if resource_input and item.key in {
            resource_input["config_key"],
            resource_input["collection_key"],
        }:
            raise ValueError("resource bindings cannot be connected parameters")
        if item.input_port not in definition.input_ports:
            raise ValueError("parameter binding must reference a declared input port")
        if item.input_port in definition.required_ports:
            raise ValueError("parameter input ports must be optional")
        port = PORT_DEFINITIONS.get(item.input_port)
        expected_type = item.type
        if expected_type == "select":
            if not item.options or any(
                not isinstance(option.get("value"), str) for option in item.options
            ):
                raise ValueError("connectable select fields require non-empty string options")
            expected_type = "string"
        if (
            port is None
            or expected_type not in {"string", "integer", "number", "boolean"}
            or not type_is_compatible(port.type_id, expected_type)
        ):
            raise ValueError("parameter input type must match its configuration field")
    if not definition.terminal and not definition.output_ports:
        raise ValueError("non-terminal nodes require at least one output port")
    for port in all_ports:
        if port not in PORT_DEFINITIONS:
            raise ValueError(f"port has no registered runtime type: {port}")
    if definition.normalize_config is not None and not callable(definition.normalize_config):
        raise ValueError("normalize_config must be callable")
    if definition.validate_config is not None and not callable(definition.validate_config):
        raise ValueError("validate_config must be callable")
    if definition.execute is not None and not callable(definition.execute):
        raise ValueError("execute must be callable")
    if definition.execute is None:
        raise ValueError("executable nodes require execute")


def register_node(definition: NodeDefinition) -> NodeDefinition:
    """Register a node type; catalogs read the live registry on each request."""
    validate_node_definition(definition)
    if definition.type in NODE_DEFINITIONS:
        raise ValueError("node type already registered")
    NODE_DEFINITIONS[definition.type] = definition
    return definition


def validate_node_result(definition: NodeDefinition, result: NodeResult) -> NodeResult:
    if not isinstance(result, NodeResult):
        raise TypeError("node executor must return NodeResult")
    if set(result.outputs) != set(definition.output_ports):
        raise ValueError("node result must return every declared output port exactly once")
    for port, data in result.outputs.items():
        if data.port != port:
            raise ValueError("node result port key does not match NodeData.port")
        port_definition = PORT_DEFINITIONS.get(port)
        if port_definition is None:
            raise TypeError(f"node output {port} has no registered runtime type")
        if not validate_port_value(port, data.value):
            raise TypeError(f"node output {port} has an invalid runtime type")
    return result


def node_catalog() -> list[dict[str, object]]:
    _validate_categories(NODE_DEFINITIONS.values())
    return [
        {
            "type": item.type,
            "label": item.label,
            "category": _category(item),
            "input_ports": list(item.input_ports),
            "output_ports": list(item.output_ports),
            "terminal": item.terminal,
            "is_source": item.is_source,
            "is_parameter_source": item.is_parameter_source,
            "max_instances": item.max_instances,
            "required_inbound": len(item.required_ports),
            "required_input_ports": list(item.required_ports),
            "removable": item.removable,
            "presentation": _catalog_presentation(item),
            "config": NodeConfigCapability(
                kind=item.config_kind, fields=list(item.fields)
            ).model_dump(mode="json"),
        }
        for item in NODE_DEFINITIONS.values()
    ]
