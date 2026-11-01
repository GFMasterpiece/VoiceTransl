import asyncio
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ..domain import (
    Job,
    Project,
    ProviderProfile,
    SourceAsset,
)
from ..domain.legacy_config import LegacyPipelineConfig, default_legacy_pipeline_config
from ..domain.project import PRIMARY_AUDIO_ID, AudioAsset
from ..domain.schema import SchemaVersionError
from ..storage import ProjectNotFound, SourceUploadTooLarge

router = APIRouter(prefix="/api/v1")


def deps(request):
    return request.app.state


def get_project(state, pid):
    try:
        return state.store.load_project(pid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}
        )


def _language_error(field: str, message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", field), "msg": message, "input": None}]
    )


def _language_form(field: str, value: str) -> str:
    if not value or value != value.strip():
        raise _language_error(field, f"{field} 不能为空或包含首尾空白")
    if len(value) > 32:
        raise _language_error(field, f"{field} 不能超过 32 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _language_error(field, f"{field} 不能包含控制字符")
    return value


def source_language_form(source_language: str = Form(...)) -> str:
    return _language_form("source_language", source_language)


def project_languages_form(
    source_language: str = Depends(source_language_form),
    target_language: str = Form(...),
) -> tuple[str, str]:
    target = _language_form("target_language", target_language)
    if source_language.casefold() == target.casefold():
        raise _language_error("target_language", "source_language 和 target_language 不能相同")
    return source_language, target


def get_job(state, pid):
    try:
        return state.store.load_job(pid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}
        )


def audio_request(request, pid, aid):
    state = deps(request)
    get_project(state, pid)
    try:
        store = state.store.scope(pid, aid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "AUDIO_NOT_FOUND", "message": "音频不存在"}}
        )
    jobs = SimpleNamespace(
        running=partial(state.jobs.running, audio_id=aid),
        running_workflow=partial(state.jobs.running_workflow, audio_id=aid),
        _lock=partial(state.jobs._lock, audio_id=aid),
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store, jobs=jobs)))


class HealthResponse(BaseModel):
    status: str


class UploadPolicyResponse(BaseModel):
    allowed_extensions: list[str]
    max_upload_bytes: int


class ProjectListResponse(BaseModel):
    items: list[Project]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ProviderProfileListResponse(BaseModel):
    items: list[ProviderProfile]


def config_editable(state, project_id: str, job: Job) -> bool:
    if job.status in {"running", "cancelling"}:
        return False
    return not state.jobs.running(project_id)


class CreateProjectResponse(BaseModel):
    project: Project
    job: Job | None


def get_provider_profile(state, profile_id: str) -> ProviderProfile:
    try:
        profile = state.store.provider_profile(profile_id)
    except SchemaVersionError:
        raise
    except (FileNotFoundError, ValueError):
        profile = None
    if profile is None:
        raise HTTPException(
            404,
            detail={
                "error": {
                    "code": "PROVIDER_PROFILE_NOT_FOUND",
                    "message": "提供商配置不存在",
                }
            },
        )
    return profile


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}


@router.get("/upload-policy", response_model=UploadPolicyResponse)
async def upload_policy(request: Request):
    settings = deps(request).settings
    return UploadPolicyResponse(
        allowed_extensions=settings.allowed_extensions,
        max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
    )


@router.get("/provider-profiles", response_model=ProviderProfileListResponse)
async def provider_profiles(request: Request):
    try:
        profiles = deps(request).store.load_provider_profiles()
    except SchemaVersionError:
        raise
    except (FileNotFoundError, ValueError):
        raise HTTPException(
            422,
            detail={
                "error": {
                    "code": "INVALID_PROVIDER_PROFILES",
                    "message": "提供商配置文件无效",
                }
            },
        )
    return {"items": profiles}


@router.post("/provider-profiles", status_code=201, response_model=ProviderProfile)
async def create_provider_profile(value: ProviderProfile, request: Request):
    state = deps(request)
    profiles = state.store.load_provider_profiles()
    if any(profile.id == value.id for profile in profiles):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "PROVIDER_PROFILE_EXISTS",
                    "message": "提供商配置 ID 已存在",
                }
            },
        )
    now = datetime.now(timezone.utc)
    profile = value.model_copy(update={"created_at": now, "updated_at": now})
    state.store.save_provider_profiles([*profiles, profile])
    return profile


