"""Immutable public service/model bindings and one protocol authority."""

import json
from dataclasses import dataclass
from typing import Callable

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
    region: str | None = None
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


def validate_connection(context, value):
    from .node_registry import NODE_DEFINITIONS

    if not isinstance(value, ConnectionBinding) or value.project_id != context.run.project_id:
        raise NodeExecutionError("CONNECTION_BINDING_INVALID", "服务连接不属于当前项目")
    owner = next(
        (node for node in context.run.workflow.nodes if node.id == value.owner_node_id), None
    )
    definition = NODE_DEFINITIONS.get(owner.type) if owner else None
    binding = (definition.presentation or {}).get("inline_connection_input") if definition else None
    if not binding or binding["provider_type"] != "service_connection":
        raise NodeExecutionError("CONNECTION_BINDING_INVALID", "服务连接归属无效")
    config = definition.normalize_config(owner.config)
    expected = {
        "project_id": context.run.project_id,
        "owner_node_id": owner.id,
        "protocol": config["protocol"],
        "endpoint": config[binding["base_url_key"]],
        "region": config.get(binding["region_key"]) if binding["region_key"] else None,
        "credential_ref": config[binding["credential_ref_key"]],
    }
    if value.model_dump() != expected:
        raise NodeExecutionError("CONNECTION_BINDING_CHANGED", "服务连接与当前冻结工作流不一致")
    return value


def validate_model(context, value, operation):
    if not isinstance(value, ModelBinding):
        raise NodeExecutionError("MODEL_BINDING_INVALID", "模型输入无效")
    connection = validate_connection(context, value.connection)
    from .node_registry import NODE_DEFINITIONS, PORT_DEFINITIONS

    owner = next(
        (node for node in context.run.workflow.nodes if node.id == value.owner_node_id), None
    )
    definition = NODE_DEFINITIONS.get(owner.type) if owner else None
    if definition is None or not any(
        PORT_DEFINITIONS[port].type_id == "model_binding" for port in definition.output_ports
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
    return value


def resolve_model(context, value, operation, timeout_seconds):
    from ..application.workflow_connections import ConnectionSnapshotError, credential_operation
    from .inline_connection import InlineConnection

    value = validate_model(context, value, operation)
    owner = value.connection
    try:
        key = credential_operation(
            context.store,
            "read",
            owner.project_id,
            owner.owner_node_id,
            "service_connection",
            owner.credential_ref,
        )
    except ConnectionSnapshotError as error:
        raise NodeExecutionError(error.code, str(error)) from None
    connection = InlineConnection(
        provider_type=owner.protocol,
        base_url=owner.endpoint,
        default_timeout=timeout_seconds,
        region=owner.region,
    )
    return connection, value.model_name, value.additional_parameters, key
