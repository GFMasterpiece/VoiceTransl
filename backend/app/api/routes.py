import asyncio
import json
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Literal
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

from ..config import load_preset
from ..domain import (
    ASRConfig,
    Job,
    JobStatus,
    PipelineConfig,
    PipelineStageName,
    Project,
    ProviderProfile,
    SourceAsset,
    StageState,
    StageStatus,
    Transcript,
    TranslationConfig,
    default_stages,
)
from ..domain.project import PRIMARY_AUDIO_ID, AudioAsset
from ..domain.schema import SchemaVersionError, require_schema_version
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


class RunRequest(BaseModel):
    resume: bool = True


class AudioRunRequest(RunRequest):
    audio_ids: list[str] = Field(min_length=1)

    @field_validator("audio_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("audio_ids must be unique")
        return value


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
        start=partial(state.jobs.start, audio_id=aid),
        cancel=partial(state.jobs.cancel, audio_id=aid),
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


class CreateProjectResponse(BaseModel):
    project: Project
    job: Job


class RunResponse(BaseModel):
    job_id: str
    status: str


def config_editable(state, project_id: str, job: Job) -> bool:
    if job.status in {JobStatus.RUNNING, JobStatus.CANCELLING}:
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
    format: str | None = None
    kind: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    bilingual: bool | None = None
    sha256: str | None = None


class OutputsResponse(BaseModel):
    items: list[OutputItem]


class PreprocessResult(BaseModel):
    kind: Literal["preprocess"] = "preprocess"
    data: dict[str, object]


class TranscriptResult(BaseModel):
    kind: Literal["transcript"] = "transcript"
    transcript: Transcript


class OutputsResult(OutputsResponse):
    kind: Literal["outputs"] = "outputs"


class StageResultError(BaseModel):
    code: Literal["STAGE_RESULT_NOT_FOUND"] = "STAGE_RESULT_NOT_FOUND"
    message: str = "阶段结果缺失或无法读取，请重新处理该音频"


class StageDetailResponse(BaseModel):
    stage: PipelineStageName
    job_id: str
    audio_id: str
    state: StageState
    config: dict[str, object] | None
    result: (
        Annotated[PreprocessResult | TranscriptResult | OutputsResult, Field(discriminator="kind")]
        | None
    ) = None
    result_error: StageResultError | None = None


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


@router.post("/projects", status_code=201, response_model=CreateProjectResponse)
async def create_project(
    request: Request,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    languages: tuple[str, str] = Depends(project_languages_form),
    preset: str = Form("mock"),
):
    source_language, target_language = languages
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
            project,
            job,
            file,
            state.settings.max_upload_mb * 1024 * 1024,
            config=PipelineConfig(
                source_language=source_language,
                target_language=target_language,
                asr=ASRConfig(language=source_language),
                translation=TranslationConfig(target_language=target_language),
            ),
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


@router.get("/projects/{pid}/config", response_model=PipelineConfig)
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


@router.put("/projects/{pid}/config", response_model=PipelineConfig)
async def update_pipeline_config(pid: str, value: PipelineConfig, request: Request):
    async with deps(request).jobs._lock(pid):
        return _update_pipeline_config(pid, value, request)


def _update_pipeline_config(pid, value, request):
    state = deps(request)
    get_project(state, pid)
    current = get_job(state, pid)
    if not config_editable(state, pid, current):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "CONFIG_READ_ONLY",
                    "message": "任务运行中或排队中，配置不可修改",
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
    profile_id = value.asr.provider_profile_id
    if profile_id:
        profile = state.store.provider_profile(profile_id)
        if profile is None:
            raise HTTPException(
                422,
                detail={
                    "error": {
                        "code": "PROVIDER_PROFILE_NOT_FOUND",
                        "message": "所选提供商配置不存在",
                    }
                },
            )
        if not profile.enabled:
            raise HTTPException(
                422,
                detail={
                    "error": {
                        "code": "PROVIDER_PROFILE_DISABLED",
                        "message": "所选提供商配置已停用",
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
                    "error": {"code": "JOB_ALREADY_RUNNING", "message": "任务正在运行或排队中"}
                },
            )
        if str(error) == "AUDIO_BUSY":
            raise HTTPException(
                409,
                detail={
                    "error": {
                        "code": "AUDIO_BUSY",
                        "message": "该音频已有节点工作流正在运行或排队",
                    }
                },
            )
        raise HTTPException(
            500,
            detail={
                "error": {"code": "INTERNAL_ERROR", "message": "任务启动失败", "details": None}
            },
        ) from error
    except SchemaVersionError:
        raise
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
    if state.jobs.running_workflow(pid):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "AUDIO_BUSY_WORKFLOW",
                    "message": "节点工作流正在使用该音频，请从工作流运行中取消",
                }
            },
        )
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
        except SchemaVersionError:
            raise
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
    manifest_entries = (
        state.store.load_export_manifest(pid) if state.store.has_export_manifest(pid) else []
    )
    manifest = {str(item["name"]): item for item in manifest_entries}
    items = []
    for path in state.store.list_outputs(pid):
        if path.name == "manifest.json":
            continue
        metadata = manifest.get(path.name, {})
        language = metadata.get("language")
        items.append(
            {
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "download_url": f"/api/v1/projects/{pid}/outputs/{path.name}",
                "format": metadata.get("format", path.suffix.lstrip(".")),
                "kind": metadata.get("kind"),
                "source_language": (
                    current.config_snapshot.source_language
                    if current.config_snapshot is not None
                    else None
                ),
                "target_language": (
                    current.config_snapshot.target_language
                    if current.config_snapshot is not None
                    else language
                ),
                "bilingual": metadata.get("bilingual"),
                "sha256": metadata.get("sha256"),
            }
        )
    return {"items": items}


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
        manifest_entries = (
            state.store.load_export_manifest(pid) if state.store.has_export_manifest(pid) else None
        )
        if manifest_entries is not None and filename not in {
            str(item["name"]) for item in manifest_entries
        }:
            raise ValueError("output is not managed by the current manifest")
        path = state.store._direct_output_path(pid, filename)
    except SchemaVersionError:
        raise
    except (ValueError, OSError):
        raise HTTPException(
            404, detail={"error": {"code": "OUTPUT_NOT_FOUND", "message": "输出不存在"}}
        )
    if not path.is_file() or path.is_symlink():
        raise HTTPException(
            404, detail={"error": {"code": "OUTPUT_NOT_FOUND", "message": "输出不存在"}}
        )
    return FileResponse(path, filename=path.name)


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


