import pytest
from app.domain import JobStatus, default_pipeline_config
from app.main import create_app
from httpx import ASGITransport, AsyncClient


async def create_project(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("sample.wav", b"RIFF", "audio/wav")},
    )
    assert response.status_code == 201
    return response.json()["project"]["id"]


def test_default_config_for_chinese_source_uses_english_target():
    config = default_pipeline_config("zh")
    assert config.target_language == "en"
    assert config.translation.target_language == "en"


def config_payload(source_language: str = "ja") -> dict:
    return {
        "source_language": source_language,
        "target_language": "zh-CN",
        "preprocess": {"sample_rate": 48_000, "channels": 1, "preserve_original": True},
        "asr": {
            "provider": "mock",
            "model": "mock-v2",
            "language": source_language,
            "vad_enabled": False,
            "chunk_seconds": 45,
        },
        "alignment": {
            "enabled": True,
            "provider": "mock",
            "model": "align-v1",
            "fallback": "keep_asr_timestamps",
        },
        "translation": {
            "provider": "mock",
            "model": "translator-v2",
            "target_language": "zh-CN",
            "batch_size": 4,
            "temperature": 0.1,
            "glossary": {"先輩": "前辈"},
            "instructions": "保留语气",
        },
        "export": {"formats": ["srt", "lrc"], "bilingual": True, "naming": "language"},
    }


@pytest.mark.asyncio
async def test_config_round_trip_and_snapshot_is_persisted(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        initial = await client.get(f"/api/v1/projects/{project_id}/config")
        assert initial.status_code == 200
        assert initial.json()["source_language"] == "ja"

        updated = await client.put(f"/api/v1/projects/{project_id}/config", json=config_payload())
        assert updated.status_code == 200
        assert updated.json()["translation"]["model"] == "translator-v2"

        assert (
            await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})
        ).status_code == 202
        await app.state.jobs.tasks[project_id]

        job = app.state.store.load_job(project_id)
        assert job.status == JobStatus.COMPLETED
        assert job.config_snapshot is not None
        assert job.config_snapshot.translation.model == "translator-v2"
        assert job.config_snapshot.asr.chunk_seconds == 45

        editable = config_payload()
        editable["translation"]["model"] = "rerun-model"
        updated_after_completion = await client.put(
            f"/api/v1/projects/{project_id}/config", json=editable
        )
        assert updated_after_completion.status_code == 200
        assert updated_after_completion.json()["translation"]["model"] == "rerun-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_completed_project_can_be_started_again_with_new_job(tmp_path, resume):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        assert (
            await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})
        ).status_code == 202
        await app.state.jobs.tasks[project_id]

        previous_job_id = app.state.store.load_job(project_id).id
        response = await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": resume})
        assert response.status_code == 202
        assert response.json()["job_id"] != previous_job_id
        await app.state.jobs.tasks[project_id]

    assert app.state.store.load_job(project_id).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_completed_project_can_be_deleted(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        job = app.state.store.load_job(project_id)
        job.status = JobStatus.COMPLETED
        app.state.store.save_job(job)
        response = await client.delete(f"/api/v1/projects/{project_id}")
    assert response.status_code == 204
    assert not (tmp_path / "projects" / project_id).exists()


@pytest.mark.asyncio
async def test_running_project_cannot_be_deleted(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        job = app.state.store.load_job(project_id)
        job.status = JobStatus.RUNNING
        app.state.store.save_job(job)
        response = await client.delete(f"/api/v1/projects/{project_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_BUSY"


@pytest.mark.asyncio
async def test_config_rejects_source_language_mismatch_and_active_jobs(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        mismatch = await client.put(
            f"/api/v1/projects/{project_id}/config", json=config_payload("en")
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["error"]["code"] == "SOURCE_LANGUAGE_MISMATCH"

        job = app.state.store.load_job(project_id)
        job.status = JobStatus.RUNNING
        app.state.store.save_job(job)
        active = await client.put(f"/api/v1/projects/{project_id}/config", json=config_payload())
        assert active.status_code == 409
        assert active.json()["error"]["code"] == "CONFIG_READ_ONLY"


@pytest.mark.asyncio
async def test_failed_resume_keeps_original_config_snapshot(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project_id = await create_project(client)
        configured = config_payload()
        configured["translation"]["model"] = "first-run"
        assert (
            await client.put(f"/api/v1/projects/{project_id}/config", json=configured)
        ).status_code == 200
        job = app.state.store.load_job(project_id)
        job.status = JobStatus.FAILED
        job.config_snapshot = app.state.store.load_pipeline_config(project_id)
        app.state.store.save_job(job)

        changed = config_payload()
        changed["translation"]["model"] = "edited-after-failure"
        assert (
            await client.put(f"/api/v1/projects/{project_id}/config", json=changed)
        ).status_code == 200

        app.state.jobs.delay_ms = 0
        await app.state.jobs.start(project_id, resume=True)
        await app.state.jobs.tasks[project_id]
        resumed = app.state.store.load_job(project_id)
        assert resumed.config_snapshot is not None
        assert resumed.config_snapshot.translation.model == "first-run"
