import asyncio

import pytest
from app.main import create_app
from app.storage import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient

from test_workflow import tiny_wav


def graph(aid=None, text=False):
    nodes = [dict(id="input", type="audio_input", config={} if aid is None else {"audio_id": aid})]
    edges = []
    previous, port = "input", "audio"
    if text:
        nodes.append(dict(id="asr", type="asr", config={"provider": "mock", "language": "ja"}))
        edges.append(
            dict(
                id="to-asr", source="input", source_port="audio", target="asr", target_port="audio"
            )
        )
        previous, port = "asr", "transcript"
    nodes.append(dict(id="preview", type="text_preview" if text else "audio_preview", config={}))
    edges.append(
        dict(id="to-preview", source=previous, source_port=port, target="preview", target_port=port)
    )
    return dict(nodes=nodes, edges=edges)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [False, True])
async def test_empty_upload_binding_preview_persistence(tmp_path, text):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/api/v1/projects/empty",
            json=dict(name="empty", source_language="ja", target_language="zh"),
        )
        assert r.status_code == 201, r.text
        p = r.json()["project"]
        assert r.json()["job"] is None and p["source"] is None and p["audios"] == []
        root = f"/api/v1/projects/{p['id']}"
        assert (await c.get(root + "/audios")).json() == {"items": []}
        assert (await c.get(root + "/config")).status_code == 200
        assert (await c.get(root + "/job")).status_code == 404
        assert (await c.get("/api/v1/projects")).json()["total"] == 1
        assert (
            await c.put(root + "/workflow", json=dict(expected_revision=0, **graph()))
        ).status_code == 200
        uploads = []
        for _ in range(2):
            upload = await c.post(
                root + "/audios",
                files={"file": ("source.wav", tiny_wav(), "text/html")},
                data={"source_language": "ja"},
            )
            assert upload.status_code == 201, upload.text
            uploads.append(upload.json()["audio"]["id"])
        aid, other = uploads
        assert aid != other
        project = (await c.get(root)).json()
        assert project["source"] is None and len(project["audios"]) == 2
        source = await c.get(root + f"/audios/{aid}/source", headers={"Range": "bytes=0-3"})
        assert source.status_code == 206 and source.content == b"RIFF"
        assert source.headers["content-type"] == "audio/wav"
        assert source.headers["x-content-type-options"] == "nosniff"
        for invalid in ("../evil", "aud_foreign", "", None, [], {}):
            body = graph(invalid)
            if invalid is None:
                body["nodes"][0]["config"] = {"audio_id": None}
            validation = await c.post(root + "/workflow/validate", json=body)
            assert validation.status_code == 200 and not validation.json()["valid"]
            assert (
                await c.put(root + "/workflow", json=dict(expected_revision=0, **body))
            ).status_code == 422
        current = (await c.get(root + "/workflow")).json()
        saved = await c.put(
            root + "/workflow", json=dict(expected_revision=current["revision"], **graph(aid, text))
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["workflow_revision"]
        mismatch = await c.post(
            root + f"/audios/{other}/workflow-runs", json={"workflow_revision": revision}
        )
        assert (
            mismatch.status_code == 422
            and mismatch.json()["error"]["code"] == "INPUT_AUDIO_MISMATCH"
        )
        started = await c.post(
            root + f"/audios/{aid}/workflow-runs", json={"workflow_revision": revision}
        )
        assert started.status_code == 202, started.text
        run_url = root + f"/audios/{aid}/workflow-runs/{started.json()['id']}"
        for _ in range(100):
            run = (await c.get(run_url)).json()
            if run["status"] not in ("pending", "running"):
                break
            await asyncio.sleep(0.02)
        assert run["status"] == "completed", run
        preview = next(n for n in run["nodes"] if n["node_id"] == "preview")
        assert preview["preview"]["renderer"] == ("text" if text else "audio")
        content = await c.get(preview["preview"]["content_url"])
        assert content.status_code == 200
        assert content.content and content.headers["x-content-type-options"] == "nosniff"
        if not text:
            assert content.content == tiny_wav()
        store = FileSystemProjectStore(tmp_path)
        persisted = store.load_workflow_run(p["id"], aid, run["id"])
        assert persisted.nodes[-1].preview.content_url == preview["preview"]["content_url"]
        assert persisted.workflow.nodes[0].config == {"audio_id": aid}
        assert (await c.get(run_url + "/outputs/absent/content")).status_code == 404
        assert (await c.delete(root)).status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        dict(name="x", source_language="ja", target_language="JA"),
        dict(name="x", source_language="ja"),
        dict(name="x", source_language=" ", target_language="zh"),
    ],
)
async def test_empty_languages_required(tmp_path, body):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.post("/api/v1/projects/empty", json=body)).status_code == 422
        assert (await c.get("/api/v1/projects")).json()["total"] == 0


