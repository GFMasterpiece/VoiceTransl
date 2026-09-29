import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import load_preset
from ..domain import Job, JobStatus, PipelineConfig, Project, SourceAsset, default_stages
from ..storage import ProjectNotFound, SourceUploadTooLarge

router = APIRouter(prefix="/api/v1")


def deps(request):
    return request.app.state


def get_project(state, pid):
    try:
        return state.store.load_project(pid)
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}
        )


def get_job(state, pid):
    try:
        return state.store.load_job(pid)
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}
        )


class RunRequest(BaseModel):
    resume: bool = True


class HealthResponse(BaseModel):
    status: str


class ProjectListResponse(BaseModel):
    items: list[Project]


class CreateProjectResponse(BaseModel):
    project: Project
    job: Job


class RunResponse(BaseModel):
    job_id: str
    status: str


def config_editable(state, project_id: str, job: Job) -> bool:
    if job.status in {JobStatus.RUNNING, JobStatus.CANCELLING, JobStatus.COMPLETED}:
        return False
    # A queued task is persisted as pending until it acquires the semaphore.
    # Its snapshot is already fixed, so it must be treated as read-only too.
    if state.jobs.running(project_id):
        return False
    return not (job.status == JobStatus.PENDING and job.config_snapshot is not None)


class TranscriptResponse(BaseModel):
    stage: str
    transcript: object


class OutputItem(BaseModel):
    name: str
    size_bytes: int
    download_url: str


class OutputsResponse(BaseModel):
    items: list[OutputItem]


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@router.post("/projects", status_code=201, response_model=CreateProjectResponse)
async def create_project(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    source_language: str = Form("ja"),
    preset: str = Form("mock"),
):
    state = deps(request)
    suffix = Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in state.settings.allowed_extensions:
        raise HTTPException(
            415, detail={"error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": "不支持的音频格式"}}
        )
    try:
        load_preset(state.settings.preset_dir, preset)
    except (FileNotFoundError, ValueError):
        raise HTTPException(
            422, detail={"error": {"code": "INVALID_PRESET", "message": "预设不存在或配置无效"}}
        )
    pid = "prj_" + uuid4().hex
    jid = "job_" + uuid4().hex
    project = Project(
        id=pid,
        name=name or Path(file.filename or "audio").stem,
        source=SourceAsset(
            path=f"source/original.{suffix}",
            original_name=file.filename or f"original.{suffix}",
            media_type=file.content_type,
            size_bytes=0,
            language=source_language,
        ),
        preset=preset,
    )
    try:
        job = Job(id=jid, project_id=pid, stages=default_stages())
        project, job = await state.store.create_project_from_upload(
            project, job, file, state.settings.max_upload_mb * 1024 * 1024
        )
        return {"project": project, "job": job}
    except SourceUploadTooLarge:
        raise HTTPException(
            413, detail={"error": {"code": "FILE_TOO_LARGE", "message": "文件过大"}}
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            500, detail={"error": {"code": "INTERNAL_ERROR", "message": "项目创建失败"}}
        )


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(request: Request):
    return {"items": deps(request).store.list_projects()}


@router.get("/projects/{pid}", response_model=Project)
async def project(pid: str, request: Request):
    return get_project(deps(request), pid)


@router.get("/projects/{pid}/config", response_model=PipelineConfig)
async def pipeline_config(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    try:
        return state.store.load_pipeline_config(pid)
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            422,
            detail={
                "error": {
                    "code": "INVALID_PIPELINE_CONFIG",
                    "message": "项目配置无效，请重新配置",
                }
            },
        )


@router.put("/projects/{pid}/config", response_model=PipelineConfig)
async def update_pipeline_config(pid: str, value: PipelineConfig, request: Request):
    state = deps(request)
    get_project(state, pid)
    current = get_job(state, pid)
    if not config_editable(state, pid, current):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "CONFIG_READ_ONLY",
                    "message": "任务运行中或已完成，配置不可修改",
                }
            },
        )
    project = get_project(state, pid)
    if value.source_language != project.source.language:
        raise HTTPException(
            422,
            detail={
                "error": {
                    "code": "SOURCE_LANGUAGE_MISMATCH",
                    "message": "配置源语言必须与项目源语言一致",
                }
            },
        )
    state.store.save_pipeline_config(pid, value)
    return value


