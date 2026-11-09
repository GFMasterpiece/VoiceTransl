"""Explicit service ownership and reusable model configuration."""

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.configuration import (
    SERVICE_PROTOCOLS,
    ConnectionBinding,
    ModelBinding,
    normalize_endpoint,
)
from ..domain.node_categories import PARAMETERS
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionError, NodeResult


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol: str = "openai_chat"
    endpoint: str = Field(default="", max_length=2048)
    region: str | None = Field(default=None, max_length=64)
    credential_ref: str | None = Field(default=None, max_length=128)

    @field_validator("protocol")
    @classmethod
    def protocol_registered(cls, value):
        if value not in SERVICE_PROTOCOLS:
            raise ValueError("服务协议未注册")
        return value


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_name: str = Field(default="", max_length=128)
    additional_parameters: dict[str, object] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )


def normalize_service(config):
    try:
        value = ServiceConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        raise ValueError("服务连接配置无效") from None
    if value["endpoint"]:
        value["endpoint"] = normalize_endpoint(value["protocol"], value["endpoint"])
    return value


def normalize_model(config):
    from ..domain.context_values import canonical_object

    try:
        value = ModelConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        raise ValueError("模型配置无效") from None
    if value["model_name"] != value["model_name"].strip() or any(
        ord(char) < 32 for char in value["model_name"]
    ):
        raise ValueError("模型名称无效")
    canonical_object(value["additional_parameters"])
    return value


async def service_evaluate(context):
    config = normalize_service(context.node.config)
    if not config["endpoint"]:
        raise NodeExecutionError("CONNECTION_ENDPOINT_REQUIRED", "请填写服务地址")
    if not config["credential_ref"]:
        raise NodeExecutionError("CREDENTIAL_REQUIRED", "请在服务连接节点保存 API Key")
    value = ConnectionBinding(
        project_id=context.run.project_id, owner_node_id=context.node.id, **config
    )
    result = NodeResult()
    result.add("connection", value)
    return result


async def model_evaluate(context):
    from ..domain.configuration import validate_connection

    config = normalize_model(context.node.config)
    connection = validate_connection(context, context.input("connection").value)
    if not config["model_name"]:
        raise NodeExecutionError("MODEL_NAME_REQUIRED", "请填写模型名称")
    parameters = SERVICE_PROTOCOLS[connection.protocol].validate_parameters(
        config["additional_parameters"]
    )
    result = NodeResult()
    result.add(
        "model",
        ModelBinding(
            connection=connection,
            owner_node_id=context.node.id,
            model_name=config["model_name"],
            parameters_json=json.dumps(
                parameters,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
        ),
    )
    return result


def definitions():
    return (
        NodeDefinition(
            type="service_connection",
            label="服务连接",
            category=PARAMETERS,
            input_ports=(),
            output_ports=("connection",),
            required_input_ports=(),
            terminal=False,
            config_kind="object",
            fields=fields_from_model(
                ServiceConfig,
                widgets={
                    "protocol": (
                        "select",
                        [
                            {"value": key, "label": value.label}
                            for key, value in SERVICE_PROTOCOLS.items()
                        ],
                    )
                },
            ),
            normalize_config=normalize_service,
            validate_config=normalize_service,
            evaluation_kind="configuration",
            can_start_without_inputs=True,
            evaluate_config=service_evaluate,
            execute=service_evaluate,
            settings_default_fields=("protocol", "endpoint", "region"),
            presentation={
                "inline_connection_input": {
                    "provider_type": "service_connection",
                    "label": "服务连接",
                    "base_url_key": "endpoint",
                    "timeout_key": None,
                    "region_key": "region",
                    "credential_ref_key": "credential_ref",
                }
            },
        ),
        NodeDefinition(
            type="model_config",
            label="模型配置",
            category=PARAMETERS,
            input_ports=("connection", "model_name", "additional_parameters"),
            output_ports=("model",),
            required_input_ports=("connection",),
            terminal=False,
            config_kind="object",
            fields=fields_from_model(
                ModelConfig,
                widgets={"additional_parameters": ("json", None)},
                input_ports={
                    "model_name": "model_name",
                    "additional_parameters": "additional_parameters",
                },
            ),
            normalize_config=normalize_model,
            validate_config=normalize_model,
            evaluation_kind="configuration",
            evaluate_config=model_evaluate,
            execute=model_evaluate,
            settings_default_fields=("model_name", "additional_parameters"),
        ),
    )
