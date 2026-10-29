import asyncio
from pathlib import Path

import pytest
from app.application.event_bus import EventBus
from app.domain import (
    Job,
    JobStatus,
    PipelineStageName,
    Project,
    Segment,
    SourceAsset,
    StageStatus,
    Transcript,
)
from app.exporters import render_lrc, render_srt
from app.main import create_app
from app.pipeline.context import PipelineContext
from app.pipeline.runner import PipelineRunner
from app.pipeline.stages import TranslationStage
from app.storage import FileSystemProjectStore
from app.storage.atomic import atomic_write_json
from app.storage.project_store import TranscriptProtectedError
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient


async def _create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("sample.wav", b"RIFF", "audio/wav")},
        data={"source_language": "ja", "target_language": "zh-CN"},
    )
    assert response.status_code == 201
    return response.json()["project"]["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [JobStatus.RUNNING, JobStatus.CANCELLING])
async def test_run_rejects_persisted_active_job(tmp_path: Path, status: JobStatus):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await _create_project(client)
        job = app.state.store.load_job(project_id)
        job.status = status
        app.state.store.save_job(job)

        response = await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_ALREADY_RUNNING"


@pytest.mark.asyncio
async def test_resume_reruns_completed_stage_when_artifact_is_missing(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="original.wav", size_bytes=0),
    )
    project_dir = store.create_project(project)
    (project_dir / "source" / "original.wav").write_bytes(b"")
    job = Job(id="job_test", project_id=project.id)
    state = job.stages[PipelineStageName.PREPROCESS]
    state.status = StageStatus.COMPLETED
    state.progress = 1
    state.artifact = "cache/missing.json"
    job.status = JobStatus.FAILED
    store.save_job(job)

    class RecordingStage:
        name = PipelineStageName.PREPROCESS

        def __init__(self):
            self.calls = 0

        async def run(self, context):
            self.calls += 1
            artifact = context.project_dir / "cache" / "preprocess.json"
            artifact.write_text("{}", encoding="utf-8")
            context.artifacts[self.name.value] = artifact

    stage = RecordingStage()
    context = PipelineContext(
        project=project,
        project_dir=project_dir,
        job=job,
        preset={"name": "mock"},
        store=store,
        cancel_event=asyncio.Event(),
    )
    await PipelineRunner([stage], EventBus()).run(context, resume=True)

    assert stage.calls == 1
    assert store.load_job(project.id).status == JobStatus.COMPLETED
    assert (
        store.load_job(project.id).stages[PipelineStageName.PREPROCESS].status
        == StageStatus.COMPLETED
    )


@pytest.mark.asyncio
async def test_manager_marks_external_cancelled_task_interrupted(tmp_path: Path, monkeypatch):
    import app.application.job_manager as job_manager_module

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await _create_project(client)

    class CancelledRunner:
        def __init__(self, *_args):
            pass

        async def run(self, _context, resume=True):
            raise asyncio.CancelledError()

    monkeypatch.setattr(job_manager_module, "PipelineRunner", CancelledRunner)
    await app.state.jobs.start(project_id)
    task = app.state.jobs.tasks[project_id]
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state.store.load_job(project_id).status == JobStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_manager_marks_external_exception_failed(tmp_path: Path, monkeypatch):
    import app.application.job_manager as job_manager_module

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await _create_project(client)
    queue = app.state.events.subscribe(project_id)

    class FailingRunner:
        def __init__(self, *_args):
            pass

        async def run(self, _context, resume=True):
            raise RuntimeError("unexpected runner failure")

    monkeypatch.setattr(job_manager_module, "PipelineRunner", FailingRunner)
    await app.state.jobs.start(project_id)
    await app.state.jobs.tasks[project_id]
    job = app.state.store.load_job(project_id)
    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert job.error.type == "JOB_FAILED"
    assert job.error.message == "任务执行失败"
    assert "unexpected runner failure" not in job.error.message
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    failed = [event for event in events if event["type"] == "job.failed"]
    assert failed
    assert failed[-1]["data"]["message"] == "任务执行失败"
    assert "unexpected runner failure" not in str(failed[-1])


def test_store_rejects_output_and_transcript_path_traversal(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="original.wav", size_bytes=0),
    )
    store.create_project(project)
    with pytest.raises(ValueError):
        store.resolve_output(project.id, "../secret.txt")
    with pytest.raises(ValueError):
        store.load_transcript(project.id, "../raw")
    with pytest.raises(ValueError):
        SourceAsset(path="../outside.wav", original_name="outside.wav", size_bytes=0)


def test_store_protects_edited_transcript_and_touches_project(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="original.wav", size_bytes=0),
    )
    store.create_project(project)
    value = Transcript(
        project_id=project.id,
        stage="raw",
        language="ja",
        segments=[Segment(id="1", source_text="a")],
    )
    store.save_transcript(project.id, "raw", value)
    before = store.load_project(project.id).updated_at
    edited = value.model_copy(update={"segments": [Segment(id="1", source_text="manual")]})
    with pytest.raises(TranscriptProtectedError):
        store.save_transcript(project.id, "raw", edited)
    store.save_transcript(project.id, "raw", edited, overwrite=True)
    assert store.load_transcript(project.id, "raw").segments[0].source_text == "manual"
    assert store.load_project(project.id).updated_at >= before


