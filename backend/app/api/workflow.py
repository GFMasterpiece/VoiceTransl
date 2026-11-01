from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

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
from ..domain.schema import SCHEMA_VERSION, SchemaVersionError, require_schema_version
from ..domain.workflow import validate_workflow_resources
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


class WorkflowSaveRequest(BaseModel):
    schema_version: str = SCHEMA_VERSION
    expected_revision: int = Field(ge=0)
    nodes: list[WorkflowNode] = Field(max_length=64)
    edges: list[WorkflowEdge] = Field(max_length=64)

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
    workflow_revision: int = Field(ge=0)


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
            if body.expected_revision != current.revision:
                raise WorkflowRevisionConflict
            return store.save_workflow(
                pid,
                current,
                expected_revision=body.expected_revision,
                nodes=body.nodes,
                edges=body.edges,
            )
        except WorkflowRevisionConflict:
            raise HTTPException(
                409,
                detail=_error("WORKFLOW_REVISION_CONFLICT", "工作流已被修改，请刷新后重试"),
            )
        except SchemaVersionError:
            raise
        except ValueError:
            raise HTTPException(422, detail=_error("INVALID_WORKFLOW", "工作流无效"))


@router.post("/projects/{pid}/workflow/validate", response_model=WorkflowValidationResponse)
async def validate_workflow(pid: str, body: WorkflowValidateRequest, request: Request):
    _project(request, pid)
    issues = validate_workflow_graph(body.nodes, body.edges, body.schema_version)
    issues.extend(validate_workflow_resources(_project(request, pid), body.nodes))
    return WorkflowValidationResponse(valid=not issues, errors=issues)


@router.post(
    "/projects/{pid}/audios/{aid}/workflow-runs",
    status_code=202,
    response_model=WorkflowRun,
)
async def start_workflow_run(pid: str, aid: str, body: WorkflowRunRequest, request: Request):
    _audio(request, pid, aid)
    try:
        return await request.app.state.jobs.start_workflow(pid, aid, body.workflow_revision)
    except RuntimeError as error:
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