@router.get("/provider-profiles/{profile_id}", response_model=ProviderProfile)
async def provider_profile(profile_id: str, request: Request):
    return get_provider_profile(deps(request), profile_id)


@router.put("/provider-profiles/{profile_id}", response_model=ProviderProfile)
async def update_provider_profile(profile_id: str, value: ProviderProfile, request: Request):
    state = deps(request)
    if value.id != profile_id:
        raise HTTPException(
            422,
            detail={
                "error": {
                    "code": "PROVIDER_PROFILE_ID_MISMATCH",
                    "message": "路径中的提供商配置 ID 必须与请求一致",
                }
            },
        )
    profiles = state.store.load_provider_profiles()
    current = next((profile for profile in profiles if profile.id == profile_id), None)
    if current is None:
        raise HTTPException(
            404,
            detail={
                "error": {
                    "code": "PROVIDER_PROFILE_NOT_FOUND",
                    "message": "提供商配置不存在",
                }
            },
        )
    profile = value.model_copy(
        update={"created_at": current.created_at, "updated_at": datetime.now(timezone.utc)}
    )
    state.store.save_provider_profiles(
        [profile if item.id == profile_id else item for item in profiles]
    )
    return profile


@router.delete("/provider-profiles/{profile_id}", status_code=204)
async def delete_provider_profile(profile_id: str, request: Request):
    state = deps(request)
    get_provider_profile(state, profile_id)
    if state.store.provider_profile_in_use(profile_id):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "PROVIDER_PROFILE_IN_USE",
                    "message": "提供商配置正在被项目使用，请先切换项目配置",
                }
            },
        )
    profiles = [
        profile for profile in state.store.load_provider_profiles() if profile.id != profile_id
    ]
    state.store.save_provider_profiles(profiles)


class EmptyProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_language: str | None = None
    target_language: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/projects/empty", status_code=201, response_model=CreateProjectResponse)
async def create_empty_project(body: EmptyProjectRequest, request: Request):
    source = (
        _language_form("source_language", body.source_language)
        if body.source_language is not None
        else None
    )
    target = (
        _language_form("target_language", body.target_language)
        if body.target_language is not None
        else None
    )
    if source is not None and target is not None and source.casefold() == target.casefold():
        raise _language_error("target_language", "source_language 和 target_language 不能相同")
    project = Project(
        id="prj_" + uuid4().hex, name=body.name, source_language=source, target_language=target
    )
    deps(request).store.create_project(project)
    return {"project": project, "job": None}


