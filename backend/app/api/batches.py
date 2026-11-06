import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.batch import BatchDetail, BatchItemList, BatchList, BatchSummary
from ..domain.schema import SCHEMA_VERSION, SchemaVersionError
from ..storage import ProjectNotFound
from .workflow import _error, _project

router = APIRouter(prefix="/api/v1/projects/{pid}/batches")


class BatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_revision: int = Field(ge=0)
    audio_ids: list[str] = Field(min_length=1, max_length=10000)
    request_id: str = Field(min_length=1, max_length=128)

    @field_validator("audio_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("audio_ids must be unique")
        return value


class BatchRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=128)
    include_cancelled: bool = False


def _batch(request, pid, bid, *, persisted=False):
    _project(request, pid)
    try:
        manager = request.app.state.batches
        return manager.store.load(pid, bid) if persisted else manager.get(pid, bid)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(404, detail=_error("BATCH_NOT_FOUND", "批次不存在")) from None


def _creation_error(error):
    code = str(error)
    messages = {
        "BATCH_REQUEST_CONFLICT": "该请求标识已用于其他批次请求",
        "BATCH_BUSY": "项目已有批次正在运行或排队",
        "BATCH_STOPPING": "服务正在停止，请稍后重试",
        "AUDIO_BUSY": "所选音频已有任务正在运行或排队",
        "WORKFLOW_REVISION_CONFLICT": "工作流版本已变化，请刷新后重试",
        "BATCH_EXPLICIT_AUDIO_BINDING": "批处理要求输入节点使用当前音频，请解除指定音频绑定",
        "BATCH_NOT_RETRYABLE": "没有可重试的失败或中断项",
        "INVALID_WORKFLOW": "工作流无效，无法运行",
        "AUDIO_NOT_FOUND": "所选音频不存在",
    }
    status = 409 if isinstance(error, RuntimeError) else 422
    if code not in messages:
        code = "INVALID_WORKFLOW"
    return HTTPException(status, detail=_error(code, messages[code]))


@router.post("", status_code=202, response_model=BatchSummary)
async def create_batch(pid: str, body: BatchCreateRequest, request: Request):
    _project(request, pid)
    try:
        batch = await request.app.state.batches.create(
            pid, body.workflow_revision, body.audio_ids, body.request_id
        )
    except SchemaVersionError:
        raise
    except (RuntimeError, ValueError) as error:
        raise _creation_error(error) from error
    return batch.summary()


@router.get("", response_model=BatchList)
async def list_batches(
    pid: str, request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    _project(request, pid)
    batches = request.app.state.batches.store.list(pid)
    return BatchList(
        items=[batch.summary() for batch in batches[offset : offset + limit]],
        total=len(batches),
        limit=limit,
        offset=offset,
    )


@router.get("/{bid}", response_model=BatchDetail)
async def get_batch(pid: str, bid: str, request: Request):
    return _batch(request, pid, bid).detail()


@router.get("/{bid}/items", response_model=BatchItemList)
async def list_batch_items(
    pid: str,
    bid: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    batch = _batch(request, pid, bid)
    return BatchItemList(
        items=batch.items[offset : offset + limit], total=batch.total, limit=limit, offset=offset
    )


@router.post("/{bid}/cancel", status_code=202, response_model=BatchSummary)
async def cancel_batch(pid: str, bid: str, request: Request):
    _batch(request, pid, bid)
    batch, changed = await request.app.state.batches.cancel(pid, bid)
    if not changed:
        return JSONResponse(status_code=200, content=batch.summary().model_dump(mode="json"))
    return batch.summary()


@router.post("/{bid}/retry", status_code=202, response_model=BatchSummary)
async def retry_batch(pid: str, bid: str, body: BatchRetryRequest, request: Request):
    original = _batch(request, pid, bid)
    try:
        batch = await request.app.state.batches.create(
            pid,
            original.workflow_revision,
            [],
            body.request_id,
            retry=original,
            cancelled=body.include_cancelled,
        )
    except SchemaVersionError:
        raise
    except (RuntimeError, ValueError) as error:
        raise _creation_error(error) from error
    return batch.summary()


def _safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(value)).strip(" .")[:100]
    if not value or value in {".", ".."}:
        value = "unnamed"
    if value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = f"_{value}"
    return value


def _item_directory(item):
    origin = item.origin.model_dump() if item.origin else {}
    relative = origin.get("relative_path", "")
    # Metadata is never used to access the server filesystem. Validate again
    # because persisted historical data may not have come from today's importer.
    if relative:
        if not isinstance(relative, str) or "\\" in relative:
            raise ValueError("invalid origin path")
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {".", ".."} for part in relative.split("/")):
            raise ValueError("invalid origin path")
        parents = [_safe_name(part) for part in path.parts[:-1]]
    else:
        parents = []
    root_id = origin.get("root_id", "")
    if root_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", str(root_id)):
        raise ValueError("invalid origin root")
    # The numeric item slot and separate node slot guarantee collision-free ZIP
    # names, including case-insensitive platforms and duplicate folder labels.
    root = _safe_name(origin.get("root_name", "audio"))
    if root_id:
        root += "--" + str(root_id) + "--" + hashlib.sha256(str(root_id).encode()).hexdigest()[:12]
    return PurePosixPath(root, *parents, f"{item.index + 1:05d}--{_safe_name(item.audio_name)}")