@router.post("/projects/{pid}/audios/run", status_code=202)
async def run_audios(pid: str, body: AudioRunRequest, request: Request):
    state = deps(request)
    project = get_project(state, pid)
    if not set(body.audio_ids).issubset({audio.id for audio in project.audios}):
        raise HTTPException(
            404, detail={"error": {"code": "AUDIO_NOT_FOUND", "message": "选择的音频不存在"}}
        )
    items = []
    for aid in body.audio_ids:
        try:
            result = await run_audio(pid, aid, RunRequest(resume=body.resume), request)
            items.append({"audio_id": aid, **result})
        except HTTPException as error:
            items.append({"audio_id": aid, "error": error.detail["error"]})
    return {"items": items}


@router.get("/projects/{pid}/audios/{aid}/config", response_model=PipelineConfig)
async def audio_config(pid: str, aid: str, request: Request):
    return await pipeline_config(pid, audio_request(request, pid, aid))


@router.put("/projects/{pid}/audios/{aid}/config", response_model=PipelineConfig)
async def update_audio_config(pid: str, aid: str, value: PipelineConfig, request: Request):
    return await update_pipeline_config(pid, value, audio_request(request, pid, aid))


@router.post("/projects/{pid}/audios/{aid}/run", status_code=202, response_model=RunResponse)
async def run_audio(pid: str, aid: str, body: RunRequest, request: Request):
    return await run(pid, body, audio_request(request, pid, aid))


