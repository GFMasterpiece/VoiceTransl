import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WORKFLOW_SCHEMA_VERSION = "1.0"
DEFAULT_FILENAME_STEM = "audio-copy"
MAX_WORKFLOW_NODES = 64
MAX_WORKFLOW_EDGES = 64
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
EDGE_ID_PATTERN = NODE_ID_PATTERN
PORT_NAME = "audio"


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class WorkflowNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class WorkflowPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(default=0, ge=-1_000_000, le=1_000_000)
    y: float = Field(default=0, ge=-1_000_000, le=1_000_000)


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=64)
    type: str = Field(max_length=64)
    config: dict[str, object] = Field(default_factory=dict)
    position: WorkflowPosition = Field(default_factory=WorkflowPosition)


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=64)
    source: str = Field(max_length=64)
    source_port: str = Field(default=PORT_NAME, max_length=32)
    target: str = Field(max_length=64)
    target_port: str = Field(default=PORT_NAME, max_length=32)


class WorkflowDocument(BaseModel):
    schema_version: str = WORKFLOW_SCHEMA_VERSION
    project_id: str
    revision: int = Field(default=0, ge=0)
    workflow_revision: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    nodes: list[WorkflowNode] = Field(default_factory=list, max_length=MAX_WORKFLOW_NODES)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=MAX_WORKFLOW_EDGES)


class WorkflowSnapshot(BaseModel):
    schema_version: str = WORKFLOW_SCHEMA_VERSION
    revision: int = Field(ge=0)
    workflow_revision: int = Field(ge=0)
    nodes: list[WorkflowNode] = Field(default_factory=list, max_length=MAX_WORKFLOW_NODES)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=MAX_WORKFLOW_EDGES)


class WorkflowValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class WorkflowValidationResponse(BaseModel):
    valid: bool
    errors: list[WorkflowValidationIssue] = Field(default_factory=list)


class WorkflowArtifact(BaseModel):
    artifact_id: str
    port: Literal["audio"] = PORT_NAME
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str
    download_url: str | None = None


class WorkflowNodeError(BaseModel):
    type: str
    message: str


class WorkflowNodeRecord(BaseModel):
    node_id: str
    node_type: str
    config: dict[str, object] = Field(default_factory=dict)
    status: WorkflowNodeStatus = WorkflowNodeStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    artifact: WorkflowArtifact | None = None
    error: WorkflowNodeError | None = None


class WorkflowRunError(BaseModel):
    type: str
    message: str
    node_id: str | None = None


class WorkflowRun(BaseModel):
    schema_version: str = WORKFLOW_SCHEMA_VERSION
    id: str
    project_id: str
    audio_id: str
    workflow_revision: int = Field(ge=0)
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    error: WorkflowRunError | None = None
    workflow: WorkflowSnapshot
    nodes: list[WorkflowNodeRecord] = Field(default_factory=list)


class WorkflowRunSummary(BaseModel):
    id: str
    status: WorkflowRunStatus
    workflow_revision: int
    created_at: datetime
    finished_at: datetime | None
    error: WorkflowRunError | None = None


class WorkflowRunList(BaseModel):
    items: list[WorkflowRunSummary]
    total: int
    limit: int
    offset: int


def default_workflow(project_id: str, updated_at: datetime | None = None) -> WorkflowDocument:
    return WorkflowDocument(
        project_id=project_id,
        updated_at=updated_at or datetime.now(timezone.utc),
        nodes=[
            WorkflowNode(
                id="input",
                type="audio_input",
                config={},
                position=WorkflowPosition(x=80, y=120),
            ),
            WorkflowNode(
                id="output",
                type="audio_output",
                config={"filename_stem": DEFAULT_FILENAME_STEM},
                position=WorkflowPosition(x=420, y=120),
            ),
        ],
        edges=[
            WorkflowEdge(
                id="input-output",
                source="input",
                source_port=PORT_NAME,
                target="output",
                target_port=PORT_NAME,
            )
        ],
    )


def workflow_snapshot(document: WorkflowDocument) -> WorkflowSnapshot:
    return WorkflowSnapshot(
        schema_version=document.schema_version,
        revision=document.revision,
        workflow_revision=document.workflow_revision,
        nodes=document.nodes,
        edges=document.edges,
    )


def _issue(
    code: str, message: str, *, node_id: str | None = None, edge_id: str | None = None
) -> WorkflowValidationIssue:
    return WorkflowValidationIssue(code=code, message=message, node_id=node_id, edge_id=edge_id)


