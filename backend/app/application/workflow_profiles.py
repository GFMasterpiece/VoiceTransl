"""Freeze non-secret connections at submission, never while dispatching queued work."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

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


def _validate_connection(profile: ProviderProfile, expected_type: str):
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
    if not profile.api_key_env:
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


def validate_frozen_workflow_profiles(snapshot: WorkflowSnapshot) -> None:
    from ..domain.workflow import workflow_provider_profile_inputs

    for node, binding in workflow_provider_profile_inputs(snapshot.nodes):
        profile_from_snapshot(
            snapshot, node.config.get(binding["config_key"]), binding["provider_type"]
        )


def freeze_workflow_profiles(store, snapshot: WorkflowSnapshot) -> WorkflowSnapshot:
    from ..domain.workflow import workflow_provider_profile_inputs

    dependencies = list(workflow_provider_profile_inputs(snapshot.nodes))
    frozen = snapshot.model_copy(deep=True)
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
