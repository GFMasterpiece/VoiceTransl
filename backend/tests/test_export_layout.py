import asyncio
import hashlib
import json

import pytest
from app.domain import PipelineConfig, Project, SourceAsset, default_pipeline_config
from app.exporters import export_files, validate_export_filename
from app.main import create_app
from app.storage import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient


async def create(client, *, source="ja", target="zh-CN"):
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("sample.wav", b"RIFF", "audio/wav")},
        data={"source_language": source, "target_language": target},
    )
    assert response.status_code == 201, response.text
    return response.json()["project"]["id"]


@pytest.mark.asyncio
async def test_custom_template_manifest_and_api_metadata(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await create(client, source="ja", target="zh-CN")
        config = default_pipeline_config("ja", target_language="zh-CN").model_dump(mode="json")
        config["export"] = {
            "formats": ["srt", "vtt"],
            "bilingual": False,
            "naming": "language",
            "filename_template": "{source_language}-{target_language}-{kind}-{format}",
        }
        assert (await client.put(f"/api/v1/projects/{pid}/config", json=config)).status_code == 200
        assert (
            await client.post(f"/api/v1/projects/{pid}/run", json={"resume": True})
        ).status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        outputs = await client.get(f"/api/v1/projects/{pid}/audios/aud_primary/outputs")
        assert outputs.status_code == 200
        items = outputs.json()["items"]
        names = {item["name"] for item in items}
        assert names == {
            "ja-zh-CN-translated-srt.srt",
            "ja-zh-CN-translated-vtt.vtt",
        }
        manifest = app.state.store.load_export_manifest(pid)
        assert {item["name"] for item in manifest} == names
        for item in items:
            entry = next(value for value in manifest if value["name"] == item["name"])
            assert item["format"] == entry["format"]
            assert item["kind"] == entry["kind"]
            assert item["bilingual"] is False
            assert item["source_language"] == "ja"
            assert item["target_language"] == "zh-CN"
            assert item["sha256"] == entry["sha256"]
            payload = (app.state.store.resolve_output(pid, item["name"])).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
            assert item["size_bytes"] == len(payload)
            text = (await client.get(item["download_url"])).text
            assert text.endswith("\n")


@pytest.mark.asyncio
async def test_legacy_language_names_and_original_fallback_remain_supported(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await create(client)
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = ["srt", "vtt"]
        config["export"]["bilingual"] = True
        config["export"]["naming"] = "language"
        assert (await client.put(f"/api/v1/projects/{pid}/config", json=config)).status_code == 200
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": True})
        await asyncio.gather(*app.state.jobs.tasks.values())
        items = (await client.get(f"/api/v1/projects/{pid}/outputs")).json()["items"]
        assert {item["name"] for item in items} == {
            "zh.srt",
            "ja-zh.srt",
            "zh.vtt",
            "ja-zh.vtt",
        }
        assert {item["name"]: item["bilingual"] for item in items} == {
            "zh.srt": False,
            "ja-zh.srt": True,
            "zh.vtt": False,
            "ja-zh.vtt": True,
        }

        config["export"]["naming"] = "original"
        assert (await client.put(f"/api/v1/projects/{pid}/config", json=config)).status_code == 200
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": False})
        await asyncio.gather(*app.state.jobs.tasks.values())
        items = (await client.get(f"/api/v1/projects/{pid}/outputs")).json()["items"]
        assert {item["name"] for item in items} == {
            "sample.srt",
            "sample-bilingual.srt",
            "sample.vtt",
            "sample-bilingual.vtt",
        }


@pytest.mark.asyncio
async def test_prune_is_manifest_driven_and_keeps_user_files(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await create(client)
        scope = app.state.store.scope(pid, "aud_primary")
        scope.save_output_text(pid, "user-notes.txt", "keep")
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = ["srt"]
        config["export"]["bilingual"] = False
        config["export"]["filename_template"] = "custom-{source_language}-{kind}-{format}"
        await client.put(f"/api/v1/projects/{pid}/config", json=config)
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": True})
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert (
            scope.resolve_output(pid, "user-notes.txt")
            if hasattr(scope, "resolve_output")
            else app.state.store.resolve_output(pid, "user-notes.txt")
        ).read_text() == "keep"
        assert (app.state.store.project_dir(pid) / "output/custom-ja-translated-srt.srt").is_file()

        config["export"]["filename_template"] = "renamed-{target_language}-{kind}-{format}"
        await client.put(f"/api/v1/projects/{pid}/config", json=config)
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": False})
        await asyncio.gather(*app.state.jobs.tasks.values())
        output = app.state.store.project_dir(pid) / "output"
        assert (output / "user-notes.txt").read_text() == "keep"
        assert (output / "renamed-zh-CN-translated-srt.srt").is_file()
        assert not (output / "custom-ja-translated-srt.srt").exists()
        manifest = json.loads((output / "manifest.json").read_text())
        assert [item["name"] for item in manifest["items"]] == ["renamed-zh-CN-translated-srt.srt"]


@pytest.mark.parametrize(
    ("template", "naming"),
    [
        ("../escape-{source_language}-{kind}-{format}", "language"),
        ("{unknown}-{source_language}-{kind}-{format}", "language"),
        ("bad{format", "language"),
        ("name.", "language"),
        ("name", "bad"),
    ],
)
def test_export_config_rejects_unsafe_templates_and_names(template, naming):
    payload = default_pipeline_config().model_dump(mode="json")
    payload["export"] = {
        **payload["export"],
        "filename_template": template,
        "naming": naming,
    }
    with pytest.raises(ValueError):
        PipelineConfig.model_validate(payload)
    valid = PipelineConfig.model_validate(
        {
            **default_pipeline_config().model_dump(mode="json"),
            "export": {
                **default_pipeline_config().model_dump(mode="json")["export"],
                "filename_template": "{source_language}-{kind}-{format}",
            },
        }
    )
    export_files(valid, source_stem="sample")


@pytest.mark.parametrize("name", ["CON", "CON.srt", "manifest.JSON"])
def test_export_filename_rejects_windows_devices_and_manifest_case_variants(name):
    with pytest.raises(ValueError):
        validate_export_filename(name)


def test_export_filename_rejects_utf8_over_file_name_limit():
    with pytest.raises(ValueError):
        validate_export_filename("a" * 252 + ".srt")


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("../../CON.wav", "audio-CON"),
        ("bad<name>?:.wav", "bad_name___"),
        ("...wav", "transcript"),
        ("my.audio.wav", "my.audio"),
        ("路径\会话.中文.wav", "会话.中文"),
        ("日本語" * 100 + ".wav", None),
    ],
)
def test_original_source_stem_is_sanitized(original, expected):
    payload = default_pipeline_config().model_dump(mode="json")
    payload["export"]["naming"] = "original"
    files = export_files(PipelineConfig.model_validate(payload), source_stem=original)
    if expected is None:
        assert files[0].name.startswith("日本語")
        assert len(files[0].name.encode("utf-8")) <= 255
    else:
        assert files[0].name == f"{expected}.srt"


@pytest.mark.asyncio
async def test_modified_managed_output_is_preserved_and_rejected(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await create(client)
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = ["srt"]
        config["export"]["bilingual"] = False
        await client.put(f"/api/v1/projects/{pid}/config", json=config)
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": True})
        await asyncio.gather(*app.state.jobs.tasks.values())
        output = app.state.store.project_dir(pid) / "output"
        managed = output / "zh.srt"
        managed.write_text("user edited subtitles" + chr(10), encoding="utf-8")
        before = managed.read_bytes()
        response = await client.post(f"/api/v1/projects/{pid}/run", json={"resume": False})
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        current = app.state.store.load_job(pid)
        assert current.status.value == "failed"
        assert managed.read_bytes() == before


@pytest.mark.asyncio
async def test_managed_output_symlink_is_never_unlinked_through_its_target(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = await create(client)
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = ["srt"]
        config["export"]["bilingual"] = False
        await client.put(f"/api/v1/projects/{pid}/config", json=config)
        await client.post(f"/api/v1/projects/{pid}/run", json={"resume": True})
        await asyncio.gather(*app.state.jobs.tasks.values())
        output = app.state.store.project_dir(pid) / "output"
        target = output / "user-notes.txt"
        target.write_text("keep" + chr(10), encoding="utf-8")
        managed = output / "zh.srt"
        managed.unlink()
        try:
            managed.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symlinks for this security test")
            raise
        response = await client.post(f"/api/v1/projects/{pid}/run", json={"resume": False})
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert target.read_text(encoding="utf-8") == "keep" + chr(10)
        assert managed.is_symlink()
        assert app.state.store.load_job(pid).status.value == "failed"


def test_empty_manifest_means_no_outputs_not_legacy_discovery(tmp_path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_empty_manifest",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    directory = store.create_project(project)
    (directory / "output" / "zh.srt").write_text("legacy", encoding="utf-8")
    (directory / "output" / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "items": []}), encoding="utf-8"
    )
    assert store.list_outputs(project.id) == []


@pytest.mark.parametrize(
    "manifest_kind", ["directory", "invalid_json", "symlink", "dangling_symlink"]
)
def test_present_invalid_manifest_is_rejected_instead_of_legacy_fallback(tmp_path, manifest_kind):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id=f"prj_invalid_manifest_{manifest_kind}",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    directory = store.create_project(project)
    output = directory / "output"
    (output / "zh.srt").write_text("legacy", encoding="utf-8")
    manifest = output / "manifest.json"
    if manifest_kind == "directory":
        manifest.mkdir()
    elif manifest_kind == "invalid_json":
        manifest.write_text("not json", encoding="utf-8")
    else:
        target = tmp_path / "missing-manifest-target.json"
        try:
            manifest.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symlinks for this validation test")
            raise
    assert store.has_export_manifest(project.id)
    with pytest.raises(ValueError, match="invalid export manifest"):
        store.load_export_manifest(project.id)
    with pytest.raises(ValueError, match="invalid export manifest"):
        store.list_outputs(project.id)
    with pytest.raises(ValueError, match="invalid export manifest"):
        store.save_export_outputs(project.id, {}, [])
    assert (output / "zh.srt").read_text(encoding="utf-8") == "legacy"


def test_developer_output_direct_path_is_not_resolved_through_symlink(tmp_path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_direct_path",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    directory = store.create_project(project)
    target = directory / "output" / "user.txt"
    target.write_text("keep", encoding="utf-8")
    linked = directory / "output" / "zh.srt"
    try:
        linked.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create symlinks for this security test")
        raise
    with pytest.raises(ValueError):
        store._direct_output_path(project.id, "zh.srt")
    assert target.read_text(encoding="utf-8") == "keep"