def _valid_filename_stem(value: object) -> tuple[bool, str | None]:
    if not isinstance(value, str):
        return False, "filename_stem 必须是字符串"
    if not value or value != value.strip():
        return False, "filename_stem 不能为空或包含首尾空白"
    if len(value) > 128:
        return False, "filename_stem 不能超过 128 个字符"
    if value in {".", ".."} or value.endswith("."):
        return False, "filename_stem 不能是 . 或 ..，也不能以点结尾"
    windows_basename = value.split(".", 1)[0].upper()
    if windows_basename in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        return False, "filename_stem 不能使用 Windows 保留名称"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return False, "filename_stem 不能包含控制字符"
    if any(char in '/\\<>:"|?*' for char in value):
        return False, "filename_stem 不能包含路径分隔符或危险字符"
    return True, None


def normalize_workflow_graph(
    nodes: list[WorkflowNode], edges: list[WorkflowEdge]
) -> tuple[list[WorkflowNode], list[WorkflowEdge]]:
    """Apply contract defaults without hiding invalid user configuration."""

    normalized: list[WorkflowNode] = []
    for node in nodes:
        config = dict(node.config)
        if node.type == "audio_output" and set(config).issubset({"filename_stem"}):
            config.setdefault("filename_stem", DEFAULT_FILENAME_STEM)
        normalized.append(node.model_copy(update={"config": config}, deep=True))
    return normalized, [edge.model_copy(deep=True) for edge in edges]