@pytest.mark.asyncio
async def test_preview_branch_survives_sibling_failure_and_graph_edits(tmp_path, monkeypatch):
    from dataclasses import replace

    from app.domain.node_registry import NODE_DEFINITIONS

    from test_workflow import create_project

    async def fail(context):
        raise ValueError("deliberate sibling failure")

    monkeypatch.setitem(
        NODE_DEFINITIONS, "audio_output", replace(NODE_DEFINITIONS["audio_output"], execute=fail)
    )
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aid = await create_project(c)
        root = f"/api/v1/projects/{pid}"
        body = graph()  # Legacy unbound {} keeps run.audio_id as its source.
        body["nodes"].append(dict(id="broken", type="audio_output", config={}))
        body["edges"].append(
            dict(
                id="to-broken",
                source="input",
                source_port="audio",
                target="broken",
                target_port="audio",
            )
        )
        saved = await c.put(root + "/workflow", json=dict(expected_revision=0, **body))
        assert saved.status_code == 200, saved.text
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
        assert run["status"] == "partial"
        preview = next(n for n in run["nodes"] if n["node_id"] == "preview")
        assert (await c.get(preview["preview"]["content_url"])).content == tiny_wav()
        edited = await c.put(
            root + "/workflow", json=dict(expected_revision=saved.json()["revision"], **graph(aid))
        )
        assert edited.status_code == 200
        assert (await c.get(run_url)).json()["workflow"]["nodes"][0]["config"] == {}
        path = app.state.store.workflow_artifact_path(
            pid, aid, run["id"], "preview", preview["artifact"]["filename"]
        )
        path.unlink()
        assert (await c.get(preview["preview"]["content_url"])).status_code == 404
        assert (await c.get(run_url)).json()["status"] == "partial"
        app.state.store.mark_interrupted()
        app.state.store.mark_workflow_runs_interrupted()
        assert (await c.get(run_url)).json()["status"] == "partial"


@pytest.mark.asyncio
async def test_foreign_audio_and_inline_source_path_boundaries(tmp_path):
    from test_workflow import create_project

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        first, aid = await create_project(c)
        second = (
            await c.post(
                "/api/v1/projects/empty",
                json=dict(name="second", source_language="ja", target_language="zh"),
            )
        ).json()["project"]["id"]
        root = f"/api/v1/projects/{second}"
        foreign = await c.post(root + "/workflow/validate", json=graph(aid))
        assert not foreign.json()["valid"]
        assert foreign.json()["errors"][0]["code"] == "INPUT_AUDIO_NOT_FOUND"
        assert (await c.get(root + f"/audios/{aid}/source")).status_code == 404
        project = app.state.store.load_project(first)
        project.source.path = "source/../project.json"
        app.state.store.save_project(project)
        assert (await c.get(f"/api/v1/projects/{first}/audios/{aid}/source")).status_code == 404
        assert (await c.delete(root)).status_code == 204


@pytest.mark.asyncio
async def test_empty_project_name_is_trimmed_and_blank_rejected(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = dict(name=" \t\n ", source_language="ja", target_language="zh")
        assert (await c.post("/api/v1/projects/empty", json=body)).status_code == 422
        assert (await c.get("/api/v1/projects")).json()["total"] == 0
        body["name"] = "  empty project  "
        response = await c.post("/api/v1/projects/empty", json=body)
        assert response.status_code == 201
        assert response.json()["project"]["name"] == "empty project"
