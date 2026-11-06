"""Generic node/type registration and catalog derivation, without built-in dispatch."""

from __future__ import annotations

import re
from typing import Callable

from .node_constants import NODE_TYPE_PATTERN, PORT_PATTERN
from .node_contracts import CapabilityField as CapabilityField
from .node_contracts import DataTypeDefinition as DataTypeDefinition
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


def validate_node_definition(definition: NodeDefinition) -> None:
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
    return [
        {
            "type": item.type,
            "label": item.label,
            "input_ports": list(item.input_ports),
            "output_ports": list(item.output_ports),
            "terminal": item.terminal,
            "is_source": item.is_source,
            "max_instances": item.max_instances,
            "required_inbound": len(item.required_ports),
            "required_input_ports": list(item.required_ports),
            "removable": item.removable,
            "presentation": item.presentation,
            "config": NodeConfigCapability(
                kind=item.config_kind, fields=list(item.fields)
            ).model_dump(mode="json"),
        }
        for item in NODE_DEFINITIONS.values()
    ]
