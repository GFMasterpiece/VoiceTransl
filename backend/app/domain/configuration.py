"""Immutable public service/model bindings and one protocol authority."""

import json
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .node_execution import NodeExecutionError


@dataclass(frozen=True)
class ServiceProtocol:
    label: str
    operations: frozenset[str]
    validate_endpoint: Callable
    validate_parameters: Callable


SERVICE_PROTOCOLS: dict[str, ServiceProtocol] = {}


def register_protocol(protocol_id: str, definition: ServiceProtocol):
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]*", protocol_id) or protocol_id in SERVICE_PROTOCOLS:
        raise ValueError("invalid or duplicate service protocol")
    if (
        not definition.label
        or not definition.operations
        or not all(isinstance(operation, str) and operation for operation in definition.operations)
        or not callable(definition.validate_endpoint)
        or not callable(definition.validate_parameters)
    ):
        raise ValueError("invalid service protocol declaration")
    SERVICE_PROTOCOLS[protocol_id] = definition


def normalize_endpoint(protocol, value):
    return SERVICE_PROTOCOLS[protocol].validate_endpoint(value)


class ConnectionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    project_id: str
    owner_node_id: str
    protocol: str
    endpoint: str
    service_family: Literal["llm", "asr"]
    provider_type: str
    credential_ref: str

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value):
        if value not in SERVICE_PROTOCOLS:
            raise ValueError("服务协议未注册")
        return value


class ModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    connection: ConnectionBinding
    owner_node_id: str
    model_name: str
    parameters_json: str = "{}"

    @property
    def additional_parameters(self):
        return json.loads(self.parameters_json)


def protocol_family(protocol):
    declaration = SERVICE_PROTOCOLS.get(protocol)
    if declaration is None:
        raise ValueError("Service protocol is not registered")
    families = {
        family
        for family, operations in SERVICE_FAMILY_OPERATIONS.items()
        if declaration.operations <= operations
    }
    if len(families) != 1:
        raise ValueError("Service protocol has no unambiguous family")
    return next(iter(families))


SERVICE_FAMILY_OPERATIONS = {
    "llm": frozenset({"translation"}),
    "asr": frozenset({"asr_file", "asr_streaming"}),
}


def validate_connection(context, value, family=None):
    from .node_registry import NODE_DEFINITIONS

    if not isinstance(value, ConnectionBinding) or value.project_id != context.run.project_id:
        raise NodeExecutionError(
            "CONNECTION_BINDING_INVALID", "Service connection does not belong to this project"
        )
    owner = next(
        (node for node in context.run.workflow.nodes if node.id == value.owner_node_id), None
    )
    definition = NODE_DEFINITIONS.get(owner.type) if owner else None
    binding = (definition.presentation or {}).get("inline_connection_input") if definition else None
    if (
        not binding
        or binding.get("service_family") != value.service_family
        or (family and family != value.service_family)
    ):
        raise NodeExecutionError(
            "CONNECTION_BINDING_INVALID", "Service connection family is invalid"
        )
    config = definition.normalize_config(owner.config)
    protocol = binding["fixed_protocol"] or config[binding["protocol_key"]]
    expected = {
        "project_id": context.run.project_id,
        "owner_node_id": owner.id,
        "service_family": binding["service_family"],
        "provider_type": binding["provider_type"],
        "protocol": protocol,
        "endpoint": config[binding["base_url_key"]],
        "credential_ref": config[binding["credential_ref_key"]],
    }
    if value.model_dump() != expected or protocol_family(protocol) != value.service_family:
        raise NodeExecutionError(
            "CONNECTION_BINDING_CHANGED", "Service connection differs from the frozen graph"
        )
    return value


async def validate_model(context, value, operation):
    if not isinstance(value, ModelBinding):
        raise NodeExecutionError("MODEL_BINDING_INVALID", "模型输入无效")
    family = next(
        (
            family
            for family, operations in SERVICE_FAMILY_OPERATIONS.items()
            if operation in operations
        ),
        None,
    )
    connection = validate_connection(context, value.connection, family)
    from .node_registry import NODE_DEFINITIONS, PORT_DEFINITIONS

    owner = next(
        (node for node in context.run.workflow.nodes if node.id == value.owner_node_id), None
    )
    definition = NODE_DEFINITIONS.get(owner.type) if owner else None
    if definition is None or not any(
        PORT_DEFINITIONS[port].type_id == f"{connection.service_family}_model"
        for port in definition.output_ports
    ):
        raise NodeExecutionError("MODEL_BINDING_INVALID", "模型配置归属无效")
    protocol = SERVICE_PROTOCOLS[connection.protocol]
    if operation not in protocol.operations:
        raise NodeExecutionError("MODEL_PROTOCOL_INCOMPATIBLE", "模型协议不支持当前处理操作")
    if (
        not value.model_name
        or value.model_name != value.model_name.strip()
        or len(value.model_name) > 128
    ):
        raise NodeExecutionError("MODEL_NAME_REQUIRED", "请填写模型名称")
    protocol.validate_endpoint(connection.endpoint)
    protocol.validate_parameters(value.additional_parameters)
    port = f"{connection.service_family}_model"
    available = getattr(context, "available_outputs", None) or {}
    trusted = available.get(owner.id, {}).get(port)
    if trusted is None:
        from ..application.configuration_evaluator import evaluate_configuration_graph

        project = getattr(context, "project", None)
        if project is None and context.store is not None:
            project = context.store.load_project(context.run.project_id)
        results = await evaluate_configuration_graph(
            context.store,
            context.run,
            project,
            context.cancel_event,
            required_ids={owner.id},
            outputs=available,
            validate_processing_nodes=False,
        )
        trusted = results.get(owner.id)
        trusted = trusted.outputs.get(port) if trusted else None
    if trusted is None or trusted.value != value:
        raise NodeExecutionError(
            "MODEL_BINDING_CHANGED", "Model differs from its frozen effective configuration"
        )
    return value


async def resolve_model(context, value, operation, timeout_seconds):
    from ..application.workflow_connections import ConnectionSnapshotError, credential_operation
    from .inline_connection import InlineConnection

    value = await validate_model(context, value, operation)
    owner = value.connection
    try:
        key = credential_operation(
            context.store,
            "read",
            owner.project_id,
            owner.owner_node_id,
            owner.provider_type,
            owner.credential_ref,
        )
    except ConnectionSnapshotError as error:
        raise NodeExecutionError(error.code, str(error)) from None
    connection = InlineConnection(
        provider_type=owner.protocol,
        base_url=owner.endpoint,
        default_timeout=timeout_seconds,
    )
    return connection, value.model_name, value.additional_parameters, key
