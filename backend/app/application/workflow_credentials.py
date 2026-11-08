"""Apply write-only key updates before publishing the one authoritative graph file."""

from ..domain.node_registry import NODE_DEFINITIONS
from .workflow_connections import ConnectionSnapshotError, credential_operation


def prepare_workflow_credentials(store, project_id, nodes, updates, request_id, expected_revision):
    prepared = [node.model_copy(deep=True) for node in nodes]
    by_id = {node.id: node for node in prepared}
    if updates and not request_id:
        raise ConnectionSnapshotError(
            "CREDENTIAL_REQUEST_ID_REQUIRED", "保存密钥需要请求标识，请重试"
        )
    seen = set()
    for update in updates:
        node = by_id.get(update.node_id)
        definition = NODE_DEFINITIONS.get(node.type) if node else None
        presentation = definition.presentation or {} if definition else {}
        binding = presentation.get("inline_connection_input")
        if node is None or node.id in seen or binding is None:
            raise ConnectionSnapshotError(
                "CREDENTIAL_UPDATE_INVALID", "密钥更新必须对应一个节点内连接"
            )
        seen.add(node.id)
        ref_key = binding["credential_ref_key"]
        if update.operation == "clear":
            node.config.pop(ref_key, None)
        else:
            provider_type = binding["provider_type"]
            node.config[ref_key] = credential_operation(
                store,
                "create",
                project_id,
                node.id,
                provider_type,
                update.api_key.get_secret_value(),
                request_id=f"{request_id}:{expected_revision}",
            )
    # References received through undo/import/copy must pass the same ownership
    # checks as keys created above, including inactive inline settings.
    for node in prepared:
        definition = NODE_DEFINITIONS.get(node.type)
        presentation = definition.presentation or {} if definition else {}
        binding = presentation.get("inline_connection_input")
        if binding is None:
            continue
        ref = node.config.get(binding["credential_ref_key"])
        if ref is not None:
            credential_operation(
                store,
                "validate_ref",
                project_id,
                node.id,
                binding["provider_type"],
                ref,
            )
    return prepared
