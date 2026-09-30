"""Logging and error helpers shared by the API and pipeline.

Application state deliberately stores only stable, user-facing error values.
The original exception and traceback remain available in the server log, with
common credential forms redacted before they are emitted.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_LEVELS = {
    name: getattr(logging, name) for name in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+(?:\s+[^\s,;]+)?)"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+"
    ),
)


def redact_secrets(value: object) -> str:
    """Return text safe for logs and user-visible diagnostics."""

    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(r"\1[REDACTED]", text)
        else:
            text = pattern.sub(r"\1[REDACTED]", text)
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_secrets(rendered)

    def formatException(self, exc_info) -> str:  # noqa: N802 - logging API name
        return redact_secrets(super().formatException(exc_info))


def configure_logging(level: str, data_dir: str | Path | None = None) -> logging.Logger:
    """Configure the named application logger idempotently.

    A tagged stream and optional file handler are installed once per target
    path. Repeated ``create_app`` calls in tests therefore do not duplicate
    records while still making a diagnostic log available on disk.
    """

    normalized = str(level).upper()
    if normalized not in _LEVELS:
        raise ValueError(f"invalid log level: {level}")
    logger = logging.getLogger("voicetransl")
    logger.setLevel(_LEVELS[normalized])
    logger.propagate = True
    formatter = _RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_key = None
    if data_dir is not None:
        file_key = f"file:{Path(data_dir).resolve() / 'voicetransl.log'}"
    # Tests and embedded callers may construct apps with different temporary
    # data roots. Retain only the current file target so records are not
    # duplicated into stale roots.
    for existing in list(logger.handlers):
        key = getattr(existing, "_voicetransl_key", None)
        if key and key.startswith("file:") and key != file_key:
            logger.removeHandler(existing)
            existing.close()

    def add_handler(handler: logging.Handler, key: str) -> None:
        for existing in logger.handlers:
            if getattr(existing, "_voicetransl_key", None) == key:
                existing.setLevel(_LEVELS[normalized])
                existing.setFormatter(formatter)
                return
        handler._voicetransl_key = key  # type: ignore[attr-defined]
        handler.setLevel(_LEVELS[normalized])
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    add_handler(logging.StreamHandler(), "stream")
    if data_dir is not None:
        path = Path(data_dir).resolve() / "voicetransl.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        add_handler(logging.FileHandler(path, encoding="utf-8"), f"file:{path}")
    return logger


def stable_stage_error(exc: BaseException) -> tuple[str, str]:
    """Map an internal exception to the stable stage error contract."""

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code, str(exc)
    return "STAGE_FAILED", "阶段处理失败"


def stable_job_error(exc: BaseException) -> tuple[str, str]:
    """Map an internal exception to the stable job error contract."""

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code, str(exc)
    return "JOB_FAILED", "任务执行失败"