@router.get("/{bid}/download")
def download_batch(pid: str, bid: str, request: Request):
    # This route runs in a thread. Read one atomically committed state instead
    # of copying a mutable live record while dispatchers update it.
    batch = _batch(request, pid, bid, persisted=True)
    store = request.app.state.store
    descriptor, temporary = tempfile.mkstemp(prefix="voicetransl-batch-", suffix=".zip")
    os.close(descriptor)
    try:
        manifest = batch.detail().model_dump(mode="json")
        manifest["format"] = "voicetransl.batch-results"
        manifest["schema_version"] = SCHEMA_VERSION
        manifest["items"] = []
        with ZipFile(temporary, "w", ZIP_DEFLATED, allowZip64=True) as archive:
            for item in batch.items:
                entry = item.model_dump(mode="json")
                entry["outputs"] = []
                entry["missing_outputs"] = []
                entry["export_error"] = None
                directory = _item_directory(item)
                if item.run_id:
                    try:
                        run = store.load_workflow_run(pid, item.audio_id, item.run_id)
                        if run.batch_id is not None and run.batch_id != batch.id:
                            raise ValueError("batch run ownership mismatch")
                        if run.workflow.model_dump() != batch.workflow.model_dump():
                            raise ValueError("batch run snapshot mismatch")
                    except ProjectNotFound:
                        run = None
                        entry["export_error"] = {
                            "type": "BATCH_RUN_MISSING",
                            "message": "运行记录缺失",
                        }
                    for index, node in enumerate(run.nodes if run else []):
                        if node.status.value != "completed" or node.artifact is None:
                            continue
                        if (
                            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node.node_id)
                            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', node.artifact.filename)
                            or node.artifact.filename in {"", ".", ".."}
                        ):
                            raise ValueError("invalid artifact path")
                        path = store.workflow_artifact_path(
                            pid, item.audio_id, run.id, node.node_id, node.artifact.filename
                        )
                        if not path.is_file() or path.is_symlink():
                            entry["missing_outputs"].append(
                                {
                                    "node_id": node.node_id,
                                    "filename": node.artifact.filename,
                                    "type": "WORKFLOW_OUTPUT_NOT_FOUND",
                                }
                            )
                            continue
                        name = str(
                            directory
                            / f"{index:03d}--{_safe_name(node.node_id)}"
                            / _safe_name(node.artifact.filename)
                        )
                        try:
                            archive.write(path, name)
                        except FileNotFoundError:
                            entry["missing_outputs"].append(
                                {
                                    "node_id": node.node_id,
                                    "filename": node.artifact.filename,
                                    "type": "WORKFLOW_OUTPUT_NOT_FOUND",
                                }
                            )
                            continue
                        entry["outputs"].append(
                            {
                                "node_id": node.node_id,
                                "path": name,
                                "artifact": node.artifact.model_dump(mode="json"),
                            }
                        )
                manifest["items"].append(entry)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    except SchemaVersionError:
        Path(temporary).unlink(missing_ok=True)
        raise
    except (OSError, ValueError, ProjectNotFound) as error:
        Path(temporary).unlink(missing_ok=True)
        raise HTTPException(
            422, detail=_error("BATCH_DOWNLOAD_INVALID", "批次产物或来源信息无效")
        ) from error
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _TemporaryZipResponse(
        temporary,
        media_type="application/zip",
        filename=f"{bid}.zip",
    )


class _TemporaryZipResponse(FileResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)
