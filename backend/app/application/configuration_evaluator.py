"""One pure configuration interpreter for submission, execution and reuse."""

from ..domain.node_execution import NodeData, NodeExecutionContext, NodeExecutionError
from ..domain.node_registry import NODE_DEFINITIONS, validate_node_result
from .workflow_reuse import execution_config


def configuration_ids(snapshot, required_ids=None):
    needed = set(required_ids) if required_ids is not None else {node.id for node in snapshot.nodes}
    while True:
        previous = len(needed)
        needed.update(edge.source for edge in snapshot.edges if edge.target in needed)
        if len(needed) == previous:
            return needed


async def evaluate_configuration_graph(
    store,
    run,
    project,
    cancel_event,
    required_ids=None,
    outputs=None,
    *,
    validate_processing_nodes=True,
):
    from ..domain.configuration import validate_model
    from .workflow_runner import _dependency_edges

    needed = configuration_ids(run.workflow, required_ids)
    values = dict(outputs or {})
    results = {}
    nodes = {node.id: node for node in run.workflow.nodes}
    incoming, order = _dependency_edges(run)
    for node_id in order:
        if node_id not in needed:
            continue
        node = nodes[node_id]
        definition = NODE_DEFINITIONS[node.type]
        if definition.evaluation_kind == "configuration" and node_id in values:
            continue
        inputs = {
            edge.target_port: NodeData(
                edge.target_port,
                values[edge.source][edge.source_port].value,
                values[edge.source][edge.source_port].type_id,
            )
            for edge in incoming.get(node_id, [])
            if edge.source in values and edge.source_port in values[edge.source]
        }
        context = NodeExecutionContext(
            store, run, node, inputs, project, cancel_event, available_outputs=values
        )
        if definition.evaluation_kind == "configuration":
            if len(inputs) != len(incoming.get(node_id, [])):
                continue
            if cancel_event.is_set():
                from ..domain.node_execution import NodeExecutionCancelled

                raise NodeExecutionCancelled()
            config = execution_config(node, inputs)
            context = NodeExecutionContext(
                store,
                run,
                node.model_copy(update={"config": config}, deep=True),
                inputs,
                project,
                cancel_event,
                available_outputs=values,
            )
            result = await definition.evaluate_config(context)
            validate_node_result(definition, result)
            results[node_id] = result
            values[node_id] = result.outputs
        elif not validate_processing_nodes:
            continue
        for port, operation in definition.binding_inputs.items():
            if port in inputs:
                await validate_model(context, inputs[port].value, operation)
        required_validation = {
            edge.target_port
            for edge in incoming.get(node_id, [])
            if edge.target_port in definition.validation_input_ports
        }
        if definition.validate_inputs and required_validation <= set(inputs):
            definition.validate_inputs(context)
    return results


async def configuration_descriptor(result, fingerprint):
    import hashlib

    from ..domain.node_registry import PORT_DEFINITIONS, TYPE_DEFINITIONS
    from .workflow_checkpoints import _json

    document = {
        "version": "1",
        "outputs": {},
        "files": {},
        "artifacts": {},
        "artifact": None,
        "preview": None,
    }
    for port in sorted(result.outputs):
        data = result.outputs[port]
        type_id = data.type_id or PORT_DEFINITIONS[port].type_id
        codec = TYPE_DEFINITIONS[type_id].checkpoint_codec
        if codec is None:
            return None
        document["outputs"][port] = {
            "type_id": type_id,
            "codec_id": codec.id,
            "codec_version": codec.version,
            "payload": await codec.encode(data.value, None),
        }
    payload = _json(document)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "version": "1",
        "fingerprint": fingerprint,
        "output_digest": digest,
        "sha256": digest,
        "size_bytes": len(payload),
    }


async def validate_configuration_snapshot(
    store, snapshot, project_id, audio_id, node_ids=None, source_node_id=None
):
    import asyncio
    from types import SimpleNamespace

    run = SimpleNamespace(
        project_id=project_id, audio_id=audio_id, workflow=snapshot, source_node_id=source_node_id
    )
    try:
        return await evaluate_configuration_graph(
            store, run, store.load_project(project_id), asyncio.Event(), required_ids=node_ids
        )
    except NodeExecutionError as error:
        from .workflow_connections import ConnectionSnapshotError

        raise ConnectionSnapshotError(error.code, str(error)) from None
    except (TypeError, ValueError):
        from .workflow_connections import ConnectionSnapshotError

        raise ConnectionSnapshotError("CONFIGURATION_INVALID", "工作流配置组合无效") from None
