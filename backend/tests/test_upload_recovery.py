import asyncio
import json
from pathlib import Path

import pytest

from app.domain import Job, JobStatus, Project, SourceAsset, default_stages
from app.storage import FileSystemProjectStore
import app.storage.project_store as project_store_module


def _models(project_id: str = "prj_upload"):
    project = Project(
        id=project_id,
        name="upload",
        source=SourceAsset(path="source/original.wav", original_name="original.wav", size_bytes=0),
    )
    job = Job(id="job_upload", project_id=project_id, stages=default_stages())
    return project, job


class _CancelledUpload:
    async def read(self, _size):
        raise asyncio.CancelledError()


class _FailingUpload:
    async def read(self, _size):
        raise OSError("read failed")


@pytest.mark.asyncio
async def test_cancelled_upload_removes_unpublished_temp_project(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project, job = _models()

    with pytest.raises(asyncio.CancelledError):
        await store.create_project_from_upload(project, job, _CancelledUpload(), max_bytes=100)

    assert not (store.projects_dir / project.id).exists()
    assert not list(store.projects_dir.glob(f".{project.id}.tmp_*"))
    assert store.list_projects() == []


@pytest.mark.asyncio
async def test_upload_read_failure_removes_temp_without_formal_project(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project, job = _models()

    with pytest.raises(OSError, match="read failed"):
        await store.create_project_from_upload(project, job, _FailingUpload(), max_bytes=100)

    assert not list(store.projects_dir.glob(f".{project.id}.tmp_*"))


@pytest.mark.asyncio
async def test_cancelled_source_upload_removes_temp_and_keeps_existing_source(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project, _ = _models()
    store.create_project(project)
    target = store.projects_dir / project.id / "source" / "original.wav"
    target.write_bytes(b"old")

    with pytest.raises(asyncio.CancelledError):
        await store.save_source_upload(project.id, "wav", _CancelledUpload(), max_bytes=100)

    assert target.read_bytes() == b"old"
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


@pytest.mark.asyncio
async def test_rename_failure_preserves_formal_project(tmp_path: Path, monkeypatch):
    store = FileSystemProjectStore(tmp_path)
    project, job = _models()
    original_replace = project_store_module.os.replace
    final_dir = store.projects_dir / project.id

    def fail_publish(source, target):
        if Path(target) == final_dir and Path(source).name.startswith(f".{project.id}.tmp_"):
            final_dir.mkdir()
            (final_dir / "sentinel").write_text("keep", encoding="utf-8")
            raise OSError("publish rename failed")
        return original_replace(source, target)

    monkeypatch.setattr(project_store_module.os, "replace", fail_publish)
    upload = type("Upload", (), {"read": _read_once})()
    with pytest.raises(OSError, match="publish rename failed"):
        await store.create_project_from_upload(project, job, upload, max_bytes=100)

    assert (final_dir / "sentinel").read_text(encoding="utf-8") == "keep"
    assert not list(store.projects_dir.glob(f".{project.id}.tmp_*"))


async def _read_once(_self, _size):
    if getattr(_self, "done", False):
        return b""
    _self.done = True
    return b"audio"


def test_listing_and_interruption_ignore_temp_and_invalid_directories(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project, job = _models("prj_valid")
    store.create_project(project)
    job.status = JobStatus.RUNNING
    store.save_job(job)

    temp = store.projects_dir / ".prj_valid.tmp_leftover"
    temp.mkdir()
    (temp / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
    (temp / "job.json").write_text(job.model_dump_json(), encoding="utf-8")
    invalid = store.projects_dir / "unrelated"
    invalid.mkdir()
    (invalid / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
    malformed = store.projects_dir / "broken"
    malformed.mkdir()
    (malformed / "project.json").write_text("{}", encoding="utf-8")

    assert [item.id for item in store.list_projects()] == [project.id]
    store.mark_interrupted()
    assert store.load_job(project.id).status == JobStatus.INTERRUPTED
    assert json.loads((temp / "job.json").read_text(encoding="utf-8"))["status"] == "running"
