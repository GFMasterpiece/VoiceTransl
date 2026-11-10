"""Separate service ownership and reusable models for language and speech APIs."""

import json

from pydantic import BaseModel, ConfigDict, Field

from ..domain.configuration import (
    SERVICE_PROTOCOLS,
    ConnectionBinding,
    ModelBinding,
    normalize_endpoint,
    protocol_family,
)
from ..domain.node_categories import LLM, SPEECH_RECOGNITION
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionError, NodeResult


class LLMServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    endpoint: str = Field(default="", max_length=2048)
    credential_ref: str | None = Field(default=None, max_length=128)


class ASRServiceConfig(LLMServiceConfig):
    protocol: str = "qwen_filetrans"


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_name: str = Field(default="", max_length=128)
    additional_parameters: dict[str, object] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )


def service_normalizer(family):
    def normalize(config):
        try:
            value = (
                (LLMServiceConfig if family == "llm" else ASRServiceConfig)
                .model_validate(config)
                .model_dump(mode="json")
            )
            protocol = "openai_chat" if family == "llm" else value["protocol"]
            if protocol_family(protocol) != family:
                raise ValueError
        except ValueError:
            raise ValueError("服务连接配置无效") from None
        if value["endpoint"]:
            value["endpoint"] = normalize_endpoint(protocol, value["endpoint"])
        return value

    return normalize


normalize_llm_service = service_normalizer("llm")
normalize_asr_service = service_normalizer("asr")


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


def service_executor(family, normalize):
    async def evaluate(context):
        from ..domain.node_registry import NODE_DEFINITIONS

        config = normalize(context.node.config)
        if not config["endpoint"]:
            raise NodeExecutionError("CONNECTION_ENDPOINT_REQUIRED", "请填写服务地址")
        if not config["credential_ref"]:
            raise NodeExecutionError("CREDENTIAL_REQUIRED", "请在服务节点保存 API Key")
        binding = NODE_DEFINITIONS[context.node.type].presentation["inline_connection_input"]
        protocol = binding["fixed_protocol"] or config[binding["protocol_key"]]
        value = ConnectionBinding(
            project_id=context.run.project_id,
            owner_node_id=context.node.id,
            service_family=family,
            provider_type=binding["provider_type"],
            protocol=protocol,
            endpoint=config["endpoint"],
            credential_ref=config["credential_ref"],
        )
        result = NodeResult()
        result.add(f"{family}_connection", value)
        return result

    return evaluate


def model_executor(family):
    async def evaluate(context):
        from ..domain.configuration import validate_connection

        config = normalize_model(context.node.config)
        connection = validate_connection(
            context, context.input(f"{family}_connection").value, family
        )
        if not config["model_name"]:
            raise NodeExecutionError("MODEL_NAME_REQUIRED", "请填写模型名称")
        parameters = SERVICE_PROTOCOLS[connection.protocol].validate_parameters(
            config["additional_parameters"]
        )
        result = NodeResult()
        result.add(
            f"{family}_model",
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

    return evaluate


def definitions():
    values = []
    for family, category, label in (("llm", LLM, "LLM"), ("asr", SPEECH_RECOGNITION, "ASR")):
        config_model = LLMServiceConfig if family == "llm" else ASRServiceConfig
        normalize = service_normalizer(family)
        execute = service_executor(family, normalize)
        widgets = (
            {
                "protocol": (
                    "select",
                    [
                        {"value": key, "label": value.label}
                        for key, value in SERVICE_PROTOCOLS.items()
                        if protocol_family(key) == family
                    ],
                )
            }
            if family == "asr"
            else {}
        )
        values.append(
            NodeDefinition(
                type=f"{family}_service",
                label=label + "服务",
                category=category,
                input_ports=(),
                output_ports=(f"{family}_connection",),
                required_input_ports=(),
                terminal=False,
                config_kind="object",
                fields=fields_from_model(config_model, widgets=widgets),
                normalize_config=normalize,
                validate_config=normalize,
                evaluation_kind="configuration",
                can_start_without_inputs=True,
                evaluate_config=execute,
                execute=execute,
                settings_default_fields=tuple(
                    key for key in config_model.model_fields if key != "credential_ref"
                ),
                execution_version="1",
                presentation={
                    "inline_connection_input": {
                        "provider_type": f"{family}_service",
                        "label": label + "服务",
                        "base_url_key": "endpoint",
                        "timeout_key": None,
                        "region_key": None,
                        "credential_ref_key": "credential_ref",
                        "service_family": family,
                        "fixed_protocol": "openai_chat" if family == "llm" else None,
                        "protocol_key": None if family == "llm" else "protocol",
                    }
                },
            )
        )
        execute_model = model_executor(family)
        values.append(
            NodeDefinition(
                type=f"{family}_model",
                label=label + "模型",
                category=category,
                input_ports=(f"{family}_connection", "model_name", "additional_parameters"),
                output_ports=(f"{family}_model",),
                required_input_ports=(f"{family}_connection",),
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
                evaluate_config=execute_model,
                execute=execute_model,
                settings_default_fields=("model_name", "additional_parameters"),
            )
        )
    return tuple(values)
