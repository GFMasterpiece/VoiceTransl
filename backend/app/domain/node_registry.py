"""Authoritative backend capabilities for workflow nodes and ASR discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

from .node_config import MockASRNodeConfig, MockPreprocessNodeConfig
from .node_execution import NodeExecutionContext, NodeResult

AUDIO_PORT = "audio"
TRANSCRIPT_PORT = "transcript"
TEXT_PORT = "text"
PORT_NAME = AUDIO_PORT
TEXT_FORMATS = frozenset({"txt", "srt", "vtt", "lrc"})
INPUT_NODE_TYPE = "audio_input"
AUDIO_INPUT_ID = "input"
PREPROCESS_NODE_TYPE = "preprocess"
ASR_NODE_TYPE = "asr"
TEXT_OUTPUT_NODE_TYPE = "text_output"
AUDIO_OUTPUT_NODE_TYPE = "audio_output"
DEFAULT_FILENAME_STEM = "audio-copy"
DEFAULT_TEXT_FILENAME_STEM = "transcript"
DEFAULT_TEXT_FORMAT = "txt"
NODE_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
PORT_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class CapabilityField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    type: str
    required: bool = False
    default: object = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None
    description: str | None = None
    validator: str | None = None
    options: list[dict[str, object]] = Field(default_factory=list)


class NodeConfigCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "object"
    fields: list[CapabilityField] = Field(default_factory=list)


ConfigCallable = Callable[[dict[str, object]], dict[str, object]]
ExecuteCallable = Callable[[NodeExecutionContext], Awaitable[NodeResult]]


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

    @property
    def config(self) -> NodeConfigCapability:
        return NodeConfigCapability(kind=self.config_kind, fields=list(self.fields))


def normalize_empty_config(config: dict[str, object]) -> dict[str, object]:
    if config:
        raise ValueError("audio_input 的 config 必须为空对象")
    return config


def normalize_preprocess_config(config: dict[str, object]) -> dict[str, object]:
    from .node_config import MockPreprocessNodeConfig

    return MockPreprocessNodeConfig.model_validate(config, strict=True).model_dump(mode="json")


def validate_preprocess_config(config: dict[str, object]) -> dict[str, object]:
    from .node_config import MockPreprocessNodeConfig

    return MockPreprocessNodeConfig.model_validate(config, strict=True).model_dump(mode="json")


def normalize_asr_config(config: dict[str, object]) -> dict[str, object]:
    from .workflow import validate_asr_config

    return validate_asr_config(config).model_dump(mode="json")


def validate_asr_node_config(config: dict[str, object]) -> dict[str, object]:
    from .workflow import validate_asr_config

    return validate_asr_config(config).model_dump(mode="json")


def normalize_text_output_config(config: dict[str, object]) -> dict[str, object]:
    from .workflow import validate_text_output_config

    return validate_text_output_config(config).model_dump(mode="json")


def validate_text_output_node_config(config: dict[str, object]) -> dict[str, object]:
    from .workflow import validate_text_output_config

    return validate_text_output_config(config).model_dump(mode="json")


def normalize_audio_output_config(config: dict[str, object]) -> dict[str, object]:
    from .workflow import AudioOutputConfig

    return AudioOutputConfig.model_validate(config, strict=True).model_dump(mode="json")


def validate_audio_output_config(config: dict[str, object]) -> dict[str, object]:
    return normalize_audio_output_config(config)


async def input_executor(context: NodeExecutionContext) -> NodeResult:
    raise ValueError("audio_input is handled by the runner source boundary")


async def preprocess_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_runner import _execute_preprocess_node

    upstream = context.input("audio").value
    artifact, output = await _execute_preprocess_node(
        context.store,
        context.run,
        context.node,
        upstream,
        context.cancel_event,
    )
    result = NodeResult(artifact=artifact)
    result.add("audio", output)
    return result


async def audio_output_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_runner import _execute_copy_node

    artifact = await _execute_copy_node(
        context.store,
        context.run,
        context.node,
        context.input("audio").value,
        context.cancel_event,
    )
    return NodeResult(artifact=artifact)


async def asr_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_asr import execute_asr_node

    artifact, transcript = await execute_asr_node(
        context.store,
        context.run,
        context.node,
        context.input("audio").value.path,
        context.project,
        context.cancel_event,
        context.mock_delay_ms,
    )
    result = NodeResult(artifact=artifact)
    result.add("transcript", transcript)
    return result


async def text_output_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_asr import execute_text_output_node

    artifact = await execute_text_output_node(
        context.store,
        context.run,
        context.node,
        context.input("transcript").value,
        context.cancel_event,
    )
    return NodeResult(artifact=artifact)


def _fields_from_model(
    model,
    *,
    widgets: dict[str, tuple[str, list[dict[str, object]] | None]] | None = None,
    excluded: set[str] | None = None,
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
                minimum=spec.get("minimum"),
                maximum=spec.get("maximum"),
                min_length=spec.get("minLength"),
                max_length=spec.get("maxLength"),
                pattern=spec.get("pattern"),
                description=spec.get("description"),
                validator="safe_filename_stem" if key == "filename_stem" else None,
                options=options or [],
            )
        )
    return tuple(fields)


def _config_fields(
    model: type[BaseModel],
    *,
    provider_options: list[dict[str, object]],
) -> tuple[CapabilityField, ...]:
    return _fields_from_model(
        model,
        widgets={"provider": ("select", provider_options)},
    )


MOCK_PROVIDER_OPTIONS = [{"value": "mock", "label": "Mock"}]


class PortDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    data_type: type
    is_valid: Callable[[object], bool]


PORT_DEFINITIONS: dict[str, PortDefinition] = {}


def register_port(
    name: str, data_type: type, *, validator: Callable[[object], bool] | None = None
) -> PortDefinition:
    if not re.fullmatch(PORT_PATTERN, name):
        raise ValueError("port name must be a lowercase identifier")
    if name in PORT_DEFINITIONS:
        raise ValueError("port already registered")
    definition = PortDefinition(
        data_type=data_type, is_valid=validator or (lambda value: isinstance(value, data_type))
    )
    PORT_DEFINITIONS[name] = definition
    return definition


def _is_audio_data(value: object) -> bool:
    # Keep the registry importable without pulling the application runner into
    # the domain layer merely to define the built-in protocol port.
    from ..application.workflow_runner import AudioData

    return isinstance(value, AudioData)


def _is_transcript_data(value: object) -> bool:
    from ..application.workflow_asr import TranscriptData

    return isinstance(value, TranscriptData)


def _is_text_artifact(value: object) -> bool:
    from .workflow import WorkflowArtifact

    return isinstance(value, WorkflowArtifact) and value.port == TEXT_PORT


# Built-in protocol ports, not a separate node whitelist. TEXT is currently a
# terminal artifact contract and has no built-in producer node.
register_port(AUDIO_PORT, object, validator=_is_audio_data)
register_port(TRANSCRIPT_PORT, object, validator=_is_transcript_data)
register_port(TEXT_PORT, object, validator=_is_text_artifact)


NODE_DEFINITIONS: dict[str, NodeDefinition] = {
    INPUT_NODE_TYPE: NodeDefinition(
        type=INPUT_NODE_TYPE,
        label="音频输入",
        input_ports=(),
        output_ports=(AUDIO_PORT,),
        terminal=False,
        config_kind="none",
        normalize_config=normalize_empty_config,
        validate_config=normalize_empty_config,
        execute=input_executor,
        max_instances=1,
        required_inbound=0,
        is_source=True,
        removable=False,
    ),
    PREPROCESS_NODE_TYPE: NodeDefinition(
        type=PREPROCESS_NODE_TYPE,
        label="预处理",
        input_ports=(AUDIO_PORT,),
        output_ports=(AUDIO_PORT,),
        terminal=False,
        config_kind="preprocess",
        fields=_config_fields(MockPreprocessNodeConfig, provider_options=MOCK_PROVIDER_OPTIONS),
        normalize_config=normalize_preprocess_config,
        validate_config=validate_preprocess_config,
        execute=preprocess_executor,
    ),
    ASR_NODE_TYPE: NodeDefinition(
        type=ASR_NODE_TYPE,
        label="语音识别",
        input_ports=(AUDIO_PORT,),
        output_ports=(TRANSCRIPT_PORT,),
        terminal=False,
        config_kind="asr",
        fields=_config_fields(MockASRNodeConfig, provider_options=MOCK_PROVIDER_OPTIONS),
        normalize_config=normalize_asr_config,
        validate_config=validate_asr_node_config,
        execute=asr_executor,
    ),
    TEXT_OUTPUT_NODE_TYPE: NodeDefinition(
        type=TEXT_OUTPUT_NODE_TYPE,
        label="文本输出",
        input_ports=(TRANSCRIPT_PORT,),
        output_ports=(),
        terminal=True,
        config_kind="text_output",
        normalize_config=normalize_text_output_config,
        validate_config=validate_text_output_node_config,
        execute=text_output_executor,
        fields=(),
    ),
    AUDIO_OUTPUT_NODE_TYPE: NodeDefinition(
        type=AUDIO_OUTPUT_NODE_TYPE,
        label="音频输出",
        input_ports=(AUDIO_PORT,),
        output_ports=(),
        terminal=True,
        config_kind="audio_output",
        normalize_config=normalize_audio_output_config,
        validate_config=validate_audio_output_config,
        execute=audio_output_executor,
        fields=(),
    ),
}


def node_ports():
    return {
        node_type: (frozenset(item.input_ports), frozenset(item.output_ports))
        for node_type, item in NODE_DEFINITIONS.items()
    }


def output_node_types():
    return frozenset(node_type for node_type, item in NODE_DEFINITIONS.items() if item.terminal)


NODE_PORTS = node_ports()
OUTPUT_NODE_TYPES = output_node_types()


def workflow_ports() -> tuple[str, ...]:
    """Return the live protocol ports in registration order."""

    return tuple(PORT_DEFINITIONS)


# Compatibility name for importers that need the built-in port set. The API
# consumes workflow_ports() so newly registered ports are included live.
WORKFLOW_PORTS = (AUDIO_PORT, TRANSCRIPT_PORT, TEXT_PORT)


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
    builtin_types = {
        INPUT_NODE_TYPE,
        PREPROCESS_NODE_TYPE,
        ASR_NODE_TYPE,
        TEXT_OUTPUT_NODE_TYPE,
        AUDIO_OUTPUT_NODE_TYPE,
    }
    if definition.type not in builtin_types:
        if definition.is_source:
            raise ValueError("the current runner supports only the built-in audio source")
        if len(definition.input_ports) != 1 or definition.required_inbound != 1:
            raise ValueError("registered nodes require exactly one input and required_inbound=1")
        if definition.terminal:
            if definition.output_ports:
                raise ValueError("terminal nodes cannot declare graph output ports")
        elif len(definition.output_ports) != 1:
            raise ValueError("non-terminal nodes require exactly one output port")
    for port in all_ports:
        if port not in PORT_DEFINITIONS:
            raise ValueError(f"port has no registered runtime type: {port}")
    if definition.normalize_config is not None and not callable(definition.normalize_config):
        raise ValueError("normalize_config must be callable")
    if definition.validate_config is not None and not callable(definition.validate_config):
        raise ValueError("validate_config must be callable")
    if definition.execute is not None and not callable(definition.execute):
        raise ValueError("execute must be callable")
    if not definition.is_source and definition.execute is None:
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
    if set(result.outputs) - set(definition.output_ports):
        raise ValueError("node result returned an undeclared output port")
    for port, data in result.outputs.items():
        if data.port != port:
            raise ValueError("node result port key does not match NodeData.port")
        port_definition = PORT_DEFINITIONS.get(port)
        if port_definition is None:
            raise TypeError(f"node output {port} has no registered runtime type")
        if not port_definition.is_valid(data.value):
            raise TypeError(f"node output {port} has an invalid runtime type")
    return result


ASR_PROVIDERS = ({"id": "mock", "label": "Mock"},)


def asr_provider_catalog() -> list[dict[str, str]]:
    """Provider choices are a backend contract, not a frontend whitelist."""
    return [dict(item) for item in ASR_PROVIDERS]


def _capability_fields(definition: NodeDefinition) -> tuple[CapabilityField, ...]:
    if definition.fields:
        return definition.fields
    if definition.config_kind in {"audio_output", "text_output"}:
        from .workflow import AudioOutputConfig, TextOutputConfig

        model = TextOutputConfig if definition.config_kind == "text_output" else AudioOutputConfig
        return _fields_from_model(
            model,
            widgets={"format": ("select", None)}
            if definition.config_kind == "text_output"
            else None,
        )
    return ()


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
            "required_inbound": item.required_inbound,
            "removable": item.removable,
            "config": NodeConfigCapability(
                kind=item.config_kind, fields=list(_capability_fields(item))
            ).model_dump(mode="json"),
        }
        for item in NODE_DEFINITIONS.values()
    ]


__all__ = [
    "CapabilityField",
    "NodeConfigCapability",
    "NodeDefinition",
    "NodeResult",
    "PortDefinition",
    "PORT_DEFINITIONS",
    "register_port",
    "validate_node_result",
    "register_node",
    "validate_node_definition",
    "NODE_DEFINITIONS",
    "NODE_PORTS",
    "OUTPUT_NODE_TYPES",
    "workflow_ports",
    "WORKFLOW_PORTS",
    "node_catalog",
    "asr_provider_catalog",
]
