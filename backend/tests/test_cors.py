import pytest
import yaml
from app.config import AppSettings, load_settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_settings import save_body
from test_workflow import tiny_wav


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "https://localhost:5173",
        "http://LOCALHOST:1",
        "http://127.0.0.1:65535",
        "https://127.0.0.1",
        "http://[::1]:5173",
        "https://[::1]",
    ],
)
async def test_loopback_health_is_readable_without_credentials(tmp_path, origin):
    app = create_app(tmp_path)
    assert app.state.settings.cors_origins == []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health", headers={"Origin": origin})
    assert response.status_code == 200 and response.json() == {"status": "ok"}
    assert response.headers["access-control-allow-origin"] == origin
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "null",
        "https://app.example",
        "http://localhost.evil",
        "http://localhost.",
        "http://127.0.0.1.evil",
        "http://localhost@evil.example",
        "http://evil@localhost",
        "http://localhost/path",
        "http://localhost/",
        "http://localhost?query",
        "http://localhost#fragment",
        "http://127.0.0.2",
        "http://127.1",
        "http://[::ffff:127.0.0.1]",
        "http://[::1].evil",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:05173",
        "ftp://localhost",
        "https://localhost:5173.evil",
    ],
)
async def test_default_cors_does_not_match_lookalikes_or_non_origins(tmp_path, origin):
    async with AsyncClient(
        transport=ASGITransport(app=create_app(tmp_path)), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health", headers={"Origin": origin})
        preflight = await client.options(
            "/api/v1/settings", headers={"Origin": origin, "Access-Control-Request-Method": "PUT"}
        )
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.asyncio
async def test_explicit_remote_origins_are_exact_and_normalized(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "VOICETRANSL_CORS_ORIGINS", "https://app.example:8443, https://EXAMPLE.com:443"
    )
    app = create_app(tmp_path)
    assert app.state.settings.cors_origins == ["https://app.example:8443", "https://example.com"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for origin in app.state.settings.cors_origins:
            response = await client.get("/api/v1/health", headers={"Origin": origin})
            assert response.headers["access-control-allow-origin"] == origin
        for origin in (
            "https://app.example",
            "http://app.example:8443",
            "https://app.example:8443.evil",
            "https://sub.example.com",
        ):
            response = await client.get("/api/v1/health", headers={"Origin": origin})
            assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_cors_preflight_writes_upload_media_range_and_errors(tmp_path):
    app = create_app(tmp_path)
    origin = "http://localhost:5173"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": origin}
    ) as client:
        for path, method, headers in (
            ("/api/v1/settings", "PUT", "content-type"),
            ("/api/v1/projects", "POST", "content-type"),
            ("/api/v1/projects/id", "DELETE", ""),
            ("/api/v1/projects/id/audios/id/source", "GET", "range,if-range"),
        ):
            preflight = await client.options(
                path,
                headers={
                    "Access-Control-Request-Method": method,
                    **({"Access-Control-Request-Headers": headers} if headers else {}),
                },
            )
            assert preflight.status_code == 200, preflight.text
            assert preflight.headers["access-control-allow-origin"] == origin
            assert "access-control-allow-credentials" not in preflight.headers
        current = (await client.get("/api/v1/settings")).json()
        saved = await client.put("/api/v1/settings", json=save_body(current))
        assert saved.status_code == 200
        assert saved.headers["access-control-allow-origin"] == origin
        assert "cors_origins" not in saved.json()["global"]
        uploaded = await client.post(
            "/api/v1/projects",
            files={"file": ("source.wav", tiny_wav(), "audio/wav")},
            data={"source_language": "ja", "target_language": "en"},
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.headers["access-control-allow-origin"] == origin
        project = uploaded.json()["project"]
        media = await client.get(
            f"/api/v1/projects/{project['id']}/audios/{project['audios'][0]['id']}/source",
            headers={"Range": "bytes=0-3"},
        )
        assert media.status_code == 206 and media.content == b"RIFF"
        assert media.headers["content-range"].startswith("bytes 0-3/")
        assert media.headers["accept-ranges"] == "bytes"
        assert media.headers["content-disposition"]
        assert media.headers["access-control-allow-origin"] == origin
        assert {"content-disposition", "content-range", "accept-ranges", "content-length"} <= {
            part.strip().lower()
            for part in media.headers["access-control-expose-headers"].split(",")
        }
        invalid = await client.put("/api/v1/settings", json={})
        missing = await client.get("/api/v1/no-such-route")
        for response, status in ((invalid, 422), (missing, 404)):
            assert response.status_code == status
            assert response.headers["access-control-allow-origin"] == origin
        assert "error" in invalid.json()
        assert missing.json() == {"detail": "Not Found"}
        for method, header in (
            ("PATCH", "content-type"),
            ("PUT", "authorization"),
            ("PUT", "x-arbitrary-header"),
        ):
            rejected = await client.options(
                "/api/v1/settings",
                headers={
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": header,
                },
            )
            assert rejected.status_code == 400


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "https://app.example",
        ["*"],
        ["null"],
        ["https://*.example"],
        ["ftp://app.example"],
        ["https://user:pass@app.example"],
        ["https://user@app.example"],
        ["https://app.example/"],
        ["https://app.example/path"],
        ["https://app.example?"],
        ["https://app.example#"],
        ["https://app.example:0"],
        ["https://app.example:65536"],
        ["https://app.example:"],
        [" https://app.example"],
        ["https://app.example\n"],
        ["https://app.example\\path"],
        [""],
    ],
)
def test_cors_yaml_invalid_origins_are_rejected(tmp_path, monkeypatch, invalid):
    monkeypatch.delenv("VOICETRANSL_CORS_ORIGINS", raising=False)
    config = tmp_path / "app.yaml"
    config.write_text(yaml.safe_dump({"app": {"cors_origins": invalid}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(config)


@pytest.mark.parametrize(
    "invalid",
    [
        "*",
        "null",
        "https://user@app.example",
        "https://app.example/path",
        "https://app.example,",
        "https://app.example,,https://other.example",
        "https://app.example?x=1",
    ],
)
def test_cors_env_invalid_origins_are_rejected(monkeypatch, invalid):
    monkeypatch.setenv("VOICETRANSL_CORS_ORIGINS", invalid)
    with pytest.raises(ValueError):
        load_settings()


def test_cors_env_overrides_yaml_and_empty_value_clears_remote_allowlist(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICETRANSL_CORS_ORIGINS", raising=False)
    config = tmp_path / "app.yaml"
    config.write_text("app:\n  cors_origins:\n    - https://yaml.example\n", encoding="utf-8")
    assert load_settings(config).cors_origins == ["https://yaml.example"]
    monkeypatch.setenv("VOICETRANSL_CORS_ORIGINS", "http://env.example:8080,https://env.example")
    assert load_settings(config).cors_origins == ["http://env.example:8080", "https://env.example"]
    monkeypatch.setenv("VOICETRANSL_CORS_ORIGINS", "")
    assert load_settings(config).cors_origins == []
    assert AppSettings().cors_origins == []
