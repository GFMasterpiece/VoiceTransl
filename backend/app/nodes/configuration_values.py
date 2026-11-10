"""Composable, deterministic configuration values and their explicit codecs."""

from dataclasses import replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.checkpoint import CheckpointCodec
from ..domain.context_values import (
    CONFIGURATION_TYPES,
    ASROptions,
    ASROptionsFields,
    JSONValue,
    TranslationOptions,
    TranslationOptionsFields,
    canonical_object,
    validate_text,
)
from ..domain.node_categories import PARAMETERS
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionError, NodeResult


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JSONValueConfig(_Config):
    value: dict[str, object] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )

    @field_validator("value")
    @classmethod
    def validate_object(cls, value):
        canonical_object(value)
        return value


class TextJoinItem(_Config):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    value: str = Field(default="", json_schema_extra={"widget": "multiline"})

    @field_validator("value")
    @classmethod
    def validate_value(cls, value):
        return validate_text(value)


class TextJoinConfig(_Config):
    items: list[TextJoinItem] = Field(
        default_factory=lambda: [TextJoinItem(id="a1"), TextJoinItem(id="a2")],
        min_length=1,
        max_length=32,
    )
    separator: str = Field(default="\n\n", json_schema_extra={"widget": "multiline"})

    @field_validator("separator")
    @classmethod
    def validate_separator(cls, value):
        return validate_text(value)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("拼接输入ID不能重复")
        return self


class JSONMergeConfig(_Config):
    left: dict[str, object] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )
    right: dict[str, object] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )
    conflict_policy: Literal["error", "left_wins", "right_wins"] = "error"

    @field_validator("left", "right")
    @classmethod
    def validate_object(cls, value):
        canonical_object(value)
        return value


def _definition(node_type, label, config_model, output, build, ports=None, *, check=None):
    ports = ports or {}

    def normalize(config):
        try:
            normalized = config_model.model_validate(config).model_dump(mode="json")
            if check is not None:
                check(normalized)
            return normalized
        except ValueError:
            raise ValueError("配置值无效，请检查字段类型、文本、JSON结构及大小限制") from None

    async def execute(context):
        value = build(normalize(context.node.config))
        result = NodeResult()
        result.add(output, value)
        return result

    return NodeDefinition(
        type=node_type,
        label=label,
        category=PARAMETERS,
        input_ports=tuple(ports.values()),
        required_input_ports=(),
        output_ports=(output,),
        terminal=False,
        config_kind=node_type,
        fields=fields_from_model(
            config_model,
            widgets={
                key: ("json", None)
                for key in ("left", "right", "value", "glossary")
                if key in config_model.model_fields
                and config_model.model_fields[key].annotation is not str
            },
            input_ports=ports,
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        can_start_without_inputs=True,
        evaluation_kind="configuration",
        evaluate_config=execute,
        settings_default_fields=tuple(config_model.model_fields),
        execution_version="2",
    )


def _join(config):
    return validate_text(config["separator"].join(item["value"] for item in config["items"]))


def _merge(config):
    left, right = config["left"], config["right"]
    if config["conflict_policy"] == "error" and left.keys() & right.keys():
        raise NodeExecutionError("JSON_MERGE_CONFLICT", "JSON对象存在重复键，请明确选择合并规则")
    merged = {**right, **left} if config["conflict_policy"] == "left_wins" else {**left, **right}
    return JSONValue.from_object(merged)


def builtin_definitions():
    return (
        _definition(
            "json_value",
            "JSON对象",
            JSONValueConfig,
            "json",
            lambda config: JSONValue.from_object(config["value"]),
        ),
        replace(
            _definition(
                "text_join",
                "文本拼接",
                TextJoinConfig,
                "string",
                _join,
                {"separator": "separator"},
                check=_join,
            ),
            fields=fields_from_model(
                TextJoinConfig, excluded={"items"}, input_ports={"separator": "separator"}
            ),
            settings_default_fields=("separator",),
            dynamic_inputs={
                "config_key": "items",
                "port_prefix": "text_",
                "type_id": "string",
                "min_items": 1,
                "max_items": 32,
                "default_items": TextJoinConfig().model_dump(mode="json")["items"],
                "field": fields_from_model(TextJoinItem, excluded={"id"})[0].model_dump(
                    mode="json"
                ),
            },
            execution_version="3",
        ),
        _definition(
            "json_merge",
            "JSON合并",
            JSONMergeConfig,
            "json",
            _merge,
            {"left": "json_left", "right": "json_right"},
        ),
        _definition(
            "translation_options",
            "翻译执行参数",
            TranslationOptionsFields,
            "translation_options",
            lambda config: TranslationOptions(**config),
            {key: key for key in TranslationOptionsFields.model_fields},
        ),
        _definition(
            "asr_options",
            "识别执行参数",
            ASROptionsFields,
            "asr_options",
            lambda config: ASROptions(**config),
            {"timeout_seconds": "timeout_seconds"},
        ),
    )


def register_configuration_types(register_type, register_port):
    from ..domain.node_registry_core import PORT_DEFINITIONS

    for type_id, packet_type in CONFIGURATION_TYPES.items():

        async def encode(value, context, packet_type=packet_type):
            return packet_type.model_validate(value).model_dump(mode="json")

        async def decode(payload, context, packet_type=packet_type):
            return packet_type.model_validate(payload)

        options = {"config_value": lambda value: value.value} if type_id == "json_object" else {}
        register_type(
            type_id,
            label=type_id,
            validator=lambda value, cls=packet_type: isinstance(value, cls),
            checkpoint_codec=CheckpointCodec(type_id, "1", encode, decode),
            **options,
        )
    ports = {
        "json": "json_object",
        "json_left": "json_object",
        "json_right": "json_object",
    }
    ports.update({type_id: type_id for type_id in CONFIGURATION_TYPES if type_id != "json_object"})
    ports.update(
        {
            key: "string"
            for key in (
                "separator",
                "context",
            )
        }
    )
    ports.update({key: "integer" for key in TranslationOptionsFields.model_fields})
    for port, type_id in ports.items():
        if port not in PORT_DEFINITIONS:
            register_port(
                port, type_id=type_id, **({"label": "上下文"} if port == "context" else {})
            )
        elif PORT_DEFINITIONS[port].type_id != type_id:
            raise ValueError("configuration port has a conflicting type")
