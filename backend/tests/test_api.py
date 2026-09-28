import asyncio
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_upload_run_and_outputs():
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")})
            assert response.status_code == 201
            project_id = response.json()["project"]["id"]
            assert (await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})).status_code == 202
            for _ in range(100):
                job = (await client.get(f"/api/v1/projects/{project_id}/job")).json()
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.05)
            assert job["status"] == "completed"
            names = {item["name"] for item in (await client.get(f"/api/v1/projects/{project_id}/outputs")).json()["items"]}
            assert names == {"zh.srt", "ja-zh.srt", "zh.lrc"}


@pytest.mark.asyncio
async def test_upload_commits_complete_directory_and_validation_errors_are_normalized():
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            )
            assert response.status_code == 201
            project_id = response.json()["project"]["id"]
            project_dir = app.state.store.project_dir(project_id)
            assert (project_dir / "project.json").is_file()
            assert (project_dir / "job.json").is_file()
            assert (project_dir / "source" / "original.wav").read_bytes() == b"RIFF"
            assert all(item.name.startswith("prj_") for item in project_dir.parent.iterdir())

            invalid = await client.post(f"/api/v1/projects/{project_id}/run")
            assert invalid.status_code == 422
            body = invalid.json()
            assert body["error"]["code"] == "VALIDATION_ERROR"
            assert "detail" not in body


@pytest.mark.asyncio
async def test_oversized_upload_leaves_no_project_directory():
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(data_dir)
        app.state.settings.max_upload_mb = 0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
            projects_dir = app.state.store.projects_dir
            assert list(projects_dir.iterdir()) == []
