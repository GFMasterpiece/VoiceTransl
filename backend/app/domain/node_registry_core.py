"""Generic node/type registration and catalog derivation, without built-in dispatch."""

from __future__ import annotations

import math
import re
from typing import Callable

from .checkpoint import CheckpointCodec
from .node_constants import BINDING_IDENTIFIER_PATTERN, NODE_TYPE_PATTERN, PORT_PATTERN
from .node_contracts import CapabilityField as CapabilityField
from .node_contracts import DataTypeDefinition as DataTypeDefinition
from .node_contracts import NodeCategory as NodeCategory
from .node_contracts import NodeConfigCapability as NodeConfigCapability
from .node_contracts import NodeDefinition as NodeDefinition
from .node_contracts import PortDefinition as PortDefinition
from .node_execution import NodeResult

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
    checkpoint_codec: CheckpointCodec | None = None,
    config_value: Callable[[object], object] | None = None,
    presentation: dict[str, str] | None = None,
) -> DataTypeDefinition:
    """Register a logical runtime type used by one or more ports."""

    _validate_identifier(type_id, "type id must be a lowercase identifier")
    if checkpoint_codec is not None and (
        not isinstance(checkpoint_codec, CheckpointCodec)
        or not checkpoint_codec.id
        or not checkpoint_codec.version
        or not callable(checkpoint_codec.encode)
        or not callable(checkpoint_codec.decode)
    ):
        raise ValueError("checkpoint codec must have an explicit id, version and handlers")
    if config_value is not None and not callable(config_value):
        raise ValueError("config_value must be callable")
    if presentation is not None and (
        not isinstance(presentation, dict)
        or set(presentation) != {"color"}
        or not isinstance(presentation["color"], str)
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", presentation["color"])
    ):
        raise ValueError("type presentation must declare one hex color")
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
        checkpoint_codec=checkpoint_codec,
        config_value=config_value,
        presentation=presentation,
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


