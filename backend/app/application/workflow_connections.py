"""Freeze node-owned connections and immutable credentials at submission."""

from __future__ import annotations

from ..domain.inline_connection import InlineConnection, LegacyConnectionConfigError


class ConnectionSnapshotError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def reject_connection_validation_errors(issues):
    for issue in issues:
        if issue.code == LegacyConnectionConfigError.code:
            raise ConnectionSnapshotError(issue.code, issue.message)


def credential_operation(store, method, *args, **kwargs):
    from ..storage.secret_store import SecretStore, SecretStoreError

    if store is None:
        raise ConnectionSnapshotError("CREDENTIAL_STORAGE_ERROR", "凭据存储不可用")
    try:
        return getattr(SecretStore(store.data_dir), method)(*args, **kwargs)
    except SecretStoreError as error:
        raise ConnectionSnapshotError(error.code, str(error)) from None


def _connection(node, binding):
    try:
        return InlineConnection(
            provider_type=binding["provider_type"],
            base_url=node.config.get(binding["base_url_key"]),
            default_timeout=node.config.get(binding["timeout_key"]),
            region=node.config.get(binding["region_key"]) if binding["region_key"] else None,
        )
    except ValueError:
        raise ConnectionSnapshotError(
            "INLINE_CONNECTION_INVALID", "节点连接设置无效或不完整"
        ) from None


def _validate_node_config(node):
    from ..domain.node_registry import NODE_DEFINITIONS

    definition = NODE_DEFINITIONS[node.type]
    try:
        if definition.validate_config is not None:
            definition.validate_config(node.config)
    except LegacyConnectionConfigError as error:
        raise ConnectionSnapshotError(error.code, str(error)) from None
    except ValueError:
        raise ConnectionSnapshotError(
            "INLINE_CONNECTION_INVALID", "节点连接设置无效或不完整"
        ) from None
    return definition


def validate_frozen_workflow_connections(snapshot, store=None, project_id=None) -> None:
    from ..domain.workflow import workflow_inline_connection_inputs

    for node, binding, provider_type in workflow_inline_connection_inputs(snapshot.nodes):
        _validate_node_config(node)
        _connection(node, binding)
        ref = node.config.get(binding["credential_ref_key"])
        if not ref:
            raise ConnectionSnapshotError(
                "CREDENTIAL_NOT_FOUND", "运行缺少固定密钥版本，请重新提交"
            )
        credential_operation(store, "validate_ref", project_id, node.id, provider_type, ref)


def freeze_workflow_connections(store, snapshot, project_id=None):
    from ..domain.workflow import workflow_inline_connection_inputs

    frozen = snapshot.model_copy(deep=True)
    for node, binding, provider_type in workflow_inline_connection_inputs(frozen.nodes):
        definition = _validate_node_config(node)
        if definition.normalize_config is not None:
            node.config = definition.normalize_config(dict(node.config))
        _connection(node, binding)
        ref = node.config.get(binding["credential_ref_key"])
        if not ref:
            raise ConnectionSnapshotError("CREDENTIAL_REQUIRED", "请在此节点中保存 API Key")
        credential_operation(store, "validate_ref", project_id, node.id, provider_type, ref)
    return frozen


def inline_connection_from_snapshot(store, snapshot, node_id, project_id):
    from ..domain.workflow import workflow_inline_connection_inputs

    for node, binding, provider_type in workflow_inline_connection_inputs(snapshot.nodes):
        if node.id != node_id:
            continue
        _validate_node_config(node)
        connection = _connection(node, binding)
        ref = node.config.get(binding["credential_ref_key"])
        if not ref:
            raise ConnectionSnapshotError("CREDENTIAL_NOT_FOUND", "运行缺少固定密钥版本")
        key = credential_operation(store, "read", project_id, node.id, provider_type, ref)
        return connection, key
    raise ConnectionSnapshotError("INLINE_CONNECTION_INVALID", "运行缺少节点连接设置")