def test_store_rejects_project_directory_symlink(tmp_path: Path):
    real = tmp_path / "real-projects"
    real.mkdir()
    projects = tmp_path / "projects"
    try:
        projects.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symlinks for this security test")
        raise
    with pytest.raises(ValueError):
        FileSystemProjectStore(tmp_path)


def test_store_rejects_source_directory_symlink(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    project_dir = store.create_project(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_dir / "source").rmdir()
    try:
        (project_dir / "source").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symlinks for this security test")
        raise
    with pytest.raises(ValueError):
        store.save_source(project.id, "wav", b"x")


@pytest.mark.parametrize("subdir", ["cache", "transcript", "output", "logs"])
def test_store_rejects_artifact_directory_symlink(tmp_path: Path, subdir: str):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    project_dir = store.create_project(project)
    outside = tmp_path / f"outside-{subdir}"
    outside.mkdir()
    (project_dir / subdir).rmdir()
    try:
        (project_dir / subdir).symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symlinks for this security test")
        raise
    with pytest.raises(ValueError):
        if subdir == "cache":
            store.save_cache_json(project.id, "x.json", {})
        elif subdir == "output":
            store.save_output_text(project.id, "x.srt", "x")
        elif subdir == "logs":
            store.append_log(project.id, "x")
        else:
            store.save_transcript(
                project.id, "raw", Transcript(project_id=project.id, stage="raw", language="ja")
            )


def test_atomic_json_failure_keeps_previous_file(tmp_path: Path, monkeypatch):
    import app.storage.atomic as atomic

    path = tmp_path / "state.json"
    atomic_write_json(path, {"version": 1})
    monkeypatch.setattr(
        atomic.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace failed"))
    )
    with pytest.raises(OSError):
        atomic_write_json(path, {"version": 2})
    assert path.read_text(encoding="utf-8").find('"version": 1') >= 0


def test_domain_models_round_trip_json():
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=1),
    )
    job = Job(id="job_test", project_id=project.id)
    transcript = Transcript(
        project_id=project.id,
        stage="raw",
        language="ja",
        segments=[Segment(id="1", source_text="x")],
    )
    assert Project.model_validate_json(project.model_dump_json()) == project
    assert Job.model_validate_json(job.model_dump_json()) == job
    assert Transcript.model_validate_json(transcript.model_dump_json()) == transcript


def test_formatters_use_half_up_and_carry():
    segment = Segment(id="1", start=1.2345, end=2.0, source_text="日", translated_text="中")
    assert "00:00:01,235" in render_srt([segment])
    carry = Segment(id="1", start=59.9995, end=60.0, source_text="日", translated_text="中")
    assert "00:01:00,000" in render_srt([carry])
    assert "[01:00.00]" in render_lrc([carry])


@pytest.mark.asyncio
async def test_translation_provider_cannot_mutate_source_fields(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    project_dir = store.create_project(project)

    class MutatingProvider:
        async def translate(self, segments, _context):
            segments[0].source_text = "changed"
            segments[0].translated_text = "ok"
            return segments

    transcript = Transcript(
        project_id=project.id,
        stage="aligned",
        language="ja",
        segments=[Segment(id="1", start=0, end=1, source_text="original")],
    )
    context = PipelineContext(
        project=project,
        project_dir=project_dir,
        job=Job(id="job_test", project_id=project.id),
        preset={},
        store=store,
        cancel_event=asyncio.Event(),
        transcript=transcript,
        allow_transcript_overwrite=True,
    )
    with pytest.raises(ValueError, match="immutable"):
        await TranslationStage(MutatingProvider()).run(context)
    assert context.transcript.segments[0].source_text == "original"


def test_event_bus_removes_empty_subscriptions():
    bus = EventBus()
    queue = bus.subscribe("prj_test")
    assert "prj_test" in bus._subs
    bus.unsubscribe("prj_test", queue)
    assert "prj_test" not in bus._subs


def test_real_websocket_receives_events_and_cleans_subscription(tmp_path: Path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 1
    with TestClient(app) as client:
        project_id = client.post(
            "/api/v1/projects",
            files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            data={"source_language": "ja", "target_language": "zh-CN"},
        ).json()["project"]["id"]
        with client.websocket_connect(f"/api/v1/projects/{project_id}/events") as socket:
            assert socket.receive_json()["type"] == "job.snapshot"
            assert client.post(f"/api/v1/projects/{project_id}/run", json={}).status_code == 202
            event_types = set()
            while "job.completed" not in event_types:
                event_types.add(socket.receive_json()["type"])
            assert "stage.started" in event_types
        assert project_id not in app.state.events._subs


@pytest.mark.asyncio
async def test_cancel_running_job_is_persisted_and_resumable(tmp_path: Path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 100
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await _create_project(client)
        assert (await client.post(f"/api/v1/projects/{project_id}/run", json={})).status_code == 202
        # Let the task enter a stage before requesting cooperative cancellation.
        for _ in range(100):
            if app.state.jobs.running(project_id):
                break
            await asyncio.sleep(0.001)
        assert (await client.post(f"/api/v1/projects/{project_id}/cancel")).status_code == 200
        await app.state.jobs.tasks[project_id]
        cancelled = app.state.store.load_job(project_id)
        assert cancelled.status == JobStatus.CANCELLED
        assert (
            await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})
        ).status_code == 202
    await app.state.jobs.tasks[project_id]
    assert app.state.store.load_job(project_id).status == JobStatus.COMPLETED