@router.post("/projects/{pid}/run", status_code=202, response_model=RunResponse)
async def run(pid: str, body: RunRequest, request: Request):
    state = deps(request)
    get_project(state, pid)
    try:
        job = await state.jobs.start(pid, body.resume)
    except RuntimeError as error:
        if str(error) == "JOB_ALREADY_RUNNING":
            raise HTTPException(
                409,
                detail={
                    "error": {"code": "JOB_ALREADY_RUNNING", "message": "任务正在运行或已完成"}
                },
            )
        raise HTTPException(
            500,
            detail={
                "error": {"code": "INTERNAL_ERROR", "message": "任务启动失败", "details": None}
            },
        ) from error
    except ValueError as error:
        raise HTTPException(
            422,
            detail={
                "error": {
                    "code": "INVALID_PIPELINE_CONFIG",
                    "message": "项目配置无效，无法开始处理",
                    "details": str(error),
                }
            },
        ) from error
    return {"job_id": job.id, "status": job.status}


@router.post("/projects/{pid}/cancel")
async def cancel(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    if not state.jobs.cancel(pid):
        raise HTTPException(
            409, detail={"error": {"code": "JOB_NOT_RUNNING", "message": "任务未运行"}}
        )
    return {"status": "cancelling"}


@router.get("/projects/{pid}/job", response_model=Job)
async def job(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    return get_job(state, pid)


@router.get("/projects/{pid}/transcript", response_model=TranscriptResponse)
async def transcript(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    current = get_job(state, pid)
    stage_status = current.stages
    for stage in ("translated", "aligned", "raw"):
        stage_name = {"translated": "translation", "aligned": "alignment", "raw": "asr"}[stage]
        if (
            stage_status.get(stage_name) is None
            or stage_status[stage_name].status.value != "completed"
        ):
            continue
        try:
            return {"stage": stage, "transcript": state.store.load_transcript(pid, stage)}
        except (FileNotFoundError, ValueError):
            pass
    raise HTTPException(
        404, detail={"error": {"code": "TRANSCRIPT_NOT_FOUND", "message": "字幕不存在"}}
    )


@router.get("/projects/{pid}/outputs", response_model=OutputsResponse)
async def outputs(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    current = get_job(state, pid)
    export = current.stages.get("export")
    if export is None or export.status.value != "completed":
        return {"items": []}
    return {
        "items": [
            {
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "download_url": f"/api/v1/projects/{pid}/outputs/{p.name}",
            }
            for p in state.store.list_outputs(pid)
        ]
    }


@router.get("/projects/{pid}/outputs/{filename}")
async def output(pid: str, filename: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    current = get_job(state, pid)
    export = current.stages.get("export")
    if export is None or export.status.value != "completed":
        raise HTTPException(
            404, detail={"error": {"code": "OUTPUT_NOT_FOUND", "message": "输出不存在"}}
        )
    try:
        path = state.store.resolve_output(pid, filename)
    except ValueError:
        raise HTTPException(
            404, detail={"error": {"code": "OUTPUT_NOT_FOUND", "message": "输出不存在"}}
        )
    if not path.is_file():
        raise HTTPException(
            404, detail={"error": {"code": "OUTPUT_NOT_FOUND", "message": "输出不存在"}}
        )
    return FileResponse(path, filename=path.name)


@router.websocket("/projects/{pid}/events")
async def events(ws: WebSocket, pid: str):
    state = ws.app.state
    try:
        state.store.load_project(pid)
    except Exception:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = state.events.subscribe(pid)
    receive_task = None
    event_task = None
    try:
        try:
            job = state.store.load_job(pid)
        except (ProjectNotFound, ValueError):
            await ws.close(code=4404)
            return
        await ws.send_json(
            {
                "schema_version": "1.0",
                "event_id": f"evt_{uuid4().hex}",
                "type": "job.snapshot",
                "project_id": pid,
                "job_id": job.id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": job.model_dump(mode="json"),
            }
        )
        receive_task = asyncio.create_task(ws.receive())
        event_task = asyncio.create_task(q.get())
        while True:
            done, _ = await asyncio.wait(
                (receive_task, event_task), return_when=asyncio.FIRST_COMPLETED
            )
            if receive_task in done:
                message = receive_task.result()
                if message["type"] == "websocket.disconnect":
                    break
                receive_task = asyncio.create_task(ws.receive())
            if event_task in done:
                await ws.send_json(event_task.result())
                event_task = asyncio.create_task(q.get())
    except (WebSocketDisconnect, asyncio.CancelledError, OSError, RuntimeError):
        # A send can fail when the peer has already disconnected.
        pass
    finally:
        # Removing the queue is synchronous and must happen before any
        # await: Starlette's TestClient may cancel this endpoint while it is
        # closing the websocket.
        state.events.unsubscribe(pid, q)
        child_tasks = tuple(task for task in (receive_task, event_task) if task is not None)
        for task in child_tasks:
            if task is not None and not task.done():
                task.cancel()
        if child_tasks:
            cleanup = asyncio.gather(*child_tasks, return_exceptions=True)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # A second cancellation can interrupt shield itself; wait for
                # the already-cancelled children before returning.
                await cleanup
