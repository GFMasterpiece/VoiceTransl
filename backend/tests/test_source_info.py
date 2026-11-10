"""Upload origin metadata and current-file parameter roots stay read-only."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from app.domain.node_execution import NodeExecutionContext
from app.domain.node_registry import NODE_DEFINITIONS, PORT_DEFINITIONS, validate_node_result
from app.domain.project import SourceAsset, SourceOrigin
from app.main import create_app
from app.nodes.source_info import execute
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from test_node_parameters import edge, run_graph
from test_workflow import create_project, save_graph, tiny_wav

ORIGIN = {"root_id": "root_a-123", "root_name": "音声素材", "relative_path": "章一/voice.wav"}


def form_origin(origin=None):
    return {f"origin_{key}": value for key, value in (origin or ORIGIN).items()}


async def upload(client, pid, origin=None, **data):
    return await client.post(
        f"/api/v1/projects/{pid}/audios",
        files={"file": ("voice.wav", tiny_wav(), "audio/wav")},
        data={"source_language": "en-US", **(origin or {}), **data},
    )


@pytest.mark.asyncio
async def test_origin_roundtrip_same_names_distinct_roots_and_legacy_upload(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, primary_id = await create_project(client)
        primary = app.state.store.audio(pid, primary_id)
        primary_bytes = app.state.store.source_path(pid, primary.source.path).read_bytes()
        first = await upload(client, pid, form_origin(), name="自定义展示名称")
        second = await upload(
            client, pid, form_origin({**ORIGIN, "root_id": "root_b", "root_name": "第二目录"})
        )
        legacy = await upload(client, pid, name="existing-contract")
        assert first.status_code == second.status_code == legacy.status_code == 201
        audio = first.json()["audio"]
        assert audio["source"]["origin"] == ORIGIN
        assert audio["source"]["language"] == "en-US"
        assert audio["name"] == "自定义展示名称"
        assert legacy.json()["audio"]["source"]["origin"] is None
        assert audio["id"] != second.json()["audio"]["id"]
        assert audio["source"]["path"] == f"audios/{audio['id']}/source/original.wav"
        assert app.state.store.source_path(pid, primary.source.path).read_bytes() == primary_bytes
    restarted = create_app(tmp_path)
    assert restarted.state.store.audio(pid, audio["id"]).source.origin.model_dump() == ORIGIN


@pytest.mark.parametrize(
    "key,value",
    [
        ("root_id", "../root"),
        ("root_id", "a:b"),
        ("root_id", "root with space"),
        ("root_id", "a" * 129),
        ("root_name", ".."),
        ("root_name", "C:"),
        ("root_name", "nested/root"),
        ("root_name", "CON"),
        ("root_name", "root\u0085"),
        ("root_name", "root\nname"),
        ("root_name", "root."),
        ("relative_path", "/voice.wav"),
        ("relative_path", "//server/voice.wav"),
        ("relative_path", "../voice.wav"),
        ("relative_path", "folder/../voice.wav"),
        ("relative_path", "folder/./voice.wav"),
        ("relative_path", "folder//voice.wav"),
        ("relative_path", "folder/voice.wav/"),
        ("relative_path", "folder\\voice.wav"),
        ("relative_path", "C:/voice.wav"),
        ("relative_path", "C:voice.wav"),
        ("relative_path", "folder:stream/voice.wav"),
        ("relative_path", "folder\x00/voice.wav"),
        ("relative_path", "NUL/voice.wav"),
        ("relative_path", "folder?/voice.wav"),
    ],
)
def test_origin_rejects_unsafe_metadata(key, value):
    with pytest.raises(ValidationError):
        SourceOrigin.model_validate({**ORIGIN, key: value})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        {"origin_root_id": "root"},
        {"origin_root_name": "root", "origin_relative_path": "voice.wav"},
        {"origin_root_id": "", "origin_root_name": "", "origin_relative_path": ""},
        {**form_origin(), "origin_relative_path": "../voice.wav"},
        {**form_origin(), "origin_relative_path": "folder/other.wav"},
        {**form_origin(), "origin_root_id": "unsafe/root"},
    ],
)
async def test_invalid_origin_upload_does_not_publish_or_change_resources(tmp_path, origin):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        project_path = app.state.store.project_dir(pid) / "project.json"
        original_project = project_path.read_bytes()
        original_source = (await client.get(f"/api/v1/projects/{pid}/audios/{aid}/source")).content
        response = await upload(client, pid, origin)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert project_path.read_bytes() == original_project
        assert len(app.state.store.load_project(pid).audios) == 1
        assert not list((project_path.parent / "audios").glob("*"))
        assert (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/source")
        ).content == original_source


@pytest.mark.asyncio
async def test_failed_oversize_origin_upload_leaves_existing_resources_intact(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        project_path = app.state.store.project_dir(pid) / "project.json"
        original = project_path.read_bytes()
        source_url = f"/api/v1/projects/{pid}/audios/{aid}/source"
        source = (await client.get(source_url)).content
        app.state.settings.max_upload_mb = 0
        response = await upload(client, pid, form_origin())
        assert response.status_code == 413, response.text
        assert project_path.read_bytes() == original
        assert not list((project_path.parent / "audios").glob("*"))
        assert (await client.get(source_url)).content == source


@pytest.mark.asyncio
async def test_unicode_origin_roundtrip_requires_explicit_language(tmp_path):
    app = create_app(tmp_path)
    filename = "音声 第１章.録音.wav"
    origin = {**ORIGIN, "relative_path": f"第１章/小節 二/{filename}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        files = {"file": (filename, tiny_wav(), "audio/wav")}
        response = await client.post(
            f"/api/v1/projects/{pid}/audios", files=files, data=form_origin(origin)
        )
        assert response.status_code == 422
        assert len(app.state.store.load_project(pid).audios) == 1
        response = await client.post(
            f"/api/v1/projects/{pid}/audios",
            files=files,
            data={**form_origin(origin), "source_language": "zh-Hant"},
        )
        assert response.status_code == 201, response.text
        source = response.json()["audio"]["source"]
        assert source["original_name"] == filename
        assert source["origin"] == origin
        assert source["language"] == "zh-Hant"


@pytest.mark.asyncio
async def test_old_project_source_is_readable_without_rewriting_files(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
    project_path = app.state.store.project_dir(pid) / "project.json"
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    payload["source"].pop("origin")
    payload.pop("audios")
    project_path.write_text(json.dumps(payload), encoding="utf-8")
    legacy_bytes = project_path.read_bytes()
    restarted = create_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        project = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert project["source"]["origin"] is None
        assert project["audios"][0]["source"]["origin"] is None
        assert (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/source")
        ).content == tiny_wav()
    assert project_path.read_bytes() == legacy_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("with_origin", [False, True])
async def test_source_info_reads_current_audio_without_filesystem_access(with_origin):
    source = SourceAsset(
        path="audios/target/source/original.wav",
        original_name="voice.wav",
        size_bytes=0,
        language="explicit-language",
        origin=SourceOrigin.model_validate(ORIGIN) if with_origin else None,
    )
    context = NodeExecutionContext(
        store=object(),
        run=SimpleNamespace(audio_id="target"),
        node=SimpleNamespace(config={}),
        inputs={},
        project=SimpleNamespace(
            audios=[
                SimpleNamespace(id="other", source=None),
                SimpleNamespace(id="target", source=source),
            ]
        ),
        cancel_event=asyncio.Event(),
    )
    result = validate_node_result(NODE_DEFINITIONS["source_info"], await execute(context))
    assert {key: item.value for key, item in result.outputs.items()} == {
        "filename": "voice.wav",
        "stem": "voice",
        "source_language": "explicit-language",
        "relative_path": ORIGIN["relative_path"] if with_origin else "voice.wav",
        "root_name": ORIGIN["root_name"] if with_origin else "",
    }
    assert all(PORT_DEFINITIONS[key].type_id == "string" for key in result.outputs)
    with pytest.raises(ValueError):
        NODE_DEFINITIONS["source_info"].normalize_config({"path": "C:/local.wav"})


@pytest.mark.asyncio
async def test_source_info_parameter_naming_records_and_immutable_history(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, primary_id = await create_project(client)
        added = await upload(client, pid, form_origin())
        aid = added.json()["audio"]["id"]
        graph = {
            "nodes": [
                dict(id="input", type="audio_input", config={}),
                dict(id="info", type="source_info", config={}),
                dict(id="info2", type="source_info", config={}),
                dict(
                    id="output",
                    type="audio_output",
                    config={"filename_stem": "manual", "naming_mode": "exact"},
                ),
                dict(id="asr", type="asr", config={}),
                dict(
                    id="context",
                    type="text_join",
                    config={"items": [{"id": "a1", "value": "ja"}]},
                ),
                dict(id="text", type="text_output", config={}),
            ],
            "edges": [
                edge("audio", "input", "audio", "output", "audio"),
                edge("stem", "info", "stem", "output", "filename_stem"),
                edge("audio-asr", "input", "audio", "asr", "audio"),
                edge("language", "info2", "source_language", "context", "text_a1"),
                edge("transcript", "asr", "transcript", "text", "transcript"),
                edge("context", "context", "string", "text", "filename_stem"),
            ],
        }
        document = await save_graph(client, pid, 0, graph)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed", run
        assert records["info"]["status"] == records["info2"]["status"] == "completed"
        assert records["info"]["effective_config"] == {}
        assert records["output"]["artifact"]["filename"] == "voice.wav"
        assert records["output"]["effective_config"]["filename_stem"] == "voice"
        assert records["output"]["config"]["filename_stem"] == "manual"
        assert records["output"]["parameter_sources"]["filename_stem"] == {
            "kind": "connection",
            "edge_id": "stem",
            "source_node_id": "info",
            "source_port": "stem",
            "target_port": "filename_stem",
        }
        assert records["context"]["effective_config"]["items"][0]["value"] == "en-US"
        run_url = f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}"
        history = (await client.get(run_url)).content
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "info"]
        graph["edges"] = [item for item in graph["edges"] if item["id"] != "stem"]
        graph["nodes"][-1]["config"] = {"filename_stem": "new-text"}
        await save_graph(client, pid, document["revision"], graph)
        assert (await client.get(run_url)).content == history
        assert app.state.store.audio(pid, primary_id).source.original_name == "source.wav"
        assert (
            await client.get(records["output"]["artifact"]["download_url"])
        ).content == tiny_wav()
    restarted = create_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        assert (await client.get(run_url)).content == history


def test_primary_normalization_keeps_origin_and_rejects_mismatched_basename():
    from app.domain.project import Project

    source = SourceAsset(
        path="source/original.wav", original_name="voice.wav", size_bytes=0, origin=ORIGIN
    )
    project = Project(id="legacy", name="legacy", source=source)
    assert project.audios[0].source.origin.model_dump() == ORIGIN
    with pytest.raises(ValidationError, match="basename"):
        SourceAsset(
            path="source/original.wav", original_name="different.wav", size_bytes=0, origin=ORIGIN
        )
