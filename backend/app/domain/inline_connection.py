"""Secret-free node connections, independent of reusable HTTP provider profiles."""

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .node_constants import BINDING_IDENTIFIER_PATTERN


class InlineConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    provider_type: str
    base_url: str = Field(min_length=1, max_length=2048)
    default_timeout: int = Field(ge=1, le=3600)
    region: str | None = Field(default=None, max_length=64)
    api_key_env: str | None = Field(default=None, max_length=128)

    @field_validator("provider_type")
    @classmethod
    def validate_provider_type(cls, value):
        if not re.fullmatch(BINDING_IDENTIFIER_PATTERN, value):
            raise ValueError("invalid connection provider type")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https", "ws", "wss"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid node connection URL")
        # Parsing the port also rejects malformed authorities without using the URL in errors.
        parsed.port
        return value

    @field_validator("api_key_env")
    @classmethod
    def validate_env(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("invalid environment variable name")
        return value


def inline_connection_active(binding, config):
    """A null mode pair declares a connection with no alternative configuration source."""
    return binding is not None and (
        binding["mode_key"] is None or config.get(binding["mode_key"]) == binding["mode_value"]
    )
