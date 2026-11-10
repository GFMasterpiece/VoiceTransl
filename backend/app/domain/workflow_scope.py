"""One source-branch planner shared by submission, execution and reuse."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceScope:
    source_node_id: str
    node_ids: frozenset[str]


def source_nodes(nodes):
    from .node_registry import NODE_DEFINITIONS

    return [
        node
        for node in nodes
        if NODE_DEFINITIONS.get(node.type) and NODE_DEFINITIONS[node.type].is_source
    ]


def source_origins(nodes, edges):
    sources = {node.id for node in source_nodes(nodes)}
    origins = {node.id: ({node.id} if node.id in sources else set()) for node in nodes}
    for _ in nodes:
        changed = False
        for edge in edges:
            if edge.source in origins and edge.target in origins:
                before = len(origins[edge.target])
                origins[edge.target].update(origins[edge.source])
                changed |= before != len(origins[edge.target])
        if not changed:
            break
    return origins


def plan_source_scope(snapshot, source_node_id=None):
    from .node_registry import NODE_DEFINITIONS

    sources = source_nodes(snapshot.nodes)
    if source_node_id is None:
        if len(sources) != 1:
            raise ValueError("SOURCE_NODE_REQUIRED")
        source_node_id = sources[0].id
    if source_node_id not in {node.id for node in sources}:
        raise ValueError("SOURCE_NODE_NOT_FOUND")
    origins = source_origins(snapshot.nodes, snapshot.edges)
    if any(len(value) > 1 for value in origins.values()):
        raise ValueError("SOURCE_SCOPE_MERGE")
    if len(sources) == 1:
        return SourceScope(source_node_id, frozenset(node.id for node in snapshot.nodes))
    needed = {node_id for node_id, value in origins.items() if source_node_id in value}
    while True:
        ancestors = {edge.source for edge in snapshot.edges if edge.target in needed}
        extra = ancestors - needed
        if not extra:
            break
        for node in snapshot.nodes:
            if node.id in extra and NODE_DEFINITIONS[node.type].evaluation_kind != "configuration":
                raise ValueError("SOURCE_SCOPE_PROCESSING_DEPENDENCY")
        needed.update(extra)
    return SourceScope(source_node_id, frozenset(needed))


def workflow_group_references(nodes):
    from .node_registry import NODE_DEFINITIONS

    references = []
    for node in nodes:
        definition = NODE_DEFINITIONS.get(node.type)
        binding = (definition.presentation or {}).get("resource_input") if definition else None
        if not binding:
            continue
        key = binding.get("group_key") or (
            binding["config_key"] if binding["resource_type"] == "audio_group" else None
        )
        if key and isinstance(node.config.get(key), str) and node.config[key]:
            references.append((node.id, node.config[key]))
    return references


def run_source_summary(run):
    """Display provenance from history alone, without touching live resources."""
    sources = source_nodes(run.workflow.nodes)
    source_id = run.source_node_id
    if source_id is None and len(sources) == 1:
        source_id = sources[0].id
    source = next((node for node in sources if node.id == source_id), None)
    group_name = None
    if source:
        references = workflow_group_references([source])
        if references:
            group = run.workflow.group_snapshots.get(references[0][1])
            if group:
                group_name = group["name"]
    return {"source_node_id": source_id, "source_group_name": group_name}
