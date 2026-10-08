import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_upload_policy_defaults(tmp_path, monkeypatch):
    config = tmp_path / "app.yaml"
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("VOICETRANSL_CONFIG", str(config))
    monkeypatch.delenv("VOICETRANSL_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("VOICETRANSL_ALLOWED_EXTENSIONS", raising=False)
    app = create_app(tmp_path / "data")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/upload-policy")

    assert response.status_code == 200
    assert response.json() == {
        "allowed_extensions": ["wav", "flac", "mp3", "m4a", "aac", "ogg", "opus"],
        "max_upload_bytes": 4096 * 1024 * 1024,
    }
    assert list(app.state.store.projects_dir.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("max_upload_mb,extensions", [(1, " .WAV, FLAC "), (2, " OPUS ")])
async def test_upload_policy_matches_configured_upload_validation(
    tmp_path, monkeypatch, max_upload_mb, extensions
):
    monkeypatch.setenv("VOICETRANSL_MAX_UPLOAD_MB", str(max_upload_mb))
    monkeypatch.setenv("VOICETRANSL_ALLOWED_EXTENSIONS", extensions)
    app = create_app(tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = (await client.get("/api/v1/upload-policy")).json()
        assert policy == {
            "allowed_extensions": app.state.settings.allowed_extensions,
            "max_upload_bytes": max_upload_mb * 1024 * 1024,
        }
        extension = policy["allowed_extensions"][0]
        accepted = await client.post(
            "/api/v1/projects",
            files={"file": (f"sample.{extension.upper()}", b"a" * policy["max_upload_bytes"])},
        )
        assert accepted.status_code == 201
        unsupported = await client.post("/api/v1/projects", files={"file": ("sample.txt", b"a")})
        assert unsupported.status_code == 415
        oversized = await client.post(
            "/api/v1/projects",
            files={"file": (f"sample.{extension}", b"a" * (policy["max_upload_bytes"] + 1))},
        )
        assert oversized.status_code == 413
        assert len(app.state.store.list_projects()) == 1


@pytest.mark.asyncio
async def test_upload_policy_reads_active_settings_without_writing(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        assert created.status_code == 201
        app.state.settings.allowed_extensions = ["flac"]
        app.state.settings.max_upload_mb = 7
        settings_before = app.state.settings.model_dump()
        files_before = {
            path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
            if path.is_file()
        }

        response = await client.get("/api/v1/upload-policy")

        assert response.status_code == 200
        assert response.json() == {
            "allowed_extensions": ["flac"],
            "max_upload_bytes": 7 * 1024 * 1024,
        }
        assert app.state.settings.model_dump() == settings_before
        assert {
            path.relative_to(tmp_path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in tmp_path.rglob("*")
            if path.is_file()
        } == files_before
        project_id = created.json()["project"]["id"]
        assert app.state.store.load_job(project_id).status == "pending"
        assert not app.state.jobs.running(project_id)
