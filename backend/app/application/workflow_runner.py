import asyncio
import logging
from datetime import datetime, timezone

from ..domain import (
    WorkflowArtifact,
    WorkflowEdge,
    WorkflowNodeError,
    WorkflowNodeStatus,
    WorkflowRun,
    WorkflowRunError,
    WorkflowRunStatus,
)
from ..domain.node_execution import (
    NodeData,
    NodeExecutionCancelled,
    NodeExecutionContext,
    NodeExecutionError,
    NodeResult,
)
from ..domain.node_registry import (
    NODE_DEFINITIONS,
    TYPE_DEFINITIONS,
    resolve_port,
    resolved_input_ports,
    type_is_compatible,
    validate_node_result,
    validate_port_value,
)
from ..domain.workflow import WorkflowNodeRecord
from .workflow_checkpoints import capture_source_identity, create_checkpoint, load_checkpoint
from .workflow_media import (
    WorkflowRunCancelled as WorkflowRunCancelled,
)
from .workflow_media import (
    _copy_audio as _copy_audio,
)
from .workflow_reuse import execution_config, node_fingerprint

logger = logging.getLogger("voicetransl.workflow")


def now():
    return datetime.now(timezone.utc)


def _dependency_edges(run: WorkflowRun) -> tuple[dict[str, list[WorkflowEdge]], list[str]]:
    incoming: dict[str, list[WorkflowEdge]] = {}
    order: list[str] = []
    outgoing: dict[str, list[str]] = {}
    indegree: dict[str, int] = {}
    for node in run.workflow.nodes:
        indegree[node.id] = 0
        outgoing[node.id] = []
    for edge in run.workflow.edges:
        incoming.setdefault(edge.target, []).append(edge)
        outgoing[edge.source].append(edge.target)
        indegree[edge.target] += 1
    queue = [node.id for node in run.workflow.nodes if indegree[node.id] == 0]
    while queue:
        node_id = queue.pop(0)
        order.append(node_id)
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(indegree):
        raise ValueError("workflow graph has a cycle")
    return incoming, order


async def _run_registered_node(context: NodeExecutionContext) -> NodeResult:
    definition = NODE_DEFINITIONS.get(context.node.type)
    if definition is None or definition.execute is None:
        raise RuntimeError("NODE_EXECUTOR_UNAVAILABLE")
    if not set(definition.required_ports) <= set(context.inputs):
        raise ValueError("node input is missing a required port")
    for port, data in context.inputs.items():
        if (
            port not in resolved_input_ports(definition, context.node.config)
            or data.port != port
            or (data.type_id is None and not validate_port_value(port, data.value, context.node))
            or (
                data.type_id is not None
                and (
                    data.type_id not in TYPE_DEFINITIONS
                    or not type_is_compatible(
                        data.type_id, resolve_port(port, context.node).type_id
                    )
                    or not TYPE_DEFINITIONS[data.type_id].is_valid(data.value)
                    or not resolve_port(port, context.node).capability_requirements
                    <= TYPE_DEFINITIONS[data.type_id].capabilities
                )
            )
        ):
            raise TypeError("node input violates its declared port contract")
    from ..domain.configuration import validate_model

    for port, operation in definition.binding_inputs.items():
        await validate_model(context, context.input(port).value, operation)
    if definition.validate_inputs:
        definition.validate_inputs(context)
    if definition.evaluation_kind == "configuration":
        from .configuration_evaluator import evaluate_configuration_graph

        seed = {key: dict(value) for key, value in (context.available_outputs or {}).items()}
        seed.update(
            {
                edge.source: {}
                for edge in context.run.workflow.edges
                if edge.target == context.node.id
            }
        )
        for edge in context.run.workflow.edges:
            if edge.target == context.node.id:
                source = context.inputs[edge.target_port]
                seed[edge.source][edge.source_port] = NodeData(
                    edge.source_port, source.value, source.type_id
                )
        result = (
            await evaluate_configuration_graph(
                context.store,
                context.run,
                context.project,
                context.cancel_event,
                required_ids={context.node.id},
                outputs=seed,
            )
        )[context.node.id]
    else:
        result = await definition.execute(context)
    validate_node_result(definition, result)
    if result.artifact is not None and not isinstance(result.artifact, WorkflowArtifact):
        raise TypeError("node artifact must be WorkflowArtifact")
    if result.preview is not None:
        from ..domain.workflow import WorkflowPreview

        if not isinstance(result.preview, WorkflowPreview) or result.artifact is None:
            raise TypeError("node preview requires a descriptor and persistent artifact")
    return result