def validate_workflow_graph(
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
    schema_version: str = WORKFLOW_SCHEMA_VERSION,
) -> list[WorkflowValidationIssue]:
    issues: list[WorkflowValidationIssue] = []
    if schema_version != WORKFLOW_SCHEMA_VERSION:
        issues.append(
            _issue(
                "UNSUPPORTED_SCHEMA_VERSION",
                f"仅支持 schema_version {WORKFLOW_SCHEMA_VERSION}",
            )
        )
    if len(nodes) > MAX_WORKFLOW_NODES:
        issues.append(_issue("TOO_MANY_NODES", f"节点数不能超过 {MAX_WORKFLOW_NODES}"))
    if len(edges) > MAX_WORKFLOW_EDGES:
        issues.append(_issue("TOO_MANY_EDGES", f"连接数不能超过 {MAX_WORKFLOW_EDGES}"))

    node_by_id: dict[str, WorkflowNode] = {}
    input_nodes: list[WorkflowNode] = []
    output_nodes: list[WorkflowNode] = []
    for node in nodes:
        if not NODE_ID_PATTERN.fullmatch(node.id):
            issues.append(
                _issue(
                    "INVALID_NODE_ID",
                    "节点 ID 只能包含字母、数字、下划线和连字符，且必须以字母或数字开头",
                    node_id=node.id,
                )
            )
            continue
        if node.id in node_by_id:
            issues.append(_issue("DUPLICATE_NODE_ID", "节点 ID 重复", node_id=node.id))
            continue
        node_by_id[node.id] = node
        if node.type == "audio_input":
            input_nodes.append(node)
            if node.config:
                issues.append(
                    _issue(
                        "INVALID_NODE_CONFIG",
                        "audio_input 的 config 必须为空对象",
                        node_id=node.id,
                    )
                )
        elif node.type == "audio_output":
            output_nodes.append(node)
            unexpected = set(node.config) - {"filename_stem"}
            if unexpected:
                issues.append(
                    _issue(
                        "INVALID_NODE_CONFIG",
                        "audio_output 只支持 filename_stem",
                        node_id=node.id,
                    )
                )
            valid_stem, stem_error = _valid_filename_stem(
                node.config.get("filename_stem", DEFAULT_FILENAME_STEM)
            )
            if not valid_stem:
                issues.append(
                    _issue(
                        "INVALID_FILENAME_STEM",
                        stem_error or "filename_stem 无效",
                        node_id=node.id,
                    )
                )
        else:
            issues.append(
                _issue(
                    "UNKNOWN_NODE_TYPE",
                    f"不支持的节点类型: {node.type}",
                    node_id=node.id,
                )
            )

    if len(input_nodes) != 1:
        issues.append(_issue("INVALID_INPUT_COUNT", "工作流必须恰好包含一个 audio_input 节点"))
    if not output_nodes:
        issues.append(_issue("INVALID_OUTPUT_COUNT", "工作流必须至少包含一个 audio_output 节点"))

    edge_by_id: dict[str, WorkflowEdge] = {}
    incoming: dict[str, list[WorkflowEdge]] = {node_id: [] for node_id in node_by_id}
    outgoing: dict[str, list[WorkflowEdge]] = {node_id: [] for node_id in node_by_id}
    for edge in edges:
        if not EDGE_ID_PATTERN.fullmatch(edge.id):
            issues.append(
                _issue(
                    "INVALID_EDGE_ID",
                    "连接 ID 只能包含字母、数字、下划线和连字符，且必须以字母或数字开头",
                    edge_id=edge.id,
                )
            )
            continue
        if edge.id in edge_by_id:
            issues.append(_issue("DUPLICATE_EDGE_ID", "连接 ID 重复", edge_id=edge.id))
            continue
        edge_by_id[edge.id] = edge
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if source is None:
            issues.append(_issue("UNKNOWN_EDGE_ENDPOINT", "连接源节点不存在", edge_id=edge.id))
        if target is None:
            issues.append(_issue("UNKNOWN_EDGE_ENDPOINT", "连接目标节点不存在", edge_id=edge.id))
        if edge.source_port != PORT_NAME or edge.target_port != PORT_NAME:
            issues.append(
                _issue(
                    "INVALID_PORT",
                    "首版连接端口只能是 audio",
                    edge_id=edge.id,
                )
            )
        if source is not None and source.type == "audio_output":
            issues.append(
                _issue(
                    "INVALID_EDGE_DIRECTION",
                    "audio_output 不能作为连接源",
                    edge_id=edge.id,
                )
            )
        if target is not None and target.type == "audio_input":
            issues.append(
                _issue(
                    "INVALID_EDGE_DIRECTION",
                    "audio_input 不能作为连接目标",
                    edge_id=edge.id,
                )
            )
        if edge.source == edge.target:
            issues.append(_issue("CYCLE", "节点不能连接到自身", edge_id=edge.id))
        if source is not None:
            outgoing[edge.source].append(edge)
        if target is not None:
            incoming[edge.target].append(edge)

    for node in output_nodes:
        if node.id in incoming and len(incoming[node.id]) != 1:
            issues.append(
                _issue(
                    "INVALID_OUTPUT_INPUT_COUNT",
                    "audio_output 必须恰好有一条输入连接",
                    node_id=node.id,
                )
            )
    for node in input_nodes:
        if node.id in outgoing and not outgoing[node.id]:
            issues.append(
                _issue(
                    "UNCONNECTED_NODE",
                    "audio_input 至少需要连接一个输出节点",
                    node_id=node.id,
                )
            )
    for node in nodes:
        if (
            node.id in node_by_id
            and not incoming.get(node.id)
            and not outgoing.get(node.id)
            and node.type in {"audio_input", "audio_output"}
        ):
            issues.append(_issue("UNCONNECTED_NODE", "节点没有连接", node_id=node.id))

    # Kahn's algorithm reports cycles without relying on recursion depth.
    indegree = {node_id: len(incoming[node_id]) for node_id in node_by_id}
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        node_id = queue.pop()
        visited += 1
        for edge in outgoing[node_id]:
            if edge.target not in indegree:
                continue
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                queue.append(edge.target)
    if node_by_id and visited != len(node_by_id):
        already_reported = any(issue.code == "CYCLE" for issue in issues)
        if not already_reported:
            issues.append(_issue("CYCLE", "工作流不能包含环"))

    return issues


def workflow_semantic_digest(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> str:
    # Defaults are part of the executable contract, but unknown configuration
    # must remain visible so validation cannot be bypassed by normalization.
    normalized_nodes = [
        node.model_copy(
            update={
                "config": (
                    {**node.config, "filename_stem": DEFAULT_FILENAME_STEM}
                    if node.type == "audio_output"
                    and "filename_stem" not in node.config
                    and not set(node.config) - {"filename_stem"}
                    else node.config
                )
            },
            deep=True,
        )
        for node in nodes
    ]
    normalized_edges = edges
    payload = {
        "nodes": sorted(
            (
                {
                    "id": node.id,
                    "type": node.type,
                    "config": node.config,
                }
                for node in normalized_nodes
            ),
            key=lambda item: item["id"],
        ),
        "edges": sorted(
            (
                {
                    "source": edge.source,
                    "source_port": edge.source_port,
                    "target": edge.target,
                    "target_port": edge.target_port,
                }
                for edge in normalized_edges
            ),
            key=lambda item: (
                item["source"],
                item["source_port"],
                item["target"],
                item["target_port"],
            ),
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
