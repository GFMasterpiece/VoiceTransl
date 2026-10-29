from datetime import datetime, timezone

import pytest
from app.domain import Project, SourceAsset
from app.main import create_app
from httpx import ASGITransport, AsyncClient


async def create(client, *, source="ja", target="zh-CN"):
    return await client.post(
        "/api/v1/projects",
        files={"file": ("sample.wav", b"RIFF", "audio/wav")},
        data={"source_language": source, "target_language": target},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("zh", "zh-CN"),
        ("zh-CN", "en"),
        ("ZH-cn", "ja"),
        ("en", "ja"),
        ("ja", "zh-CN"),
    ],
)
async def test_create_project_persists_explicit_languages_without_same_language_default(
    tmp_path, source, target
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, source=source, target=target)
        assert response.status_code == 201, response.text
        pid = response.json()["project"]["id"]
        config = (await client.get(f"/api/v1/projects/{pid}/config")).json()
        assert config["source_language"] == source
        assert config["target_language"] == target
        assert config["asr"]["language"] == source
        assert config["translation"]["target_language"] == target


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("", "zh-CN"),
        ("ja", ""),
        (" ja", "zh-CN"),
        ("ja", "zh-CN "),
        ("ja", "ja"),
        ("EN", "en"),
        ("ja\n", "zh-CN"),
        ("ja", "zh\x00-CN"),
    ],
)
async def test_create_project_rejects_missing_trimmed_same_or_control_languages(
    tmp_path, source, target
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await create(client, source=source, target=target)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert list(app.state.store.projects_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_create_project_rejects_missing_language_forms(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        assert missing.status_code == 422
        no_target = await client.post(
            "/api/v1/projects",
            files={"file": ("sample.wav", b"RIFF", "audio/wav")},
            data={"source_language": "ja"},
        )
        assert no_target.status_code == 422
        assert list(app.state.store.projects_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_append_audio_requires_only_source_language_and_inherits_target(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await create(client)
        pid = created.json()["project"]["id"]
        base = f"/api/v1/projects/{pid}/audios"
        missing = await client.post(base, files={"file": ("extra.wav", b"RIFF", "audio/wav")})
        assert missing.status_code == 422
        appended = await client.post(
            base,
            files={"file": ("extra.wav", b"RIFF", "audio/wav")},
            data={"source_language": "en"},
        )
        assert appended.status_code == 201, appended.text
        aid = appended.json()["audio"]["id"]
        config = (await client.get(f"/api/v1/projects/{pid}/audios/{aid}/config")).json()
        assert config["source_language"] == "en"
        assert config["asr"]["language"] == "en"
        assert config["target_language"] == "zh-CN"
        assert config["translation"]["target_language"] == "zh-CN"


@pytest.mark.asyncio
async def test_project_list_pagination_has_stable_total_and_bounds(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ids = []
        for suffix in ("a", "b", "c"):
            response = await client.post(
                "/api/v1/projects",
                files={"file": (f"{suffix}.wav", b"RIFF", "audio/wav")},
                data={"source_language": "ja", "target_language": "zh-CN"},
            )
            ids.append(response.json()["project"]["id"])
        first = (await client.get("/api/v1/projects?limit=2&offset=0")).json()
        second = (await client.get("/api/v1/projects?limit=2&offset=2")).json()
        assert first["total"] == second["total"] == 3
        assert first["limit"] == second["limit"] == 2
        assert first["offset"] == 0 and second["offset"] == 2
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1
        assert {item["id"] for item in first["items"] + second["items"]} == set(ids)
        default = (await client.get("/api/v1/projects")).json()
        assert default["limit"] == 20 and default["offset"] == 0
        assert default["total"] == 3
        for query in ("limit=0", "limit=101", "offset=-1", "limit=x"):
            invalid = await client.get(f"/api/v1/projects?{query}")
            assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_project_list_total_beyond_legacy_cap_stable_ties_and_empty_tail(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for index in range(205):
        project = Project(
            id=f"prj_page_{index:03d}",
            name=str(index),
            source=SourceAsset(
                path="source/original.wav",
                original_name="original.wav",
                size_bytes=0,
                language="ja",
            ),
            updated_at=stamp,
        )
        store.create_project(project)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.get("/api/v1/projects?limit=100&offset=0")).json()
        second = (await client.get("/api/v1/projects?limit=100&offset=100")).json()
        tail = (await client.get("/api/v1/projects?limit=100&offset=200")).json()
        empty = (await client.get("/api/v1/projects?limit=5&offset=9999")).json()
    assert first["total"] == second["total"] == tail["total"] == 205
    assert len(first["items"]) == len(second["items"]) == 100
    assert len(tail["items"]) == 5
    assert empty["total"] == 205 and empty["items"] == []
    ids = [item["id"] for item in first["items"] + second["items"] + tail["items"]]
    assert len(ids) == 205 and len(set(ids)) == 205
    assert ids == sorted(ids, reverse=True)
