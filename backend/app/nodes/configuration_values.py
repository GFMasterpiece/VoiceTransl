"""Composable, deterministic configuration values and their explicit codecs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.checkpoint import CheckpointCodec
from ..domain.context_values import (
    CONFIGURATION_TYPES,
    ASRContext,
    ASROptions,
    ASROptionsFields,
    JSONValue,
    TranslationContext,
    TranslationOptions,
    TranslationOptionsFields,
    canonical_object,
    validate_language,
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


class TextJoinConfig(_Config):
    left: str = Field(default="", json_schema_extra={"widget": "multiline"})
    right: str = Field(default="", json_schema_extra={"widget": "multiline"})
    separator: str = Field(default="\n\n", json_schema_extra={"widget": "multiline"})

    @field_validator("left", "right", "separator")
    @classmethod
    def validate_part(cls, value):
        return validate_text(value)


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


class TranslationContextConfig(_Config):
    source_language: str = Field(
        default="", max_length=32, json_schema_extra={"suggestion_source": "languages"}
    )
    target_language: str = Field(
        default="", max_length=32, json_schema_extra={"suggestion_source": "languages"}
    )
    instructions: str = Field(
        default="", max_length=8192, json_schema_extra={"widget": "multiline"}
    )
    background: str = Field(default="", max_length=8192, json_schema_extra={"widget": "multiline"})
    glossary: dict[str, str] = Field(
        default_factory=dict, json_schema_extra={"input_type_id": "json_object"}
    )


class ASRContextConfig(_Config):
    language: str = Field(
        default="", max_length=32, json_schema_extra={"suggestion_source": "languages"}
    )

    @field_validator("language")
    @classmethod
    def validate_code(cls, value):
        return validate_language(value)


def _packet_context(config):
    return TranslationContext.from_values(**config)


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
        if isinstance(value, TranslationContext) and not value.target_language:
            raise NodeExecutionError("LLM_TARGET_LANGUAGE_REQUIRED", "请填写翻译上下文的目标语言")
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
    return validate_text(config["left"] + config["separator"] + config["right"])


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
        _definition(
            "text_join",
            "文本拼接",
            TextJoinConfig,
            "string",
            _join,
            {"left": "text_left", "right": "text_right", "separator": "separator"},
            check=_join,
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
            "translation_context",
            "翻译上下文",
            TranslationContextConfig,
            "translation_context",
            _packet_context,
            {key: key for key in TranslationContextConfig.model_fields},
            check=_packet_context,
        ),
        _definition(
            "asr_context",
            "识别上下文",
            ASRContextConfig,
            "asr_context",
            lambda config: ASRContext(**config),
            {"language": "language"},
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
        "glossary": "json_object",
    }
    ports.update({type_id: type_id for type_id in CONFIGURATION_TYPES if type_id != "json_object"})
    ports.update(
        {
            key: "string"
            for key in (
                "text_left",
                "text_right",
                "separator",
                "instructions",
                "background",
                "source_language",
                "target_language",
                "language",
            )
        }
    )
    ports.update({key: "integer" for key in TranslationOptionsFields.model_fields})
    for port, type_id in ports.items():
        if port not in PORT_DEFINITIONS:
            register_port(port, type_id=type_id)
        elif PORT_DEFINITIONS[port].type_id != type_id:
            raise ValueError("configuration port has a conflicting type")