def _execution_node(node, definition, inputs, record: WorkflowNodeRecord):
    """Resolve into a separate node, preserving manual settings and graph snapshots."""
    try:
        config = execution_config(node, inputs, record)
    except (TypeError, ValueError) as error:
        raise NodeExecutionError(
            "INVALID_NODE_CONFIG", f"{node.type} 执行配置无效: {error}"
        ) from error
    return node.model_copy(update={"config": config}, deep=True)


async def execute_workflow_run(
    store,
    run: WorkflowRun,
    cancel_event: asyncio.Event,
    *,
    mock_delay_ms: int = 0,
) -> WorkflowRun:
    run.status = WorkflowRunStatus.RUNNING
    run.started_at = now()
    run.finished_at = None
    run.error = None
    store.save_workflow_run(run)

    records = {node.node_id: node for node in run.nodes}
    node_by_id = {node.id: node for node in run.workflow.nodes}
    execution_ids = set(run.rerun.execute_node_ids) if run.rerun else set(node_by_id)
    output_ids = [
        node.id
        for node in run.workflow.nodes
        if node.id in execution_ids
        and NODE_DEFINITIONS.get(node.type) is not None
        and NODE_DEFINITIONS[node.type].terminal
    ]
    if not output_ids:
        output_ids = list(execution_ids)
    input_nodes = [
        node
        for node in run.workflow.nodes
        if NODE_DEFINITIONS.get(node.type) is not None and NODE_DEFINITIONS[node.type].is_source
    ]
    input_record = records.get(input_nodes[0].id) if input_nodes else None
    incoming, order = _dependency_edges(run)

    try:
        project = store.load_project(run.project_id)
        identity = await capture_source_identity(store, run, cancel_event)
    except NodeExecutionCancelled:
        run.cancel_requested = True
        run.status = WorkflowRunStatus.CANCELLED
        run.finished_at = now()
        for record in run.nodes:
            if record.status in {WorkflowNodeStatus.PENDING, WorkflowNodeStatus.RUNNING}:
                record.status = WorkflowNodeStatus.CANCELLED
                record.finished_at = run.finished_at
        store.save_workflow_run(run)
        return run
    except Exception:
        if input_record is not None and input_record.status in {
            WorkflowNodeStatus.PENDING,
            WorkflowNodeStatus.RUNNING,
        }:
            input_record.status = WorkflowNodeStatus.FAILED
            input_record.started_at = input_record.started_at or now()
            input_record.finished_at = now()
            input_record.error = WorkflowNodeError(
                type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用"
            )
        for record in run.nodes:
            if record is not input_record and record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
            }:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="DEPENDENCY_BLOCKED", message="上游音频不可用，节点未执行"
                )
        run.status = WorkflowRunStatus.FAILED
        run.finished_at = now()
        run.error = WorkflowRunError(
            type="RERUN_SOURCE_CHANGED" if run.rerun else "INPUT_SOURCE_UNAVAILABLE",
            message="源音频不可用",
        )
        store.save_workflow_run(run)
        return run

    outputs_by_node: dict[str, dict[str, NodeData]] = {}
    failed_or_blocked: set[str] = set()
    try:
        if run.rerun and identity != run.source_identity:
            raise NodeExecutionError("RERUN_SOURCE_CHANGED", "排队期间源音频或文件信息已改变")
        run.source_identity = identity
        store.save_workflow_run(run)
        checkpoints = {}
        if run.rerun:
            from ..domain.checkpoint import CheckpointDescriptor

            for node_id in (node_id for node_id in order if node_id in run.rerun.reuse_node_ids):
                record = records[node_id]
                try:
                    result = await load_checkpoint(
                        store,
                        run,
                        node_by_id[node_id],
                        CheckpointDescriptor.model_validate(record.checkpoint),
                        cancel_event,
                        available_outputs=outputs_by_node,
                    )
                except (OSError, ValueError, TypeError):
                    raise NodeExecutionError(
                        "RERUN_CHECKPOINT_INVALID", "排队复用结果缺失或损坏，请重新提交"
                    ) from None
                outputs_by_node[node_id] = result.outputs
                checkpoints[node_id] = record.checkpoint
        for node_id in order:
            if node_id not in execution_ids:
                continue
            if cancel_event.is_set():
                raise WorkflowRunCancelled
            node = node_by_id[node_id]
            record = records[node_id]
            definition = NODE_DEFINITIONS.get(node.type)
            if definition is None:
                record.status = WorkflowNodeStatus.FAILED
                record.error = WorkflowNodeError(type="UNKNOWN_NODE_TYPE", message="节点类型未注册")
                failed_or_blocked.add(node.id)
                continue
            inputs: dict[str, NodeData] = {}
            blocked = False
            for edge in incoming.get(node_id, []):
                data = outputs_by_node.get(edge.source, {}).get(edge.source_port)
                if edge.source in failed_or_blocked or data is None:
                    blocked = True
                    break
                inputs[edge.target_port] = NodeData(
                    edge.target_port, data.value, type_id=data.type_id
                )
            if blocked:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="DEPENDENCY_BLOCKED", message="上游节点未成功，节点未执行"
                )
                failed_or_blocked.add(node.id)
                store.save_workflow_run(run)
                continue
            if cancel_event.is_set():
                raise WorkflowRunCancelled
            record.finished_at = None
            record.error = None
            record.execution_kind = "executed"
            record.status = WorkflowNodeStatus.RUNNING
            record.started_at = now()
            store.save_workflow_run(run)
            try:
                execution_node = _execution_node(node, definition, inputs, record)
                store.save_workflow_run(run)
                result = await _run_registered_node(
                    NodeExecutionContext(
                        store=store,
                        run=run,
                        node=execution_node,
                        inputs=inputs,
                        project=project,
                        cancel_event=cancel_event,
                        mock_delay_ms=mock_delay_ms,
                        available_outputs=outputs_by_node,
                    )
                )
                fingerprint = node_fingerprint(
                    node,
                    execution_node.config,
                    incoming.get(node_id, []),
                    checkpoints,
                    run.source_identity,
                )
                descriptor = (
                    await create_checkpoint(store, run, node, result, fingerprint, cancel_event)
                    if fingerprint
                    else None
                )
                if descriptor is not None:
                    record.checkpoint = descriptor.model_dump(mode="json")
                    checkpoints[node_id] = record.checkpoint
                record.artifact = result.artifact
                record.preview = result.preview
                outputs_by_node[node.id] = result.outputs
                record.status = WorkflowNodeStatus.COMPLETED
                record.finished_at = now()
                store.save_workflow_run(run)
            except NodeExecutionCancelled as error:
                if error.code is not None or error.message is not None:
                    record.error = WorkflowNodeError(
                        type=error.code or "NODE_CANCELLED",
                        message=error.message or "节点运行已取消",
                    )
                raise
            except NodeExecutionError as error:
                record.status = WorkflowNodeStatus.FAILED
                record.finished_at = now()
                record.error = WorkflowNodeError(type=error.code, message=str(error))
                failed_or_blocked.add(node.id)
            except Exception:
                record.status = WorkflowNodeStatus.FAILED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="INPUT_SOURCE_UNAVAILABLE"
                    if definition.is_source
                    else "NODE_EXECUTION_FAILED",
                    message="源音频不可用" if definition.is_source else "节点执行失败",
                )
                failed_or_blocked.add(node.id)
            store.save_workflow_run(run)
    except NodeExecutionCancelled:
        run.cancel_requested = True
        run.status = WorkflowRunStatus.CANCELLED
        run.finished_at = now()
        for record in run.nodes:
            if record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
                WorkflowNodeStatus.BLOCKED,
            }:
                record.status = WorkflowNodeStatus.CANCELLED
                record.finished_at = run.finished_at
        store.save_workflow_run(run)
        return run
    except asyncio.CancelledError:
        raise
    except NodeExecutionError as error:
        run.status = WorkflowRunStatus.FAILED
        run.error = WorkflowRunError(type=error.code, message=str(error))
        for record in run.nodes:
            if record.status in {WorkflowNodeStatus.PENDING, WorkflowNodeStatus.RUNNING}:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
    except Exception:
        logger.exception("workflow run failed run=%s", run.id)
        run.status = WorkflowRunStatus.FAILED
        run.error = WorkflowRunError(type="WORKFLOW_RUN_FAILED", message="工作流运行失败")
        for record in run.nodes:
            if record.status in {
                WorkflowNodeStatus.PENDING,
                WorkflowNodeStatus.RUNNING,
                WorkflowNodeStatus.BLOCKED,
            }:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="WORKFLOW_RUN_FAILED", message="工作流运行失败，节点未完成"
                )
    else:
        successes = sum(
            records[node_id].status == WorkflowNodeStatus.COMPLETED for node_id in output_ids
        )
        if successes == len(output_ids):
            run.status = WorkflowRunStatus.COMPLETED
        elif successes:
            run.status = WorkflowRunStatus.PARTIAL
            run.error = WorkflowRunError(type="PARTIAL_FAILURE", message="部分输出节点执行失败")
        else:
            run.status = WorkflowRunStatus.FAILED
            if (
                input_record is not None
                and input_record.error is not None
                and input_record.error.type == "INPUT_SOURCE_UNAVAILABLE"
            ):
                run.error = WorkflowRunError(
                    type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用"
                )
            else:
                run.error = WorkflowRunError(
                    type="ALL_OUTPUTS_FAILED", message="全部输出节点执行失败"
                )
    run.finished_at = now()
    store.save_workflow_run(run)
    return run
