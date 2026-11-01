import asyncio

import pytest
from app.main import create_app
from app.storage import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient

from test_previews import graph
from test_workflow import tiny_wav


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "languages",
    [
        {},
        {"source_language": None, "target_language": None},
        {"source_language": "ko"},
        {"target_language": "fr"},
        {"source_language": "ja", "target_language": "zh-CN"},
    ],
)
async def test_empty_project_optional_languages_preserved_without_read_migration(
    tmp_path, languages
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/api/v1/projects/empty", json={"name": "  only a name  ", **languages}
        )
        assert response.status_code == 201, response.text
        project = response.json()["project"]
        assert response.json()["job"] is None
        assert project["name"] == "only a name"
        assert project["source"] is None and project["audios"] == []
        for key in ("source_language", "target_language"):
            assert project[key] == languages.get(key)
        pid = project["id"]
        path = app.state.store.project_dir(pid) / "project.json"
        original = path.read_bytes()
        assert (await c.get(f"/api/v1/projects/{pid}")).json() == project
        assert (await c.get("/api/v1/projects")).json()["items"] == [project]
        assert FileSystemProjectStore(tmp_path).load_project(pid).model_dump(mode="json") == project
        assert path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_language", "target_language"])
@pytest.mark.parametrize("value", ["", " ", " ja", "ja ", "j\na", "a" * 33])
async def test_empty_project_rejects_invalid_optional_language(tmp_path, field, value):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/projects/empty", json={"name": "x", field: value})
        assert response.status_code == 422
        assert (await c.get("/api/v1/projects")).json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("languages", [{}, {"source_language": "ja", "target_language": "zh-CN"}])
async def test_name_only_project_upload_and_run_preserve_project_and_history_languages(
    tmp_path, languages
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post("/api/v1/projects/empty", json={"name": "name-only", **languages})
        assert response.status_code == 201
        pid = response.json()["project"]["id"]
        root = f"/api/v1/projects/{pid}"
        saved = await c.put(root + "/workflow", json=dict(expected_revision=0, **graph()))
        assert saved.status_code == 200
        missing = await c.post(
            root + "/audios", files={"file": ("source.wav", tiny_wav(), "audio/wav")}
        )
        assert missing.status_code == 422
        assert (await c.get(root + "/audios")).json()["items"] == []
        uploaded = await c.post(
            root + "/audios",
            files={"file": ("source.wav", tiny_wav(), "audio/wav")},
            data={"source_language": "ko"},
        )
        assert uploaded.status_code == 201
        audio = uploaded.json()["audio"]
        assert audio["source"]["language"] == "ko"
        aid = audio["id"]
        started = await c.post(
            root + f"/audios/{aid}/workflow-runs",
            json={"workflow_revision": saved.json()["workflow_revision"]},
        )
        assert started.status_code == 202
        run_url = root + f"/audios/{aid}/workflow-runs/{started.json()['id']}"
        for _ in range(100):
            run = (await c.get(run_url)).json()
            if run["status"] not in ("pending", "running"):
                break
            await asyncio.sleep(0.02)
        assert run["status"] == "completed"
        run_path = app.state.store.workflow_run_dir(pid, aid, run["id"]) / "run.json"
        original = run_path.read_bytes()
        second = await c.post(
            root + "/audios",
            files={"file": ("other.wav", tiny_wav(), "audio/wav")},
            data={"source_language": "fr"},
        )
        assert second.status_code == 201
        project = (await c.get(root)).json()
        assert project["source_language"] == languages.get("source_language")
        assert project["target_language"] == languages.get("target_language")
        assert [a["source"]["language"] for a in project["audios"]] == ["ko", "fr"]
        assert (await c.get(run_url)).json() == run
        assert run_path.read_bytes() == original
        assert (await c.get(root + f"/audios/{aid}/workflow-runs")).json()["total"] == 1
