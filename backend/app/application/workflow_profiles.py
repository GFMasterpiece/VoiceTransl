"""Freeze non-secret connections at submission, never while dispatching queued work."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from ..domain.inline_connection import InlineConnection
from ..domain.provider_profile import ProviderProfile
from ..domain.schema import SchemaVersionError

if TYPE_CHECKING:
    from ..domain.workflow import WorkflowSnapshot


class ProfileSnapshotError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _profile_id(value):
    if not isinstance(value, str) or not value.strip():
        raise ProfileSnapshotError("PROVIDER_PROFILE_REQUIRED", "请先选择提供商连接配置")
    return value


def _validate_connection(profile: ProviderProfile, expected_type: str, *, require_env=True):
    if not profile.enabled:
        raise ProfileSnapshotError("PROVIDER_PROFILE_DISABLED", "提供商连接配置已停用")
    if profile.provider_type != expected_type:
        raise ProfileSnapshotError("PROVIDER_PROFILE_TYPE_MISMATCH", "提供商连接类型与节点不匹配")
    if not profile.base_url:
        raise ProfileSnapshotError("PROVIDER_PROFILE_BASE_URL_MISSING", "提供商连接未配置服务地址")
    parsed = urlsplit(profile.base_url)
    if parsed.query or parsed.fragment:
        raise ProfileSnapshotError(
            "PROVIDER_PROFILE_BASE_URL_INVALID", "云端服务地址不能包含查询参数或片段"
        )
    if require_env and not profile.api_key_env:
        raise ProfileSnapshotError(
            "PROVIDER_PROFILE_API_KEY_ENV_MISSING", "提供商连接未配置 API Key 环境变量名"
        )


def profile_from_snapshot(
    snapshot: WorkflowSnapshot, profile_id: str, expected_type: str
) -> ProviderProfile:
    profile_id = _profile_id(profile_id)
    profiles = snapshot.provider_profiles_snapshot
    if profiles is None or profile_id not in profiles:
        raise ProfileSnapshotError(
            "PROVIDER_PROFILE_SNAPSHOT_MISSING", "运行缺少冻结的连接配置，请重新提交工作流"
        )
    profile = profiles[profile_id]
    if profile.id != profile_id:
        raise ProfileSnapshotError("PROVIDER_PROFILE_SNAPSHOT_INVALID", "冻结的连接配置无效")
    _validate_connection(profile, expected_type)
    return profile.model_copy(deep=True)


def validate_frozen_workflow_profiles(
    snapshot: WorkflowSnapshot, store=None, project_id=None
) -> None:
    from ..domain.workflow import workflow_provider_profile_inputs

    for node, binding in workflow_provider_profile_inputs(snapshot.nodes):
        profile_from_snapshot(
            snapshot, node.config.get(binding["config_key"]), binding["provider_type"]
        )
    from ..domain.workflow import workflow_inline_connection_inputs

    for node, binding, provider_type in workflow_inline_connection_inputs(snapshot.nodes):
        _inline_profile(node, binding, provider_type)
        ref = node.config.get(binding["credential_ref_key"])
        if not ref:
            raise ProfileSnapshotError("CREDENTIAL_NOT_FOUND", "运行缺少固定密钥版本，请重新提交")
        credential_operation(store, "validate_ref", project_id, node.id, provider_type, ref)


def freeze_workflow_profiles(
    store, snapshot: WorkflowSnapshot, project_id=None
) -> WorkflowSnapshot:
    from ..domain.workflow import workflow_provider_profile_inputs

    dependencies = list(workflow_provider_profile_inputs(snapshot.nodes))
    frozen = snapshot.model_copy(deep=True)
    _freeze_inline_connections(store, frozen, project_id)
    if not dependencies:
        frozen.provider_profiles_snapshot = None
        return frozen
    try:
        profiles = {profile.id: profile for profile in store.load_provider_profiles()}
    except SchemaVersionError:
        raise
    except (OSError, ValueError) as error:
        raise ProfileSnapshotError("INVALID_PROVIDER_PROFILES", "提供商连接配置文件无效") from error
    selected = {}
    for node, binding in dependencies:
        profile_id = _profile_id(node.config.get(binding["config_key"]))
        profile = profiles.get(profile_id)
        if profile is None:
            raise ProfileSnapshotError("PROVIDER_PROFILE_NOT_FOUND", "提供商连接配置不存在")
        _validate_connection(profile, binding["provider_type"])
        selected[profile_id] = profile.model_copy(deep=True)
    frozen.provider_profiles_snapshot = selected
    return frozen


def credential_operation(store, method, *args, **kwargs):
    from ..storage.secret_store import SecretStore, SecretStoreError

    if store is None:
        raise ProfileSnapshotError("CREDENTIAL_STORAGE_ERROR", "凭据存储不可用")
    try:
        return getattr(SecretStore(store.data_dir), method)(*args, **kwargs)
    except SecretStoreError as error:
        raise ProfileSnapshotError(error.code, str(error)) from None


def _inline_profile(node, binding, provider_type):
    if not node.config.get(binding["base_url_key"]):
        raise ProfileSnapshotError("PROVIDER_PROFILE_BASE_URL_MISSING", "节点未配置服务地址")
    try:
        profile = InlineConnection(
            provider_type=provider_type,
            base_url=node.config.get(binding["base_url_key"]),
            default_timeout=node.config.get(binding["timeout_key"]),
            region=node.config.get(binding["region_key"]),
            api_key_env=node.config.get(binding["api_key_env_key"]),
        )
    except ValueError:
        raise ProfileSnapshotError(
            "INLINE_CONNECTION_INVALID", "节点连接设置无效或不完整"
        ) from None
    return profile


def _freeze_inline_connections(store, snapshot, project_id):
    from ..domain.node_registry import NODE_DEFINITIONS
    from ..domain.workflow import workflow_inline_connection_inputs

    for node, binding, provider_type in workflow_inline_connection_inputs(snapshot.nodes):
        definition = NODE_DEFINITIONS[node.type]
        if definition.normalize_config is not None:
            node.config = definition.normalize_config(dict(node.config))
        profile = _inline_profile(node, binding, provider_type)
        for role, value in (
            ("base_url_key", profile.base_url),
            ("timeout_key", profile.default_timeout),
            ("region_key", profile.region),
            ("api_key_env_key", profile.api_key_env),
        ):
            if binding[role] is not None:
                node.config[binding[role]] = value
        ref_key = binding["credential_ref_key"]
        ref = node.config.get(ref_key)
        if ref:
            credential_operation(store, "validate_ref", project_id, node.id, provider_type, ref)
        else:
            key = os.environ.get(profile.api_key_env, "") if profile.api_key_env else ""
            if not key:
                raise ProfileSnapshotError(
                    "CREDENTIAL_REQUIRED",
                    "请在此节点中保存 API Key"
                    if binding["api_key_env_key"] is None
                    else "请在节点中保存 API Key 或配置备用环境变量",
                )
            node.config[ref_key] = credential_operation(
                store,
                "create",
                project_id,
                node.id,
                provider_type,
                key,
                request_id=f"env:{uuid4().hex}",
            )


def inline_connection_from_snapshot(store, snapshot, node_id, project_id):
    from ..domain.workflow import workflow_inline_connection_inputs

    for node, binding, provider_type in workflow_inline_connection_inputs(snapshot.nodes):
        if node.id != node_id:
            continue
        profile = _inline_profile(node, binding, provider_type)
        ref = node.config.get(binding["credential_ref_key"])
        if not ref:
            raise ProfileSnapshotError("CREDENTIAL_NOT_FOUND", "运行缺少固定密钥版本")
        key = credential_operation(store, "read", project_id, node.id, provider_type, ref)
        return profile, key
    raise ProfileSnapshotError("INLINE_CONNECTION_INVALID", "运行缺少节点连接设置")
