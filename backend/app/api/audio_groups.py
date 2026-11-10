"""Manage reusable audio groups without copying or deleting audio resources."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import Field

from ..domain.audio_group import AudioGroup, AudioGroupMode, AudioGroupValues
from ..domain.limits import MAX_BATCH_AUDIO_ITEMS
from ..domain.schema import SchemaVersionError
from ..storage.audio_group_store import (
    AudioGroupAudioNotFound,
    AudioGroupNotFound,
    AudioGroupRevisionConflict,
    AudioGroupStore,
)
from ..storage.project_store import ProjectNotFound

router = APIRouter(prefix="/api/v1")


class AudioGroupUpdate(AudioGroupValues):
    expected_revision: int = Field(ge=1, strict=True)
    audio_ids: list[Annotated[str, Field(strict=True)]] = Field(
        ..., max_length=MAX_BATCH_AUDIO_ITEMS, strict=True
    )
    background: str = Field(..., strict=True)
    mode: AudioGroupMode


def _error(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details}}


def _store(request, project_id):
    projects = request.app.state.store
    try:
        projects.load_project(project_id)
    except SchemaVersionError:
        raise
    except (ProjectNotFound, ValueError):
        raise HTTPException(404, detail=_error("PROJECT_NOT_FOUND", "项目不存在")) from None
    return AudioGroupStore(projects)


def _raise_error(error):
    if isinstance(error, ProjectNotFound):
        raise HTTPException(404, detail=_error("PROJECT_NOT_FOUND", "项目不存在")) from None
    if isinstance(error, SchemaVersionError):
        raise error
    if isinstance(error, AudioGroupNotFound):
        raise HTTPException(404, detail=_error("GROUP_NOT_FOUND", "音频组不存在")) from None
    if isinstance(error, AudioGroupRevisionConflict):
        raise HTTPException(
            409, detail=_error("GROUP_REVISION_CONFLICT", "音频组已变化，请刷新后重试")
        ) from None
    if isinstance(error, AudioGroupAudioNotFound):
        raise HTTPException(
            422,
            detail=_error(
                "GROUP_AUDIO_NOT_FOUND", "组内音频不属于当前项目", {"audio_ids": error.audio_ids}
            ),
        ) from None
    if isinstance(error, ValueError):
        raise HTTPException(422, detail=_error("GROUP_INVALID", "音频组数据无效")) from None
    raise HTTPException(503, detail=_error("GROUP_STORAGE_ERROR", "音频组存储暂时不可用")) from None


@router.get("/projects/{pid}/audio-groups")
async def list_groups(
    pid: str, request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
):
    store = _store(request, pid)
    try:
        items, total = store.list(pid, limit=limit, offset=offset)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except (OSError, ValueError) as error:
        _raise_error(error)


@router.post("/projects/{pid}/audio-groups", status_code=201, response_model=AudioGroup)
async def create_group(pid: str, body: AudioGroupValues, request: Request):
    store = _store(request, pid)
    try:
        async with store.projects.mutation_lock(pid):
            return store.create(pid, **body.model_dump(mode="json"))
    except (OSError, ValueError) as error:
        _raise_error(error)


@router.get("/projects/{pid}/audio-groups/{gid}", response_model=AudioGroup)
async def get_group(pid: str, gid: str, request: Request):
    store = _store(request, pid)
    try:
        return store.load(pid, gid)
    except (OSError, ValueError) as error:
        _raise_error(error)


@router.put("/projects/{pid}/audio-groups/{gid}", response_model=AudioGroup)
async def update_group(pid: str, gid: str, body: AudioGroupUpdate, request: Request):
    store = _store(request, pid)
    try:
        async with store.projects.mutation_lock(pid):
            return store.update(
                pid,
                gid,
                body.expected_revision,
                **body.model_dump(mode="json", exclude={"expected_revision"}),
            )
    except (OSError, ValueError) as error:
        _raise_error(error)


@router.delete("/projects/{pid}/audio-groups/{gid}", status_code=204)
async def delete_group(
    pid: str, gid: str, request: Request, expected_revision: int = Query(..., ge=1)
):
    from ..domain.workflow_scope import workflow_group_references

    store = _store(request, pid)
    try:
        async with store.projects.mutation_lock(pid):
            current = store.load(pid, gid)
            if current.revision != expected_revision:
                raise AudioGroupRevisionConflict
            document = store.projects.load_workflow(pid)
            references = [
                node_id
                for node_id, group_id in workflow_group_references(document.nodes)
                if group_id == gid
            ]
            if references:
                raise HTTPException(
                    409,
                    detail=_error(
                        "GROUP_REFERENCED", "当前工作流仍在使用此音频组", {"node_ids": references}
                    ),
                )
            store.delete(pid, gid, expected_revision)
    except (OSError, ValueError) as error:
        _raise_error(error)
