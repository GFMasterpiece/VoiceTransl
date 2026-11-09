"""Compatibility entry point and explicit composition of built-in workflow nodes.

Reusable registry mechanics live in node_registry_core; node implementations do not
import this facade. Existing callers retain the populated public registry.
"""

from ..nodes import builtin_definitions
from ..nodes.data_types import register_builtin_types
from ..nodes.source_info import PORT_LABELS as SOURCE_INFO_PORT_LABELS
from .node_constants import (
    ASR_NODE_TYPE as ASR_NODE_TYPE,
)
from .node_constants import (
    AUDIO_INPUT_ID as AUDIO_INPUT_ID,
)
from .node_constants import (
    AUDIO_OUTPUT_NODE_TYPE as AUDIO_OUTPUT_NODE_TYPE,
)
from .node_constants import (
    AUDIO_PORT as AUDIO_PORT,
)
from .node_constants import (
    DEFAULT_FILENAME_STEM as DEFAULT_FILENAME_STEM,
)
from .node_constants import (
    DEFAULT_TEXT_FILENAME_STEM as DEFAULT_TEXT_FILENAME_STEM,
)
from .node_constants import (
    DEFAULT_TEXT_FORMAT as DEFAULT_TEXT_FORMAT,
)
from .node_constants import (
    INPUT_NODE_TYPE as INPUT_NODE_TYPE,
)
from .node_constants import (
    NODE_TYPE_PATTERN as NODE_TYPE_PATTERN,
)
from .node_constants import (
    PORT_NAME as PORT_NAME,
)
from .node_constants import (
    PORT_PATTERN as PORT_PATTERN,
)
from .node_constants import (
    PREPROCESS_NODE_TYPE as PREPROCESS_NODE_TYPE,
)
from .node_constants import (
    TEXT_FORMATS as TEXT_FORMATS,
)
from .node_constants import (
    TEXT_OUTPUT_NODE_TYPE as TEXT_OUTPUT_NODE_TYPE,
)
from .node_constants import (
    TEXT_PORT as TEXT_PORT,
)
from .node_constants import (
    TRANSCRIPT_PORT as TRANSCRIPT_PORT,
)
from .node_contracts import (
    CapabilityField as CapabilityField,
)
from .node_contracts import (
    DataTypeDefinition as DataTypeDefinition,
)
from .node_contracts import (
    NodeCategory as NodeCategory,
)
from .node_contracts import (
    NodeConfigCapability as NodeConfigCapability,
)
from .node_contracts import (
    NodeDefinition as NodeDefinition,
)
from .node_contracts import (
    PortDefinition as PortDefinition,
)
from .node_registry_core import (
    NODE_DEFINITIONS as NODE_DEFINITIONS,
)
from .node_registry_core import (
    PORT_DEFINITIONS as PORT_DEFINITIONS,
)
from .node_registry_core import (
    TYPE_DEFINITIONS as TYPE_DEFINITIONS,
)
from .node_registry_core import (
    node_catalog as node_catalog,
)
from .node_registry_core import (
    node_ports as node_ports,
)
from .node_registry_core import (
    output_node_types as output_node_types,
)
from .node_registry_core import (
    port_catalog as port_catalog,
)
from .node_registry_core import (
    ports_are_compatible as ports_are_compatible,
)
from .node_registry_core import (
    register_node as register_node,
)
from .node_registry_core import (
    register_port as register_port,
)
from .node_registry_core import (
    register_type as register_type,
)
from .node_registry_core import (
    type_catalog as type_catalog,
)
from .node_registry_core import (
    type_is_compatible as type_is_compatible,
)
from .node_registry_core import (
    validate_node_definition as validate_node_definition,
)
from .node_registry_core import (
    validate_node_result as validate_node_result,
)
from .node_registry_core import (
    validate_port_value as validate_port_value,
)
from .node_registry_core import (
    workflow_ports as workflow_ports,
)

register_builtin_types(register_type, register_port)
for _port, _label in SOURCE_INFO_PORT_LABELS.items():
    if _port not in PORT_DEFINITIONS:
        register_port(_port, type_id="string", label=_label)
for _definition in builtin_definitions():
    register_node(_definition)

NODE_PORTS = node_ports()
OUTPUT_NODE_TYPES = output_node_types()
WORKFLOW_PORTS = (AUDIO_PORT, TRANSCRIPT_PORT, TEXT_PORT)

ASR_PROVIDERS = ({"id": "mock", "label": "Mock"},)


def asr_provider_catalog() -> list[dict[str, str]]:
    """Provider choices are a backend contract, not a frontend whitelist."""
    return [dict(item) for item in ASR_PROVIDERS]
