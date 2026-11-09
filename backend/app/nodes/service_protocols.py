"""Explicit built-in service composition; domain registry has no adapter dependencies."""

from ..adapters.llm_translation_protocol import (
    validate_additional_parameters as chat_parameters,
)
from ..adapters.llm_translation_protocol import validate_base_url
from ..adapters.qwen_protocol import validate_additional_parameters as file_parameters
from ..adapters.qwen_streaming_protocol import (
    validate_additional_parameters as stream_parameters,
)
from ..adapters.qwen_streaming_protocol import validate_endpoint as stream_endpoint
from ..domain.configuration import SERVICE_PROTOCOLS, ServiceProtocol, register_protocol


def http_endpoint(value):
    return validate_base_url(value).rstrip("/")


def register_builtin_protocols():
    declarations = {
        "openai_chat": ServiceProtocol(
            "Chat Completions", frozenset({"translation"}), http_endpoint, chat_parameters
        ),
        "qwen_filetrans": ServiceProtocol(
            "Qwen Filetrans", frozenset({"asr_file"}), http_endpoint, file_parameters
        ),
        "qwen_streaming": ServiceProtocol(
            "Qwen Streaming", frozenset({"asr_streaming"}), stream_endpoint, stream_parameters
        ),
    }
    for protocol_id, declaration in declarations.items():
        if protocol_id not in SERVICE_PROTOCOLS:
            register_protocol(protocol_id, declaration)
