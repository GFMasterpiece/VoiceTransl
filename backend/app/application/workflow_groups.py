"""Freeze project group resources without reading mutable groups during execution."""

from ..domain.workflow_scope import plan_source_scope, workflow_group_references


def freeze_group_snapshots(store, snapshot, project_id, node_ids, expected_revisions=None):
    from ..storage.audio_group_store import AudioGroupStore

    groups = AudioGroupStore(store)
    frozen = snapshot.model_copy(deep=True)
    values = dict(frozen.group_snapshots)
    for _, group_id in workflow_group_references(
        [node for node in frozen.nodes if node.id in node_ids]
    ):
        try:
            group = groups.load(project_id, group_id)
        except FileNotFoundError:
            raise ValueError("INPUT_GROUP_NOT_FOUND") from None
        if (
            expected_revisions
            and group_id in expected_revisions
            and group.revision != expected_revisions[group_id]
        ):
            raise RuntimeError("GROUP_REVISION_CONFLICT")
        values[group_id] = group.model_dump(mode="json")
    frozen.group_snapshots = values
    return frozen


def selected_audio_ids(snapshot, source_node_id, context_audio_id=None):
    from ..domain.node_registry import NODE_DEFINITIONS

    scope = plan_source_scope(snapshot, source_node_id)
    source = next(node for node in snapshot.nodes if node.id == scope.source_node_id)
    binding = (NODE_DEFINITIONS[source.type].presentation or {}).get("resource_input") or {}
    group_id = source_group_id(snapshot, scope.source_node_id)
    collection_key = binding.get("collection_key")
    single_key = binding.get("config_key") if binding.get("resource_type") == "audio" else None
    if group_id is not None:
        if not group_id:
            raise ValueError("INPUT_GROUP_REQUIRED")
        group = snapshot.group_snapshots.get(group_id)
        if group is None:
            raise ValueError("GROUP_SNAPSHOT_REQUIRED")
        values = group["audio_ids"]
    elif collection_key and collection_key in source.config:
        values = source.config[collection_key]
    elif single_key and single_key in source.config:
        values = [source.config[single_key]]
    else:
        values = [context_audio_id] if context_audio_id else []
    if not values:
        raise ValueError("INPUT_AUDIO_COLLECTION_EMPTY")
    return list(values)


def source_group_id(snapshot, source_node_id):
    from ..domain.node_registry import NODE_DEFINITIONS

    source = next(node for node in snapshot.nodes if node.id == source_node_id)
    binding = (NODE_DEFINITIONS[source.type].presentation or {}).get("resource_input") or {}
    key = binding.get("group_key")
    return source.config.get(key) if key else None


def validate_source_audio(snapshot, source_node_id, audio_id):
    values = selected_audio_ids(snapshot, source_node_id, audio_id)
    if audio_id not in values:
        raise ValueError("INPUT_AUDIO_MISMATCH")