@router.post("/projects/{pid}/audios/{aid}/cancel")
async def cancel_audio(pid: str, aid: str, request: Request):
    return await cancel(pid, audio_request(request, pid, aid))


@router.get("/projects/{pid}/audios/{aid}/job", response_model=Job)
async def audio_job(pid: str, aid: str, request: Request):
    return await job(pid, audio_request(request, pid, aid))


@router.get("/projects/{pid}/audios/{aid}/transcript", response_model=TranscriptResponse)
async def audio_transcript(pid: str, aid: str, request: Request):
    return await transcript(pid, audio_request(request, pid, aid))


@router.get("/projects/{pid}/audios/{aid}/stages/{stage}", response_model=StageDetailResponse)
async def audio_stage_detail(pid: str, aid: str, stage: PipelineStageName, request: Request):
    scoped_request = audio_request(request, pid, aid)
    store = deps(scoped_request).store
    current = get_job(deps(scoped_request), pid)
    state = current.stages.get(stage, StageState())
    detail = StageDetailResponse(
        stage=stage,
        job_id=current.id,
        audio_id=current.audio_id,
        state=state,
        config=(
            getattr(current.config_snapshot, stage.value).model_dump(mode="json")
            if current.config_snapshot is not None
            else None
        ),
    )
    # A fresh run keeps old files until each stage replaces them. Only its
    # completed stages may expose results, including when later stages fail.
    if state.status != StageStatus.COMPLETED:
        return detail
    try:
        if not state.artifact:
            raise ValueError("missing stage artifact")
        artifact = store.resolve_artifact_path(pid, state.artifact)
        transcript_name = {
            PipelineStageName.ASR: "raw",
            PipelineStageName.ALIGNMENT: "aligned",
            PipelineStageName.TRANSLATION: "translated",
        }.get(stage)
        if transcript_name:
            expected = store.resolve_project_path(pid, f"transcript/{transcript_name}.json")
            if artifact != expected:
                raise ValueError("wrong transcript artifact")
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("invalid transcript")
            # A malformed artifact is reported through result_error. Only an
            # explicit, present version mismatch is an API schema rejection.
            if "schema_version" in payload:
                require_schema_version(payload["schema_version"], "transcript")
            value = Transcript.model_validate_json(artifact.read_text(encoding="utf-8"))
            if value.project_id != pid or value.stage != transcript_name:
                raise ValueError("wrong transcript identity")
            detail.result = TranscriptResult(transcript=value)
        elif stage == PipelineStageName.PREPROCESS:
            if artifact != store.resolve_project_path(pid, "cache/preprocess.json"):
                raise ValueError("wrong preprocessing artifact")
            metadata = json.loads(artifact.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("invalid preprocessing metadata")
            detail.result = PreprocessResult(data=metadata)
        else:
            if artifact != store.resolve_project_path(pid, "output") or not artifact.is_dir():
                raise ValueError("missing output directory")
            result = await audio_outputs(pid, aid, request)
            if not result["items"]:
                raise ValueError("missing outputs")
            detail.result = OutputsResult(items=result["items"])
    except SchemaVersionError:
        raise
    except (OSError, ValueError):
        detail.result_error = StageResultError()
    return detail


@router.get("/projects/{pid}/audios/{aid}/outputs", response_model=OutputsResponse)
async def audio_outputs(pid: str, aid: str, request: Request):
    result = await outputs(pid, audio_request(request, pid, aid))
    for item in result["items"]:
        item["download_url"] = f"/api/v1/projects/{pid}/audios/{aid}/outputs/{item['name']}"
    return result


@router.get("/projects/{pid}/audios/{aid}/outputs/{filename}")
async def audio_output(pid: str, aid: str, filename: str, request: Request):
    return await output(pid, filename, audio_request(request, pid, aid))


@router.websocket("/projects/{pid}/audios/{aid}/events")
async def audio_events(ws: WebSocket, pid: str, aid: str):
    await events(ws, pid, audio_id=aid)