@router.post("/projects", status_code=201, response_model=CreateProjectResponse)
async def create_project(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    languages: tuple[str, str] = Depends(project_languages_form),
):
    source_language, target_language = languages
    state = deps(request)
    suffix = Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in state.settings.allowed_extensions:
        raise HTTPException(
            415, detail={"error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": "不支持的音频格式"}}
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
    )
    try:
        job = Job(id=jid, project_id=pid)
        project, job = await state.store.create_project_from_upload(
            project,
            job,
            file,
            state.settings.max_upload_mb * 1024 * 1024,
            config=default_legacy_pipeline_config(source_language, target_language=target_language),
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
async def list_projects(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    items, total = deps(request).store.list_projects_page(limit=limit, offset=offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/projects/{pid}", response_model=Project)
async def project(pid: str, request: Request):
    return get_project(deps(request), pid)


@router.delete("/projects/{pid}", status_code=204)
async def delete_project(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    try:
        await state.jobs.delete_project(pid)
    except RuntimeError as error:
        if str(error) == "PROJECT_BUSY":
            raise HTTPException(
                409,
                detail={
                    "error": {"code": "PROJECT_BUSY", "message": "任务正在运行或排队，请先取消任务"}
                },
            )
        raise
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"}}
        )


@router.get("/projects/{pid}/config", response_model=LegacyPipelineConfig)
async def pipeline_config(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    try:
        return state.store.load_pipeline_config(pid)
    except SchemaVersionError:
        raise
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


def _legacy_config_read_only() -> HTTPException:
    return HTTPException(
        409,
        detail={
            "error": {
                "code": "LEGACY_CONFIG_READ_ONLY",
                "message": "旧 Pipeline 配置已退役，仅支持读取；请使用工作流配置",
            }
        },
    )


@router.put("/projects/{pid}/config", response_model=LegacyPipelineConfig)
async def update_pipeline_config(pid: str, request: Request):
    get_project(deps(request), pid)
    raise _legacy_config_read_only()


@router.get("/projects/{pid}/job", response_model=Job)
async def job(pid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    return get_job(state, pid)


@router.websocket("/projects/{pid}/events")
async def events(ws: WebSocket, pid: str, audio_id: str | None = None):
    state = ws.app.state
    try:
        state.store.load_project(pid)
        store = state.store.scope(pid, audio_id or PRIMARY_AUDIO_ID)
    except SchemaVersionError:
        await ws.close(code=4402)
        return
    except Exception:
        await ws.close(code=4404)
        return
    await ws.accept()
    q = state.events.subscribe(pid)
    receive_task = None
    event_task = None
    try:
        try:
            job = store.load_job(pid)
        except SchemaVersionError:
            await ws.close(code=4402)
            return
        except (ProjectNotFound, ValueError):
            await ws.close(code=4404)
            return
        await ws.send_json(
            {
                "schema_version": "1.0",
                "event_id": f"evt_{uuid4().hex}",
                "type": "job.snapshot",
                "project_id": pid,
                "audio_id": job.audio_id,
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
                event = event_task.result()
                if audio_id is None or event.get("audio_id", PRIMARY_AUDIO_ID) == audio_id:
                    await ws.send_json(event)
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


@router.get("/projects/{pid}/audios")
async def audios(pid: str, request: Request):
    state = deps(request)
    project = get_project(state, pid)
    return {
        "items": [
            {**audio.model_dump(mode="json"), "job": state.jobs.job(pid, audio.id)}
            for audio in project.audios
        ]
    }


@router.post("/projects/{pid}/audios", status_code=201)
async def append_audio(
    pid: str,
    request: Request,
    file: UploadFile = File(...),
    source_language: str = Depends(source_language_form),
    name: str | None = Form(None),
):
    state = deps(request)
    get_project(state, pid)
    suffix = Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in state.settings.allowed_extensions:
        raise HTTPException(
            415, detail={"error": {"code": "UNSUPPORTED_MEDIA_TYPE", "message": "不支持的音频格式"}}
        )
    aid = "aud_" + uuid4().hex
    audio = AudioAsset(
        id=aid,
        name=name,
        source=SourceAsset(
            path=f"audios/{aid}/source/original.{suffix}",
            original_name=file.filename or f"original.{suffix}",
            media_type=file.content_type,
            size_bytes=0,
            language=source_language,
        ),
    )
    job = Job(id="job_" + uuid4().hex, project_id=pid, audio_id=aid)
    try:
        audio, job = await state.store.append_audio(
            pid, audio, job, file, state.settings.max_upload_mb * 1024 * 1024
        )
    except SourceUploadTooLarge:
        raise HTTPException(
            413, detail={"error": {"code": "FILE_TOO_LARGE", "message": "文件过大"}}
        )
    except ProjectNotFound:
        get_project(state, pid)
        raise
    return {"audio": audio, "job": job}


@router.get("/projects/{pid}/audios/{aid}/config", response_model=LegacyPipelineConfig)
async def audio_config(pid: str, aid: str, request: Request):
    return await pipeline_config(pid, audio_request(request, pid, aid))


@router.put("/projects/{pid}/audios/{aid}/config", response_model=LegacyPipelineConfig)
async def update_audio_config(pid: str, aid: str, request: Request):
    audio_request(request, pid, aid)
    raise _legacy_config_read_only()


@router.get("/projects/{pid}/audios/{aid}/job", response_model=Job)
async def audio_job(pid: str, aid: str, request: Request):
    return await job(pid, audio_request(request, pid, aid))


@router.websocket("/projects/{pid}/audios/{aid}/events")
async def audio_events(ws: WebSocket, pid: str, aid: str):
    await events(ws, pid, audio_id=aid)


@router.get("/projects/{pid}/audios/{aid}/source")
async def audio_source(pid: str, aid: str, request: Request):
    state = deps(request)
    get_project(state, pid)
    try:
        audio = state.store.audio(pid, aid)
        path = state.store.scope(pid, aid).source_path(pid, audio.source.path)
    except (OSError, ValueError):
        raise HTTPException(
            404, detail={"error": {"code": "AUDIO_NOT_FOUND", "message": "音频文件不存在"}}
        )
    from ..domain.media import inline_media_type

    return FileResponse(
        path,
        media_type=inline_media_type(path),
        headers={"X-Content-Type-Options": "nosniff"},
        filename=path.name,
        content_disposition_type="inline",
    )
