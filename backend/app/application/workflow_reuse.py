"""Explicit, validated reuse plans for a saved workflow graph."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..domain.node_registry import NODE_DEFINITIONS
from ..domain.workflow import WorkflowNodeStatus, WorkflowRerun, WorkflowRunStatus
from .workflow_checkpoints import capture_source_identity, load_checkpoint


class ReuseIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    restart_node_ids: list[str] = Field(default_factory=list)


class ReusePlan(BaseModel):
    valid: bool = True
    execute_node_ids: list[str] = Field(default_factory=list)
    reuse_node_ids: list[str] = Field(default_factory=list)
    skipped_node_ids: list[str] = Field(default_factory=list)
    issues: list[ReuseIssue] = Field(default_factory=list)


class ReuseError(ValueError):
    def __init__(self, plan):
        self.plan = plan
        super().__init__(plan.issues[0].message)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def execution_config(node, inputs, record=None):
    definition = NODE_DEFINITIONS[node.type]
    config = deepcopy(node.config)
    for item in definition.fields:
        if item.input_port is not None and item.input_port in inputs:
            value = inputs[item.input_port].value
            from ..domain.node_registry import PORT_DEFINITIONS, TYPE_DEFINITIONS

            type_id = inputs[item.input_port].type_id or PORT_DEFINITIONS[item.input_port].type_id
            projection = TYPE_DEFINITIONS[type_id].config_value
            config[item.key] = deepcopy(projection(value) if projection else value)
    json.dumps(config, allow_nan=False)
    if record is not None:
        record.effective_config = deepcopy(config)
    if definition.validate_config:
        definition.validate_config(config)
    if definition.normalize_config:
        config = definition.normalize_config(config)
        if not isinstance(config, dict):
            raise ValueError("normalized configuration must be an object")
    if definition.validate_config:
        definition.validate_config(config)
    json.dumps(config, allow_nan=False)
    if definition.validate_execution_config:
        definition.validate_execution_config(config, frozenset())
    if record is not None:
        record.effective_config = deepcopy(config)
    return config


def node_fingerprint(node, effective_config, incoming, checkpoints, source_identity):
    dependencies = []
    for edge in incoming:
        checkpoint = checkpoints.get(edge.source)
        if checkpoint is None:
            return None
        dependencies.append(
            {
                "source_node_id": edge.source,
                "source_port": edge.source_port,
                "target_port": edge.target_port,
                "fingerprint": checkpoint["fingerprint"],
                "output_digest": checkpoint["output_digest"],
            }
        )
    dependencies.sort(
        key=lambda item: (item["target_port"], item["source_node_id"], item["source_port"])
    )
    return digest(
        {
            "version": "1",
            "node_id": node.id,
            "node_type": node.type,
            "execution_version": NODE_DEFINITIONS[node.type].execution_version,
            "config": node.config,
            "effective_config": effective_config,
            "incoming": dependencies,
            "source_identity": source_identity,
        }
    )


def graph_sets(snapshot, from_node_id):
    ids = [node.id for node in snapshot.nodes]
    if from_node_id not in ids:
        raise ValueError("RERUN_NODE_NOT_FOUND")
    incoming, outgoing = {}, {}
    for edge in snapshot.edges:
        incoming.setdefault(edge.target, []).append(edge)
        outgoing.setdefault(edge.source, []).append(edge.target)
    execute = {from_node_id}
    queue = [from_node_id]
    while queue:
        for target in outgoing.get(queue.pop(), []):
            if target not in execute:
                execute.add(target)
                queue.append(target)
    ancestors = set()
    queue = list(execute)
    while queue:
        for edge in incoming.get(queue.pop(), []):
            if edge.source not in execute and edge.source not in ancestors:
                ancestors.add(edge.source)
                queue.append(edge.source)
    configuration = {
        node.id
        for node in snapshot.nodes
        if node.id in ancestors and NODE_DEFINITIONS[node.type].evaluation_kind == "configuration"
    }
    execute.update(configuration)
    ancestors.difference_update(configuration)
    return ReusePlan(
        execute_node_ids=[i for i in ids if i in execute],
        reuse_node_ids=[i for i in ids if i in ancestors],
        skipped_node_ids=[i for i in ids if i not in execute | ancestors],
    )


def reject(plan, code, message, node_id=None, restart_node_ids=None):
    plan.valid = False
    plan.issues.append(
        ReuseIssue(
            code=code,
            message=message,
            node_id=node_id,
            restart_node_ids=restart_node_ids
            if restart_node_ids is not None
            else [node_id]
            if node_id
            else [],
        )
    )
    raise ReuseError(plan)


@dataclass
class PreparedReuse:
    plan: ReusePlan
    source: object
    source_identity: dict

    def metadata(self, request):
        return WorkflowRerun(
            **request.model_dump(), **self.plan.model_dump(exclude={"valid", "issues"})
        )


async def prepare_reuse(store, project_id, audio_id, snapshot, request):
    from ..domain.checkpoint import CheckpointDescriptor
    from ..domain.node_execution import NodeData
    from .workflow_runner import _dependency_edges

    try:
        plan = graph_sets(snapshot, request.from_node_id)
    except ValueError:
        reject(ReusePlan(), "RERUN_NODE_NOT_FOUND", "所选节点不存在", request.from_node_id)
    try:
        source = store.load_workflow_run(project_id, audio_id, request.source_run_id)
    except (OSError, ValueError, FileNotFoundError):
        reject(plan, "RERUN_SOURCE_NOT_FOUND", "来源运行不存在或不属于当前音频")
    if source.status in {
        WorkflowRunStatus.PENDING,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.CANCELLING,
    }:
        reject(plan, "RERUN_SOURCE_ACTIVE", "来源运行尚未结束")
    source_ids = [node.id for node in snapshot.nodes if NODE_DEFINITIONS[node.type].is_source]
    event = asyncio.Event()
    try:
        identity = await capture_source_identity(store, source, event)
    except (OSError, ValueError):
        reject(
            plan,
            "RERUN_SOURCE_CHANGED",
            "源音频不可用或已改变",
            source_ids[0] if source_ids else None,
            source_ids,
        )
    if source.source_identity is None and plan.reuse_node_ids:
        reject(
            plan,
            "RERUN_CHECKPOINT_UNAVAILABLE",
            "旧运行没有结果检查点，请先完整运行当前工作流",
            source_ids[0] if source_ids else None,
            source_ids,
        )
    if source.source_identity != identity and plan.reuse_node_ids:
        reject(
            plan,
            "RERUN_SOURCE_CHANGED",
            "源音频或文件信息已改变，请从上游重新运行",
            source_ids[0] if source_ids else None,
            source_ids,
        )
    records = {record.node_id: record for record in source.nodes}
    node_by_id = {node.id: node for node in snapshot.nodes}
    # Topology depends only on the immutable graph object, not persisted records.
    topology = source.model_copy(update={"workflow": snapshot})
    incoming, order = _dependency_edges(topology)
    outputs, checkpoints = {}, {}
    for node_id in order:
        if (
            node_id in plan.execute_node_ids
            and NODE_DEFINITIONS[node_by_id[node_id].type].evaluation_kind == "configuration"
        ):
            from .configuration_evaluator import (
                configuration_descriptor,
                evaluate_configuration_graph,
            )

            try:
                result = (
                    await evaluate_configuration_graph(
                        store,
                        topology,
                        store.load_project(project_id),
                        event,
                        required_ids={node_id},
                        outputs=outputs,
                    )
                ).get(node_id)
                if result is not None:
                    inputs = {
                        edge.target_port: NodeData(
                            edge.target_port,
                            outputs[edge.source][edge.source_port].value,
                            outputs[edge.source][edge.source_port].type_id,
                        )
                        for edge in incoming.get(node_id, [])
                    }
                    effective = execution_config(node_by_id[node_id], inputs)
                    fingerprint = node_fingerprint(
                        node_by_id[node_id],
                        effective,
                        incoming.get(node_id, []),
                        checkpoints,
                        identity,
                    )
                    outputs[node_id] = result.outputs
                    if fingerprint:
                        checkpoints[node_id] = await configuration_descriptor(result, fingerprint)
            except Exception as error:
                from ..domain.node_execution import NodeExecutionCancelled, NodeExecutionError

                if isinstance(error, NodeExecutionCancelled):
                    raise
                if isinstance(error, NodeExecutionError):
                    reject(plan, error.code, str(error), node_id)
                reject(
                    plan, "RERUN_CONFIGURATION_INVALID", "配置无法重新求值，请检查配置连线", node_id
                )
            continue
        if node_id not in plan.reuse_node_ids:
            continue
        node = node_by_id[node_id]
        record = records.get(node_id)
        if record is None or record.status != WorkflowNodeStatus.COMPLETED or not record.checkpoint:
            reject(plan, "RERUN_CHECKPOINT_UNAVAILABLE", "上游没有可复用的已完成结果", node_id)
        try:
            inputs = {
                edge.target_port: NodeData(
                    edge.target_port,
                    outputs[edge.source][edge.source_port].value,
                    type_id=outputs[edge.source][edge.source_port].type_id,
                )
                for edge in incoming.get(node_id, [])
            }
            effective = execution_config(node, inputs)
            fingerprint = node_fingerprint(
                node, effective, incoming.get(node_id, []), checkpoints, identity
            )
            if record.node_type != node.type or fingerprint != record.checkpoint["fingerprint"]:
                reject(
                    plan,
                    "RERUN_UPSTREAM_CHANGED",
                    "上游设置、连接或实现已改变，请从该节点重新运行",
                    node_id,
                )
            result = await load_checkpoint(
                store, source, node, CheckpointDescriptor.model_validate(record.checkpoint), event
            )
            outputs[node_id] = result.outputs
            checkpoints[node_id] = record.checkpoint
        except ReuseError:
            raise
        except Exception:
            reject(plan, "RERUN_CHECKPOINT_INVALID", "上游结果缺失、损坏或不支持复用", node_id)
    return PreparedReuse(plan, source, identity)
