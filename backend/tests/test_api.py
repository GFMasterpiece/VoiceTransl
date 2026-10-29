import tempfile

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_upload_creates_new_default_audio_passthrough_workflow():
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            assert response.status_code == 201, response.text
            project = response.json()["project"]
            workflow = await client.get(f"/api/v1/projects/{project['id']}/workflow")
            assert workflow.status_code == 200
            document = workflow.json()
            assert [node["type"] for node in document["nodes"]] == [
                "audio_input",
                "audio_output",
            ]
            assert document["edges"] == [
                {
                    "id": "input-output",
                    "source": "input",
                    "source_port": "audio",
                    "target": "output",
                    "target_port": "audio",
                }
            ]
            assert project["audios"][0]["id"] == "aud_primary"
            assert not (app.state.store.project_dir(project["id"]) / "workflow.json").exists()


@pytest.mark.asyncio
async def test_project_creation_is_atomic_and_validation_errors_are_normalized():
    with tempfile.TemporaryDirectory() as data_dir:
        app = create_app(data_dir)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects",
                files={"file": ("sample.wav", b"RIFF", "audio/wav")},
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            assert response.status_code == 201
            project_id = response.json()["project"]["id"]
            project_dir = app.state.store.project_dir(project_id)
            assert (project_dir / "project.json").is_file()
            assert (project_dir / "job.json").is_file()
            assert (project_dir / "source" / "original.wav").read_bytes() == b"RIFF"
            assert all(item.name.startswith("prj_") for item in project_dir.parent.iterdir())

            invalid = await client.post("/api/v1/projects")
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
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
            projects_dir = app.state.store.projects_dir
            assert list(projects_dir.iterdir()) == []