def resolved_input_ports(definition, config):
    group = definition.dynamic_inputs
    if group is None:
        return definition.input_ports
    items = config.get(group["config_key"], [])
    if not isinstance(items, list):
        return definition.input_ports
    return definition.input_ports + tuple(
        group["port_prefix"] + item["id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def resolved_fields(definition, config):
    fields = list(definition.fields)
    group = definition.dynamic_inputs
    if group is not None:
        items = config.get(group["config_key"], [])
        if not isinstance(items, list):
            return tuple(fields)
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                fields.append(
                    CapabilityField.model_validate(
                        {
                            **group["field"],
                            "key": f"{group['config_key']}.{item['id']}.value",
                            "input_port": group["port_prefix"] + item["id"],
                            "input_type_id": group["type_id"],
                        }
                    )
                )
    return tuple(fields)


def resolve_port(port_id, node=None):
    if node is not None:
        definition = NODE_DEFINITIONS.get(node.type)
        group = definition.dynamic_inputs if definition else None
        if group and port_id in resolved_input_ports(definition, node.config):
            if port_id.startswith(group["port_prefix"]):
                return PortDefinition(port_id=port_id, type_id=group["type_id"], label=port_id)
    return PORT_DEFINITIONS.get(port_id)


def ports_are_compatible(
    source_port_id: str, target_port_id: str, source_node=None, target_node=None
) -> bool:
    source = resolve_port(source_port_id, source_node)
    target = resolve_port(target_port_id, target_node)
    return bool(
        source
        and target
        and type_is_compatible(source.type_id, target.type_id)
        and target.capability_requirements <= TYPE_DEFINITIONS[source.type_id].capabilities
    )


def validate_port_value(port_id: str, value: object, node=None) -> bool:
    definition = resolve_port(port_id, node)
    if definition is None:
        return False
    type_definition = TYPE_DEFINITIONS.get(definition.type_id)
    return bool(type_definition and type_definition.is_valid(value))


NODE_DEFINITIONS: dict[str, NodeDefinition] = {}


def validate_dynamic_config(definition, config):
    group = definition.dynamic_inputs
    if group is None:
        return
    items = config.get(group["config_key"])
    if not isinstance(items, list) or not group["min_items"] <= len(items) <= group["max_items"]:
        raise ValueError("invalid dynamic input count")
    seen = set()
    template = CapabilityField.model_validate(group["field"])
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "value"}
            or not isinstance(item["id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", item["id"])
            or item["id"] in seen
            or not TYPE_DEFINITIONS[group["type_id"]].is_valid(item["value"])
        ):
            raise ValueError("invalid dynamic input item")
        seen.add(item["id"])
        value = item["value"]
        if isinstance(value, str) and (
            (template.required and not value)
            or (template.min_length is not None and len(value) < template.min_length)
            or (template.max_length is not None and len(value) > template.max_length)
            or (template.pattern is not None and not re.search(template.pattern, value))
        ):
            raise ValueError("invalid dynamic input value")


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
            "presentation": definition.presentation,
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
    if "provider_profile_input" in presentation:
        raise ValueError("provider profile declarations are no longer supported")
    inline = _inline_connection_binding(definition)
    if inline is not None:
        presentation["inline_connection_input"] = inline
    return presentation


def _inline_connection_binding(definition):
    binding = (definition.presentation or {}).get("inline_connection_input")
    if binding is None:
        return None
    roles = {"base_url_key", "timeout_key", "region_key", "credential_ref_key"}
    required = roles | {"provider_type", "label"}
    if (
        not isinstance(binding, dict)
        or not required <= set(binding)
        or set(binding) - (required | {"data_handling"})
    ):
        raise ValueError("invalid inline connection declaration")
    if any(
        not (key in {"region_key", "timeout_key"} and binding[key] is None)
        and (
            not isinstance(binding[key], str)
            or not re.fullmatch(BINDING_IDENTIFIER_PATTERN, binding[key])
        )
        for key in roles | {"provider_type"}
    ):
        raise ValueError("invalid inline connection declaration")
    label = binding["label"]
    if (
        not isinstance(label, str)
        or not 1 <= len(label) <= 128
        or label != label.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in label)
    ):
        raise ValueError("invalid inline connection label")
    bound_roles = {role for role in roles if binding[role] is not None}
    if len({binding[key] for key in bound_roles}) != len(bound_roles):
        raise ValueError("inline connection fields must be distinct")
    fields = {item.key: item for item in definition.fields}
    for role in bound_roles:
        item = fields.get(binding[role])
        expected = "integer" if role == "timeout_key" else "string"
        if item is None or item.type != expected or item.input_port is not None:
            raise ValueError("inline connection fields cannot be connected parameters")
    result = dict(binding)
    if "data_handling" in binding:
        handling = binding["data_handling"]
        if (
            not isinstance(handling, dict)
            or not {"temporary_upload_retention_hours", "remote_cancel_pending_only"}
            <= set(handling)
            or set(handling)
            - {"temporary_upload_retention_hours", "remote_cancel_pending_only", "cancel_behavior"}
            or type(handling["remote_cancel_pending_only"]) is not bool
            or ("cancel_behavior" in handling and handling["cancel_behavior"] != "close_stream")
        ):
            raise ValueError("invalid inline data handling declaration")
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
    group = definition.dynamic_inputs
    if group is not None:
        if (
            set(group)
            != {
                "config_key",
                "port_prefix",
                "type_id",
                "min_items",
                "max_items",
                "field",
                "default_items",
            }
            or group["type_id"] != "string"
            or not isinstance(group["config_key"], str)
            or not re.fullmatch(PORT_PATTERN, group["config_key"])
            or any(item.key == group["config_key"] for item in definition.fields)
            or not isinstance(group["port_prefix"], str)
            or len(group["port_prefix"]) > 32
            or not re.fullmatch(r"[a-z][a-z0-9_]*_", group["port_prefix"])
            or type(group["min_items"]) is not int
            or type(group["max_items"]) is not int
            or not 1 <= group["min_items"] <= group["max_items"] <= 32
        ):
            raise ValueError("invalid dynamic input declaration")
        template = CapabilityField.model_validate(group["field"])
        if (
            template.type != group["type_id"]
            or template.input_port is not None
            or template.nullable
            or not isinstance(template.default, str)
        ):
            raise ValueError("invalid dynamic input field template")
        if any(
            port.startswith(group["port_prefix"])
            for port in (*definition.input_ports, *definition.output_ports)
        ):
            raise ValueError("dynamic inputs overlap fixed node ports")
        validate_dynamic_config(definition, {group["config_key"]: group["default_items"]})
        validate_dynamic_config(
            definition,
            {
                group["config_key"]: [
                    {"id": f"default_{index}", "value": template.default}
                    for index in range(group["min_items"])
                ]
            },
        )
    if definition.evaluation_kind not in {"configuration", "processing"}:
        raise ValueError("invalid evaluation kind")
    if definition.can_start_without_inputs and definition.evaluation_kind != "configuration":
        raise ValueError("only configuration nodes may start without inputs")
    if definition.evaluation_kind == "configuration" and not callable(definition.evaluate_config):
        raise ValueError("configuration nodes require evaluate_config")
    if any(port not in definition.input_ports for port in definition.binding_inputs):
        raise ValueError("binding inputs must name declared ports")
    if type(definition.debug_only) is not bool:
        raise ValueError("node debug_only must be a boolean")
    if not isinstance(definition.execution_version, str) or not definition.execution_version:
        raise ValueError("node execution version must be a non-empty string")
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
    if "provider_profile_input" in (definition.presentation or {}):
        raise ValueError("provider profile declarations are no longer supported")
    _inline_connection_binding(definition)
    bound_ports = [item.input_port for item in definition.fields if item.input_port is not None]
    if len(set(bound_ports)) != len(bound_ports):
        raise ValueError("parameter input bindings must be unique")
    for item in definition.fields:
        if item.widget is not None and (
            item.type != "string" or item.suggestion_source is not None
        ):
            raise ValueError("multiline widget requires a plain string")
        if item.suggestion_source is not None and (
            item.suggestion_source != "languages" or item.type != "string"
        ):
            raise ValueError("invalid field suggestion source")
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
        if item.input_type_id is not None:
            expected_type = item.input_type_id
        if expected_type == "select":
            if not item.options or any(
                not isinstance(option.get("value"), str) for option in item.options
            ):
                raise ValueError("connectable select fields require non-empty string options")
            expected_type = "string"
        if (
            port is None
            or expected_type not in TYPE_DEFINITIONS
            or not type_is_compatible(port.type_id, expected_type)
        ):
            raise ValueError("parameter input type must match its configuration field")
    if not definition.terminal and not definition.output_ports:
        raise ValueError("non-terminal nodes require at least one output port")
    if not set(definition.settings_default_fields) <= set(field_keys):
        raise ValueError("settings defaults must reference declared fields")
    binding = (definition.presentation or {}).get("inline_connection_input")
    if binding and binding["credential_ref_key"] in definition.settings_default_fields:
        raise ValueError("credential references cannot be settings defaults")
    for port in all_ports:
        if port not in PORT_DEFINITIONS:
            raise ValueError(f"port has no registered runtime type: {port}")
    if definition.normalize_config is not None and not callable(definition.normalize_config):
        raise ValueError("normalize_config must be callable")
    if definition.validate_config is not None and not callable(definition.validate_config):
        raise ValueError("validate_config must be callable")
    if definition.validate_execution_config is not None and not callable(
        definition.validate_execution_config
    ):
        raise ValueError("validate_execution_config must be callable")
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
        actual = TYPE_DEFINITIONS.get(data.type_id or port_definition.type_id)
        if (
            actual is None
            or not type_is_compatible(actual.type_id, port_definition.type_id)
            or not actual.is_valid(data.value)
            or not validate_port_value(port, data.value)
        ):
            raise TypeError(f"node output {port} has an invalid runtime type")
    return result


def node_catalog() -> list[dict[str, object]]:
    _validate_categories(NODE_DEFINITIONS.values())
    return [
        {
            "type": item.type,
            "label": item.label,
            "debug_only": item.debug_only,
            "evaluation_kind": item.evaluation_kind,
            "can_start_without_inputs": item.can_start_without_inputs,
            "dynamic_inputs": item.dynamic_inputs,
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
