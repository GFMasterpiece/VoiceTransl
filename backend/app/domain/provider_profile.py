"""Validated, secret-free provider connection profiles.

Profiles describe how the server connects to a provider. Credentials are
always resolved from the environment variable named by api_key_env and
are never persisted in this model.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ENVIRONMENT_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderProfile(BaseModel):
    """A reusable provider connection without secret material."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    id: str = Field(min_length=1, max_length=64)
    provider_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=2_048)
    api_key_env: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, max_length=64)
    default_timeout: int = Field(default=120, ge=1, le=3_600)
    enabled: bool = True
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("id", "provider_type")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be a safe identifier")
        return value

    @field_validator("api_key_env")
    @classmethod
    def validate_api_key_env(cls, value: str | None) -> str | None:
        if value is not None and not _ENVIRONMENT_VARIABLE.fullmatch(value):
            raise ValueError("api_key_env must be an environment variable name")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an HTTP or HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("base_url must not contain credentials")
        return value.rstrip("/")


class ProviderProfilesDocument(BaseModel):
    """Versioned on-disk document for global profiles."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    profiles: list[ProviderProfile] = Field(default_factory=list)

    @field_validator("profiles")
    @classmethod
    def validate_unique_ids(cls, value: list[ProviderProfile]) -> list[ProviderProfile]:
        ids = [profile.id for profile in value]
        if len(ids) != len(set(ids)):
            raise ValueError("provider profile ids must be unique")
        return value


__all__ = ["ProviderProfile", "ProviderProfilesDocument"]
