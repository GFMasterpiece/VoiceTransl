import asyncio
from datetime import datetime, timezone
import os
import shutil
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ..domain import Job, Project, SourceAsset, default_stages
from ..storage import ProjectNotFound

router=APIRouter(prefix="/api/v1")
def deps(request): return request.app.state
def get_project(state,pid):
    try: return state.store.load_project(pid)
    except (ProjectNotFound,ValueError): raise HTTPException(404,detail={"error":{"code":"PROJECT_NOT_FOUND","message":"项目不存在"}})
class RunRequest(BaseModel): resume: bool=True

@router.get("/health")
async def health(): return {"status":"ok"}
@router.post("/projects",status_code=201)
async def create_project(request:Request,file:UploadFile=File(...),name:str|None=Form(None),source_language:str=Form("ja"),preset:str=Form("mock")):
    state=deps(request); suffix=Path(file.filename or "").suffix.lower().lstrip(".")
    if suffix not in state.settings.allowed_extensions: raise HTTPException(415,detail={"error":{"code":"UNSUPPORTED_MEDIA_TYPE","message":"不支持的音频格式"}})
    pid="prj_"+uuid4().hex; jid="job_"+uuid4().hex
    temp_id=f".{pid}.tmp_{uuid4().hex}"
    temp_dir = state.store.project_dir(temp_id)
    final_dir = state.store.project_dir(pid)
    try:
        # Build the complete project next to its final directory.  The final
        # directory becomes visible only after project.json and job.json have
        # both been atomically written and the source upload is complete.
        temp_dir.mkdir(parents=True)
        (temp_dir/"source").mkdir()
        for n in ("cache","transcript","output","logs"): (temp_dir/n).mkdir()
        target=temp_dir/"source"/f"original.{suffix}"; total=0
        with target.open("wb") as out:
            while chunk:=await file.read(1024*1024):
                total+=len(chunk)
                if total>state.settings.max_upload_mb*1024*1024:
                    raise HTTPException(413,detail={"error":{"code":"FILE_TOO_LARGE","message":"文件过大"}})
                out.write(chunk)
                out.flush()
        project=Project(id=pid,name=name or Path(file.filename or "audio").stem,source=SourceAsset(path=target.relative_to(temp_dir).as_posix(),original_name=file.filename or target.name,media_type=file.content_type,size_bytes=total,language=source_language),preset=preset)
        from ..storage.atomic import atomic_write_json
        atomic_write_json(temp_dir/"project.json", project.model_dump(mode="json"))
        job=Job(id=jid,project_id=pid,stages=default_stages())
        atomic_write_json(temp_dir/"job.json", job.model_dump(mode="json"))
        os.replace(temp_dir, final_dir)
        return {"project":project,"job":job}
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(final_dir, ignore_errors=True)
        raise HTTPException(500,detail={"error":{"code":"INTERNAL_ERROR","message":"项目创建失败"}})
@router.get("/projects")
async def list_projects(request:Request): return {"items":deps(request).store.list_projects()}
@router.get("/projects/{pid}")
async def project(pid:str,request:Request): return get_project(deps(request),pid)
@router.post("/projects/{pid}/run",status_code=202)
async def run(pid:str,body:RunRequest,request:Request):
    state=deps(request); get_project(state,pid)
    try: job=await state.jobs.start(pid,body.resume)
    except RuntimeError as error:
        if str(error) == "JOB_ALREADY_RUNNING":
            raise HTTPException(409,detail={"error":{"code":"JOB_ALREADY_RUNNING","message":"任务正在运行或已完成"}})
        raise
    return {"job_id":job.id,"status":job.status}
@router.post("/projects/{pid}/cancel")
async def cancel(pid:str,request:Request):
    state=deps(request); get_project(state,pid)
    if not state.jobs.cancel(pid): raise HTTPException(409,detail={"error":{"code":"JOB_NOT_RUNNING","message":"任务未运行"}})
    return {"status":"cancelling"}
@router.get("/projects/{pid}/job")
async def job(pid:str,request:Request): get_project(deps(request),pid); return deps(request).store.load_job(pid)
@router.get("/projects/{pid}/transcript")
async def transcript(pid:str,request:Request):
    state=deps(request); get_project(state,pid)
    for stage in ("translated","aligned","raw"):
        try: return {"stage":stage,"transcript":state.store.load_transcript(pid,stage)}
        except (FileNotFoundError, ValueError): pass
    raise HTTPException(404,detail={"error":{"code":"TRANSCRIPT_NOT_FOUND","message":"字幕不存在"}})
@router.get("/projects/{pid}/outputs")
async def outputs(pid:str,request:Request):
    state=deps(request); get_project(state,pid); return {"items":[{"name":p.name,"size_bytes":p.stat().st_size,"download_url":f"/api/v1/projects/{pid}/outputs/{p.name}"} for p in state.store.list_outputs(pid)]}
@router.get("/projects/{pid}/outputs/{filename}")
async def output(pid:str,filename:str,request:Request):
    state=deps(request); get_project(state,pid)
    try: path=state.store.resolve_output(pid,filename)
    except ValueError: raise HTTPException(404,detail={"error":{"code":"OUTPUT_NOT_FOUND","message":"输出不存在"}})
    if not path.is_file(): raise HTTPException(404,detail={"error":{"code":"OUTPUT_NOT_FOUND","message":"输出不存在"}})
    return FileResponse(path,filename=path.name)
@router.websocket("/projects/{pid}/events")
async def events(ws:WebSocket,pid:str):
    state=ws.app.state
    try: state.store.load_project(pid)
    except Exception: await ws.close(code=4404); return
    await ws.accept(); q=state.events.subscribe(pid)
    try:
        job = state.store.load_job(pid)
        await ws.send_json({"schema_version":"1.0","event_id":f"evt_{uuid4().hex}","type":"job.snapshot","project_id":pid,"job_id":job.id,"timestamp":datetime.now(timezone.utc).isoformat(),"data":job.model_dump(mode="json")})
        while True: await ws.send_json(await q.get())
    except (WebSocketDisconnect,asyncio.CancelledError): pass
    finally: state.events.unsubscribe(pid,q)
