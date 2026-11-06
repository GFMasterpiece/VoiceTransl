import asyncio
import json
import logging
from copy import deepcopy
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
    validate_node_result,
    validate_port_value,
)
from ..domain.workflow import WorkflowNodeRecord
from .workflow_media import (
    WorkflowRunCancelled as WorkflowRunCancelled,
)
from .workflow_media import (
    _copy_audio as _copy_audio,
)

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
            port not in definition.input_ports
            or data.port != port
            or not validate_port_value(port, data.value)
        ):
            raise TypeError("node input violates its declared port contract")
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
    config = deepcopy(node.config)
    for item in definition.fields:
        if item.input_port is not None and item.input_port in inputs:
            config[item.key] = deepcopy(inputs[item.input_port].value)
    try:
        json.dumps(config, allow_nan=False)
        record.effective_config = deepcopy(config)
        if definition.validate_config is not None:
            definition.validate_config(config)
        if definition.normalize_config is not None:
            config = definition.normalize_config(config)
            if not isinstance(config, dict):
                raise ValueError("normalized configuration must be an object")
        if definition.validate_config is not None:
            definition.validate_config(config)
        json.dumps(config, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise NodeExecutionError(
            "INVALID_NODE_CONFIG", f"{node.type} 执行配置无效: {error}"
        ) from error
    record.effective_config = deepcopy(config)
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
    output_ids = [
        node.id
        for node in run.workflow.nodes
        if NODE_DEFINITIONS.get(node.type) is not None and NODE_DEFINITIONS[node.type].terminal
    ]
    input_nodes = [
        node
        for node in run.workflow.nodes
        if NODE_DEFINITIONS.get(node.type) is not None and NODE_DEFINITIONS[node.type].is_source
    ]
    input_record = records.get(input_nodes[0].id) if input_nodes else None
    incoming, order = _dependency_edges(run)

    try:
        project = store.load_project(run.project_id)
    except Exception:
        if input_record is not None:
            input_record.status = WorkflowNodeStatus.FAILED
            input_record.started_at = input_record.started_at or now()
            input_record.finished_at = now()
            input_record.error = WorkflowNodeError(
                type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用"
            )
        for record in run.nodes:
            if record is not input_record:
                record.status = WorkflowNodeStatus.BLOCKED
                record.finished_at = now()
                record.error = WorkflowNodeError(
                    type="DEPENDENCY_BLOCKED", message="上游音频不可用，节点未执行"
                )
        run.status = WorkflowRunStatus.FAILED
        run.finished_at = now()
        run.error = WorkflowRunError(type="INPUT_SOURCE_UNAVAILABLE", message="源音频不可用")
        store.save_workflow_run(run)
        return run

    outputs_by_node: dict[str, dict[str, NodeData]] = {}
    failed_or_blocked: set[str] = set()
    try:
        for node_id in order:
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
                inputs[edge.target_port] = NodeData(edge.target_port, data.value)
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
                    )
                )
                record.artifact = result.artifact
                record.preview = result.preview
                outputs_by_node[node.id] = result.outputs
                record.status = WorkflowNodeStatus.COMPLETED
                record.finished_at = now()
                store.save_workflow_run(run)
            except NodeExecutionCancelled:
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
