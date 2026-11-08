"""Secret-free Filetrans configuration rules shared by adapter and node."""

import json
import math
import re
from urllib.parse import urlsplit

DEFAULT_MODEL = "qwen-audio-3.1-asr-flash-filetrans"
DEFAULT_BASE_URL = "https://maas.qianwenaiapi.com/api/v1"
CHUNK_SIZE = 64 * 1024
MAX_PARAMETERS_BYTES = 16 * 1024
MAX_RESULT_BYTES = 32 * 1024 * 1024
POLL_INTERVAL = 2.0
CANCEL_TIMEOUT = 2.0

TEMPORARY_UPLOAD_RETENTION_HOURS = 48


def validate_base_url(value):
    """Shared Filetrans endpoint validation; errors never echo connection data."""
    try:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.port == 0
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or value != value.strip()
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Qwen节点服务地址无效") from None
    return value


def validate_additional_parameters(value):
    """Bound JSON complexity; optional model parameters cannot route or authenticate."""
    if not isinstance(value, dict):
        raise ValueError("additional_parameters 必须是 JSON 对象")
    count = 0
    forbidden = {
        "model",
        "input",
        "parameters",
        "fileurl",
        "fileurls",
        "languagehints",
        "authorization",
        "headers",
        "cookies",
        "cookie",
        "credentials",
        "token",
        "accesstoken",
        "refreshtoken",
        "policy",
        "signature",
        "baseurl",
        "url",
        "endpoint",
        "method",
        "stream",
        "providerprofileid",
        "apikeyenv",
    }

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 8 or count > 2048:
            raise ValueError("additional_parameters 过深或包含过多内容")
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str) or not key or any(ord(c) < 32 for c in key):
                    raise ValueError("additional_parameters 字段名无效")
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if depth == 0 and (
                    normalized in forbidden
                    or any(
                        part in normalized for part in ("apikey", "password", "secret", "accesskey")
                    )
                ):
                    raise ValueError("additional_parameters 不能包含核心输入、路由或认证字段")
                visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("additional_parameters 必须仅包含 JSON 值")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("additional_parameters 不允许非有限数值")

    visit(value, 0)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("additional_parameters 必须是合法 JSON") from None
    if len(encoded) > MAX_PARAMETERS_BYTES:
        raise ValueError("additional_parameters 不能超过16 KiB")
    return value
