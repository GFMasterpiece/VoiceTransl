"""Global editor defaults and runtime scheduling settings."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from ..domain.schema import SCHEMA_VERSION, SchemaVersionError, require_schema_version
from ..domain.settings import GlobalSettings, SettingsResponse, default_definitions, global_fields
from ..storage.secret_store import SecretStoreError
from ..storage.settings_store import SettingsConflict

router = APIRouter(prefix="/api/v1")


class DefaultKeySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_type: str
    operation: Literal["set"]
    api_key: SecretStr = Field(min_length=1, max_length=8192, exclude=True)


class DefaultKeyClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_type: str
    operation: Literal["clear"]


class SettingsSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = SCHEMA_VERSION
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    global_: GlobalSettings = Field(alias="global")
    node_defaults: dict[str, dict[str, object]]
    credential_updates: list[
        Annotated[DefaultKeySet | DefaultKeyClear, Field(discriminator="operation")]
    ] = Field(default_factory=list, exclude=True)

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value):
        return require_schema_version(value, "settings request")


def response(document):
    return {
        "schema_version": document.schema_version,
        "revision": document.revision,
        "global_fields": global_fields(),
        "global": document.global_,
        "node_defaults": {
            key: {
                "label": definition.label,
                "fields": [
                    field
                    for field in definition.fields
                    if field.key in definition.settings_default_fields
                ],
                "config": document.node_defaults[key],
                "credential_configured": key in document.credentials,
                "credential_version": document.credentials.get(key),
            }
            for key, definition in default_definitions().items()
        },
    }


def failure(code, message, status):
    return HTTPException(
        status, detail={"error": {"code": code, "message": message, "details": None}}
    )


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request):
    try:
        return response(request.app.state.settings_store.load())
    except SchemaVersionError:
        raise
    except SecretStoreError as error:
        raise failure(error.code, str(error), 503) from None
    except (OSError, ValueError):
        raise failure("SETTINGS_READ_FAILED", "全局设置无法读取，请检查设置文件", 503) from None


@router.put("/settings", response_model=SettingsResponse)
async def put_settings(body: SettingsSaveRequest, request: Request):
    state = request.app.state
    async with state.settings_store.lock:
        try:
            document = state.settings_store.save(body)
        except SchemaVersionError:
            raise
        except SettingsConflict:
            raise failure(
                "SETTINGS_REVISION_CONFLICT", "全局设置已被修改，请刷新后重试", 409
            ) from None
        except SecretStoreError as error:
            status = (
                409
                if error.code == "CREDENTIAL_REQUEST_CONFLICT"
                else 422
                if error.code == "CREDENTIAL_INVALID"
                else 503
            )
            raise failure(error.code, str(error), status) from None
        except OSError:
            raise failure(
                "SETTINGS_SAVE_FAILED", "全局设置保存失败，请使用相同请求重试", 503
            ) from None
        except ValueError:
            raise failure("INVALID_SETTINGS", "全局设置无效，请检查字段", 422) from None
        state.settings.upload_concurrency = document.global_.upload_concurrency
        state.settings.max_concurrent = document.global_.max_concurrent
        state.jobs.set_max_concurrent(document.global_.max_concurrent)
        state.batches.resize()
        return response(document)
