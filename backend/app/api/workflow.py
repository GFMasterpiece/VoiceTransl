from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr, field_validator

from ..application.workflow_connections import (
    ConnectionSnapshotError,
)
from ..application.workflow_credentials import prepare_workflow_credentials
from ..application.workflow_reuse import ReuseError, ReuseIssue, ReusePlan, prepare_reuse
from ..domain import (
    WorkflowDocument,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    WorkflowRunList,
    WorkflowRunSummary,
    WorkflowValidationResponse,
    validate_workflow_graph,
)
from ..domain.schema import CONFIGURATION_SCHEMA_VERSION as SCHEMA_VERSION
from ..domain.schema import SchemaVersionError, require_schema_version
from ..domain.workflow import (
    WorkflowRerunRequest,
    normalize_workflow_graph,
    validate_workflow_audio_selection,
    validate_workflow_resources,
    workflow_snapshot,
)
from ..storage import ProjectNotFound
from ..storage.project_store import WorkflowRevisionConflict

router = APIRouter(prefix="/api/v1")


def _error(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def _project(request: Request, pid: str):
    try:
        return request.app.state.store.load_project(pid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(404, detail=_error("PROJECT_NOT_FOUND", "项目不存在"))


def _audio(request: Request, pid: str, aid: str):
    project = _project(request, pid)
    if all(audio.id != aid for audio in project.audios):
        raise HTTPException(404, detail=_error("AUDIO_NOT_FOUND", "音频不存在"))
    return project


def _run(request: Request, pid: str, aid: str, rid: str) -> WorkflowRun:
    _audio(request, pid, aid)
    try:
        return request.app.state.store.load_workflow_run(pid, aid, rid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(404, detail=_error("WORKFLOW_RUN_NOT_FOUND", "工作流运行不存在"))


class SetCredential(BaseModel):
    model_config = {"extra": "forbid"}
    node_id: str
    operation: Literal["set"]
    api_key: SecretStr = Field(min_length=1, max_length=8192, exclude=True)


class ClearCredential(BaseModel):
    model_config = {"extra": "forbid"}
    node_id: str
    operation: Literal["clear"]


class UseDefaultCredential(BaseModel):
    model_config = {"extra": "forbid"}
    node_id: str
    operation: Literal["use_default"]
    default_credential_version: str = Field(min_length=1, max_length=256, exclude=True)


class WorkflowSaveRequest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    expected_revision: int = Field(ge=0)
    nodes: list[WorkflowNode] = Field(max_length=64)
    edges: list[WorkflowEdge] = Field(max_length=64)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    credential_updates: list[
        Annotated[
            SetCredential | ClearCredential | UseDefaultCredential, Field(discriminator="operation")
        ]
    ] = Field(default_factory=list, max_length=64, exclude=True)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return require_schema_version(value, "workflow request")


class WorkflowValidateRequest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    nodes: list[WorkflowNode] = Field(max_length=64)
    edges: list[WorkflowEdge] = Field(max_length=64)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        return require_schema_version(value, "workflow validation request")


class WorkflowRunRequest(BaseModel):
    model_config = {"extra": "forbid"}
    workflow_revision: int = Field(ge=0)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    rerun: WorkflowRerunRequest | None = None


@router.get("/projects/{pid}/workflow", response_model=WorkflowDocument)
async def get_workflow(pid: str, request: Request):
    _project(request, pid)
    try:
        return request.app.state.store.load_workflow(pid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(422, detail=_error("INVALID_WORKFLOW", "已保存的工作流无法读取"))


@router.put("/projects/{pid}/workflow", response_model=WorkflowDocument)
async def put_workflow(pid: str, body: WorkflowSaveRequest, request: Request):
    _project(request, pid)
    store = request.app.state.store
    issues = validate_workflow_graph(body.nodes, body.edges)
    issues.extend(validate_workflow_resources(_project(request, pid), body.nodes))
    if issues:
        raise HTTPException(
            422,
            detail=_error(
                "INVALID_WORKFLOW",
                "工作流无效",
                [issue.model_dump(mode="json") for issue in issues],
            ),
        )
    async with store.mutation_lock(pid):
        try:
            current = store.load_workflow(pid)
            replay = bool(
                body.credential_updates
                and body.request_id
                and current.revision == body.expected_revision + 1
            )
            if body.expected_revision != current.revision and not replay:
                raise WorkflowRevisionConflict
            prepared = prepare_workflow_credentials(
                store,
                pid,
                body.nodes,
                body.credential_updates,
                body.request_id,
                body.expected_revision,
                existing_node_ids={node.id for node in current.nodes},
                replay=replay,
            )
            prepared, edges = normalize_workflow_graph(prepared, body.edges)
            if replay:
                if prepared == current.nodes and edges == current.edges:
                    return current
                raise WorkflowRevisionConflict
            try:
                return store.save_workflow(
                    pid,
                    current,
                    expected_revision=body.expected_revision,
                    nodes=prepared,
                    edges=edges,
                )
            except (OSError, ValueError):
                # The atomic graph replace is the commit point. Metadata or
                # history repair afterwards must not make a committed key
                # update look as though it rolled back. No credential values
                # participate in this comparison or error output.
                published = WorkflowDocument.model_validate(
                    store._load_json(store._workflow_path(pid), "workflow")
                )
                if (
                    published.revision == body.expected_revision + 1
                    and published.nodes == prepared
                    and published.edges == edges
                ):
                    return published
                raise
        except ConnectionSnapshotError as error:
            status = (
                409
                if error.code == "CREDENTIAL_REQUEST_CONFLICT"
                else 503
                if error.code == "CREDENTIAL_STORAGE_ERROR"
                else 422
            )
            raise HTTPException(status, detail=_error(error.code, str(error))) from None
        except WorkflowRevisionConflict:
            raise HTTPException(
                409,
                detail=_error("WORKFLOW_REVISION_CONFLICT", "工作流已被修改，请刷新后重试"),
            )
        except SchemaVersionError:
            raise
        except ValueError:
            raise HTTPException(422, detail=_error("INVALID_WORKFLOW", "工作流无效"))
        except OSError:
            raise HTTPException(
                503, detail=_error("WORKFLOW_SAVE_FAILED", "工作流保存失败，请使用相同请求重试")
            ) from None


@router.post("/projects/{pid}/workflow/validate", response_model=WorkflowValidationResponse)
async def validate_workflow(pid: str, body: WorkflowValidateRequest, request: Request):
    _project(request, pid)
    issues = validate_workflow_graph(body.nodes, body.edges, body.schema_version)
    issues.extend(validate_workflow_resources(_project(request, pid), body.nodes))
    return WorkflowValidationResponse(valid=not issues, errors=issues)


@router.post("/projects/{pid}/audios/{aid}/workflow-runs/plan", response_model=ReusePlan)
async def plan_workflow_run(pid: str, aid: str, body: WorkflowRunRequest, request: Request):
    project = _audio(request, pid, aid)
    store = request.app.state.store
    if body.rerun is None:
        raise HTTPException(422, detail=_error("RERUN_REQUIRED", "请选择重跑节点和来源运行"))
    async with store.mutation_lock(pid):
        document = store.load_workflow(pid)
        if document.workflow_revision != body.workflow_revision:
            raise HTTPException(
                409, detail=_error("WORKFLOW_REVISION_CONFLICT", "工作流版本已变化，请刷新后重试")
            )
        issues = validate_workflow_graph(document.nodes, document.edges)
        issues.extend(validate_workflow_resources(project, document.nodes))
        if issues:
            return ReusePlan(
                valid=False, issues=[ReuseIssue(code="INVALID_WORKFLOW", message="当前保存图无效")]
            )
        try:
            validate_workflow_audio_selection(
                document.nodes, audio_id=aid, allow_collection_subset=True
            )
            prepared = await prepare_reuse(store, pid, aid, workflow_snapshot(document), body.rerun)
            from ..application.workflow_connections import freeze_workflow_connections

            freeze_workflow_connections(
                store,
                workflow_snapshot(document),
                pid,
                node_ids=set(prepared.plan.execute_node_ids),
            )
            from ..application.configuration_evaluator import validate_configuration_snapshot

            await validate_configuration_snapshot(
                store,
                workflow_snapshot(document),
                pid,
                aid,
                node_ids=set(prepared.plan.execute_node_ids),
            )
            return prepared.plan
        except ReuseError as error:
            return error.plan
        except ConnectionSnapshotError as error:
            return ReusePlan(valid=False, issues=[ReuseIssue(code=error.code, message=str(error))])
        except ValueError:
            return ReusePlan(
                valid=False,
                issues=[
                    ReuseIssue(code="INPUT_AUDIO_MISMATCH", message="当前音频不在输入节点清单中")
                ],
            )


@router.post(
    "/projects/{pid}/audios/{aid}/workflow-runs",
    status_code=202,
    response_model=WorkflowRun,
)
async def start_workflow_run(pid: str, aid: str, body: WorkflowRunRequest, request: Request):
    _audio(request, pid, aid)
    if body.rerun and not body.request_id:
        raise HTTPException(422, detail=_error("WORKFLOW_REQUEST_ID_REQUIRED", "重跑需要请求标识"))
    try:
        return await request.app.state.jobs.start_workflow(
            pid, aid, body.workflow_revision, rerun=body.rerun, request_id=body.request_id
        )
    except ReuseError as error:
        issue = error.plan.issues[0]
        raise HTTPException(
            422, detail=_error(issue.code, issue.message, error.plan.model_dump())
        ) from None
    except ConnectionSnapshotError as error:
        raise HTTPException(422, detail=_error(error.code, str(error))) from error
    except RuntimeError as error:
        if str(error) == "WORKFLOW_STOPPING":
            raise HTTPException(
                503, detail=_error("WORKFLOW_STOPPING", "服务正在关闭，请稍后重试")
            ) from None
        if str(error) == "WORKFLOW_REQUEST_CONFLICT":
            raise HTTPException(
                409, detail=_error("WORKFLOW_REQUEST_CONFLICT", "请求标识已用于不同运行")
            ) from None
        if str(error) == "WORKFLOW_REVISION_CONFLICT":
            raise HTTPException(
                409,
                detail=_error("WORKFLOW_REVISION_CONFLICT", "工作流版本已变化，请刷新后重试"),
            )
        if str(error) == "AUDIO_BUSY":
            raise HTTPException(
                409,
                detail=_error("AUDIO_BUSY", "该音频已有任务正在运行或排队"),
            )
        raise HTTPException(500, detail=_error("INTERNAL_ERROR", "工作流启动失败")) from error
    except ValueError as error:
        if str(error) in {"INPUT_AUDIO_COLLECTION", "INPUT_AUDIO_COLLECTION_EMPTY"}:
            raise HTTPException(
                422,
                detail=_error(
                    str(error),
                    "输入节点包含多个音频，请启动完整批次"
                    if str(error) == "INPUT_AUDIO_COLLECTION"
                    else "输入节点尚未选择音频，请先添加音频",
                ),
            ) from error
        if str(error) == "INPUT_AUDIO_MISMATCH":
            raise HTTPException(
                422, detail=_error("INPUT_AUDIO_MISMATCH", "输入节点绑定的音频与运行音频不一致")
            ) from error
        raise HTTPException(
            422, detail=_error("INVALID_WORKFLOW", "工作流无效，无法运行")
        ) from error


@router.get("/projects/{pid}/audios/{aid}/workflow-runs", response_model=WorkflowRunList)
async def list_workflow_runs(
    pid: str,
    aid: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    _audio(request, pid, aid)
    runs, total = request.app.state.store.list_workflow_runs(pid, aid, limit=limit, offset=offset)
    items = [
        WorkflowRunSummary(
            id=run.id,
            status=run.status,
            workflow_revision=run.workflow_revision,
            created_at=run.created_at,
            finished_at=run.finished_at,
            error=run.error,
        )
        for run in runs
    ]
    return WorkflowRunList(items=items, total=total, limit=limit, offset=offset)


@router.get("/projects/{pid}/audios/{aid}/workflow-runs/{rid}", response_model=WorkflowRun)
async def get_workflow_run(pid: str, aid: str, rid: str, request: Request):
    return _run(request, pid, aid, rid)


@router.post(
    "/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel",
    status_code=202,
    response_model=WorkflowRun,
)
async def cancel_workflow_run(pid: str, aid: str, rid: str, request: Request):
    _run(request, pid, aid, rid)
    try:
        changed = request.app.state.jobs.cancel_workflow(pid, aid, rid)
    except RuntimeError:
        # A persisted run may still look active after a restart. It is
        # interrupted during startup and cannot be cancelled again.
        changed = False
    if changed:
        return _run(request, pid, aid, rid)
    return JSONResponse(
        status_code=200, content=_run(request, pid, aid, rid).model_dump(mode="json")
    )


def _artifact_run(request: Request, pid: str, aid: str, rid: str, node_id: str):
    run = _run(request, pid, aid, rid)
    record = next((node for node in run.nodes if node.node_id == node_id), None)
    if record is None or record.status.value != "completed" or record.artifact is None:
        raise HTTPException(404, detail=_error("WORKFLOW_OUTPUT_NOT_FOUND", "节点产物不存在"))
    store = request.app.state.store
    try:
        path = store.workflow_artifact_path(pid, aid, rid, record.node_id, record.artifact.filename)
        valid_file = path.is_file() and not path.is_symlink()
    except (OSError, ValueError):
        valid_file = False
    if not valid_file:
        raise HTTPException(404, detail=_error("WORKFLOW_OUTPUT_NOT_FOUND", "节点产物文件不存在"))
    return run, record, path


@router.get("/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}")
async def workflow_output(pid: str, aid: str, rid: str, node_id: str, request: Request):
    _, record, _ = _artifact_run(request, pid, aid, rid, node_id)
    return record.artifact


@router.get("/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/download")
async def download_workflow_output(pid: str, aid: str, rid: str, node_id: str, request: Request):
    _, record, path = _artifact_run(request, pid, aid, rid, node_id)
    return FileResponse(
        path,
        media_type=record.artifact.media_type,
        filename=record.artifact.filename,
    )


@router.get("/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/content")
async def inline_workflow_output(pid: str, aid: str, rid: str, node_id: str, request: Request):
    from ..domain.media import inline_media_type

    _, record, path = _artifact_run(request, pid, aid, rid, node_id)
    return FileResponse(
        path,
        media_type=inline_media_type(path),
        headers={"X-Content-Type-Options": "nosniff"},
        filename=record.artifact.filename,
        content_disposition_type="inline",
    )
