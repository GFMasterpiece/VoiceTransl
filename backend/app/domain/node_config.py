from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class MockPreprocessNodeConfig(_ConfigModel):
    provider: Literal["mock"] = "mock"


class MockASRNodeConfig(_ConfigModel):
    provider: Literal["mock"] = "mock"
    language: str = Field(default="ja", min_length=1, max_length=32)


__all__ = [
    "MockASRNodeConfig",
    "MockPreprocessNodeConfig",
]
