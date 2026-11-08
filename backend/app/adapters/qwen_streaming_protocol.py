"""Single source for the Qwen Audio Streaming wire and configuration contract."""

import ipaddress
import re
from urllib.parse import urlsplit

from .qwen_protocol import validate_additional_parameters as _validate_json_parameters

DEFAULT_ENDPOINT = "wss://maas.qianwenaiapi.com/api-ws/v1/inference"
DEFAULT_MODEL = "qwen-audio-3.0-asr-flash-streaming"
# Actual PCM16 mono WAV headers provide the sampling rate for each file. Other
# remote codecs aren't offered until their metadata can be verified reliably.
CHUNK_SIZE = 64 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_SENTENCES = 100_000
CLOSE_TIMEOUT = 1.0


def validate_endpoint(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        local = host == "localhost"
        if host and not local:
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        valid = (
            (parsed.scheme == "wss" or (parsed.scheme == "ws" and local))
            and host
            and parsed.port != 0
        )
        if (
            not valid
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise ValueError("请配置有效的 WSS 地址；明文 WS 仅限本机测试") from None
    return value


def validate_additional_parameters(value):
    _validate_json_parameters(value)
    protected = {
        "format",
        "samplerate",
        "header",
        "action",
        "taskid",
        "payload",
        "taskgroup",
        "task",
        "function",
        "streaming",
        "credentialref",
        "connectionmode",
    }
    if any(re.sub(r"[^a-z0-9]", "", key.casefold()) in protected for key in value):
        raise ValueError("additional_parameters 不能覆盖音频格式、采样率或任务协议字段")
    return value
