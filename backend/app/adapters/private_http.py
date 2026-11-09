"""Suppress sensitive HTTP transport diagnostics only in the active task context."""

import logging
from contextvars import ContextVar

PRIVATE_HTTP = ContextVar("voicetransl_private_http", default=False)


class _PrivateHTTPFilter(logging.Filter):
    def filter(self, record):
        return not PRIVATE_HTTP.get()


HTTP_FILTER = _PrivateHTTPFilter()


def ensure_private_http_filters():
    # httpcore creates some transport loggers lazily during client construction.
    names = {
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
        *(name for name in logging.Logger.manager.loggerDict if name.startswith("httpcore.")),
    }
    for name in names:
        logger = logging.getLogger(name)
        if HTTP_FILTER not in logger.filters:
            logger.addFilter(HTTP_FILTER)


ensure_private_http_filters()
