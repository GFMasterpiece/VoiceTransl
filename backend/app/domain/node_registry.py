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
    presentation: dict[str, object] | None = None

    @property
    def config(self) -> NodeConfigCapability:
        return NodeConfigCapability(kind=self.config_kind, fields=list(self.fields))


def normalize_empty_config(config: dict[str, object]) -> dict[str, object]:
    if config:
        raise ValueError("config 必须为空对象")
    return config


def normalize_input_config(config: dict[str, object]) -> dict[str, object]:
    if set(config) - {"audio_id"}:
        raise ValueError("audio_input 仅接受 audio_id")
    if "audio_id" in config:
        from .project import AudioAsset

        value = config["audio_id"]
        if not isinstance(value, str):
            raise ValueError("audio_id 必须是字符串")
        AudioAsset.validate_id(value)
    return dict(config)


async def audio_preview_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_preview import execute_audio_preview

    return await execute_audio_preview(context)


async def text_preview_executor(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_preview import execute_text_preview

    return await execute_text_preview(context)


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


@dataclass(frozen=True)
class DataTypeDefinition:
    """Runtime data contract independent from a graph port identifier.

    A concrete type may declare a parent logical type. This lets values such as
    decoded, streamed, or remote audio satisfy a node that requests ``audio``
    without making every implementation inherit one application class.
    """

    type_id: str
    label: str
    is_valid: Callable[[object], bool]
    parent_type_id: str | None = None
    capabilities: frozenset[str] = frozenset()


class PortDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    port_id: str
    type_id: str
    label: str
    capability_requirements: frozenset[str] = frozenset()
    # Kept as compatibility properties for existing extensions. New code
    # should validate through the referenced DataTypeDefinition.
    data_type: type = object
    is_valid: Callable[[object], bool] | None = None


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


def _is_audio_data(value: object) -> bool:
    # Structural validation keeps concrete audio implementations replaceable.
    path = getattr(value, "path", None)
    return (
        path is not None
        and hasattr(path, "open")
        and isinstance(getattr(value, "suffix", None), str)
        and isinstance(getattr(value, "media_type", None), str)
        and isinstance(getattr(value, "size_bytes", None), int)
        and getattr(value, "size_bytes", -1) >= 0
    )


def _is_transcript_data(value: object) -> bool:
    from .transcript import Transcript

    return isinstance(getattr(value, "transcript", None), Transcript) and hasattr(value, "path")


def _is_text_artifact(value: object) -> bool:
    from .workflow import WorkflowArtifact

    return isinstance(value, WorkflowArtifact) and value.port == TEXT_PORT


# Built-in protocol ports, not a separate node whitelist. TEXT is currently a
# terminal artifact contract and has no built-in producer node.
register_type(AUDIO_PORT, label="音频", validator=_is_audio_data)
register_type(TRANSCRIPT_PORT, label="转写结果", validator=_is_transcript_data)
register_type(TEXT_PORT, label="文本产物", validator=_is_text_artifact)
register_port(AUDIO_PORT, type_id=AUDIO_PORT)
register_port(TRANSCRIPT_PORT, type_id=TRANSCRIPT_PORT)
register_port(TEXT_PORT, type_id=TEXT_PORT)


NODE_DEFINITIONS: dict[str, NodeDefinition] = {
    INPUT_NODE_TYPE: NodeDefinition(
        type=INPUT_NODE_TYPE,
        label="音频输入",
        input_ports=(),
        output_ports=(AUDIO_PORT,),
        terminal=False,
        config_kind="none",
        normalize_config=normalize_input_config,
        validate_config=normalize_input_config,
        fields=(CapabilityField(key="audio_id", type="string"),),
        presentation={"resource_input": {"resource_type": "audio", "config_key": "audio_id"}},
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


for _preview_type, _port, _renderer, _executor in (
    ("audio_preview", AUDIO_PORT, "audio", audio_preview_executor),
    ("text_preview", TRANSCRIPT_PORT, "text", text_preview_executor),
):
    NODE_DEFINITIONS[_preview_type] = NodeDefinition(
        type=_preview_type,
        label="音频预览" if _renderer == "audio" else "文本预览",
        input_ports=(_port,),
        output_ports=(),
        terminal=True,
        config_kind="none",
        normalize_config=normalize_empty_config,
        validate_config=normalize_empty_config,
        presentation={"preview": {"renderer": _renderer}},
        execute=_executor,
    )


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
        if not validate_port_value(port, data.value):
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
            "presentation": item.presentation,
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
    "DataTypeDefinition",
    "NodeResult",
    "TYPE_DEFINITIONS",
    "PortDefinition",
    "PORT_DEFINITIONS",
    "ports_are_compatible",
    "type_is_compatible",
    "validate_port_value",
    "register_type",
    "register_port",
    "validate_node_result",
    "register_node",
    "validate_node_definition",
    "NODE_DEFINITIONS",
    "NODE_PORTS",
    "OUTPUT_NODE_TYPES",
    "workflow_ports",
    "port_catalog",
    "type_catalog",
    "WORKFLOW_PORTS",
    "node_catalog",
    "asr_provider_catalog",
]
