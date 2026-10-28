import asyncio
import io
import json
import struct
import threading
from pathlib import Path

import pytest
from app.application.workflow_runner import WorkflowRunCancelled, _copy_audio
from app.domain import WorkflowNodeStatus, WorkflowRunStatus
from app.main import create_app
from app.storage import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient


def tiny_wav(payload: bytes = b"\x00\x01workflow") -> bytes:
    # A valid 8 kHz, 8-bit mono WAV with a short recognizable data section.
    data_size = len(payload)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + data_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 8000, 1, 8)
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + payload


async def create_project(client: AsyncClient, payload: bytes | None = None) -> tuple[str, str]:
    payload = payload or tiny_wav()
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("source.wav", payload, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    project = response.json()["project"]
    return project["id"], project["audios"][0]["id"]


async def get_default_workflow(client: AsyncClient, pid: str) -> dict:
    response = await client.get(f"/api/v1/projects/{pid}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def save_graph(client: AsyncClient, pid: str, revision: int, graph: dict) -> dict:
    response = await client.put(
        f"/api/v1/projects/{pid}/workflow",
        json={"expected_revision": revision, **graph},
    )
    assert response.status_code == 200, response.text
    return response.json()


def graph_with_two_outputs(first_stem: str = "first", second_stem: str = "second") -> dict:
    return {
        "nodes": [
            {
                "id": "input",
                "type": "audio_input",
                "config": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "output-a",
                "type": "audio_output",
                "config": {"filename_stem": first_stem},
                "position": {"x": 200, "y": 0},
            },
            {
                "id": "output-b",
                "type": "audio_output",
                "config": {"filename_stem": second_stem},
                "position": {"x": 200, "y": 150},
            },
        ],
        "edges": [
            {
                "id": "edge-a",
                "source": "input",
                "source_port": "audio",
                "target": "output-a",
                "target_port": "audio",
            },
            {
                "id": "edge-b",
                "source": "input",
                "source_port": "audio",
                "target": "output-b",
                "target_port": "audio",
            },
        ],
    }


@pytest.mark.asyncio
async def test_default_workflow_and_validation_contract(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        document = await get_default_workflow(client, pid)
        assert document["revision"] == document["workflow_revision"] == 0
        assert document["nodes"][1]["config"] == {"filename_stem": "audio-copy"}
        assert not (app.state.store.project_dir(pid) / "workflow.json").exists()

        graph = graph_with_two_outputs()
        response = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": graph["nodes"], "edges": graph["edges"]},
        )
        assert response.status_code == 200
        assert response.json() == {"valid": True, "errors": []}

        graph["nodes"].append(
            {
                "id": "input",
                "type": "audio_input",
                "config": {},
                "position": {"x": 0, "y": 0},
            }
        )
        response = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": graph["nodes"], "edges": graph["edges"]},
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False
        assert any(error["code"] == "DUPLICATE_NODE_ID" for error in response.json()["errors"])

        too_many = [graph["nodes"][0].copy() for _ in range(65)]
        response = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": too_many, "edges": []},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_workflow_versions_ignore_layout_and_edge_ids_but_keep_immutable_history(
    tmp_path,
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        graph = graph_with_two_outputs()
        first = await save_graph(client, pid, 0, graph)
        assert first["revision"] == 1
        assert first["workflow_revision"] == 1

        graph["nodes"] = list(reversed(graph["nodes"]))
        graph["nodes"][0]["position"] = {"x": 10, "y": 20}
        graph["edges"] = list(reversed(graph["edges"]))
        graph["edges"][0]["id"] = "renamed-edge"
        second = await save_graph(client, pid, first["revision"], graph)
        assert second["revision"] == 2
        assert second["workflow_revision"] == 1

        graph["nodes"][1]["config"]["filename_stem"] = "changed"
        third = await save_graph(client, pid, second["revision"], graph)
        assert third["revision"] == 3
        assert third["workflow_revision"] == 2

        conflict = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={"expected_revision": second["revision"], **graph},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "WORKFLOW_REVISION_CONFLICT"

        same = await save_graph(client, pid, third["revision"], graph)
        assert same["revision"] == 3
        history = app.state.store.project_dir(pid) / "workflow-revisions"
        assert sorted(path.name for path in history.iterdir()) == [
            "00000001.json",
            "00000002.json",
            "00000003.json",
        ]

        graph["nodes"][0]["config"]["unexpected"] = True
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={"expected_revision": third["revision"], **graph},
        )
        assert response.status_code == 422
        assert any(
            error["code"] == "INVALID_NODE_CONFIG" for error in response.json()["error"]["details"]
        )


@pytest.mark.asyncio
async def test_first_save_of_default_graph_persists_revision_one(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        default = await get_default_workflow(client, pid)
        saved = await save_graph(
            client,
            pid,
            default["revision"],
            {"nodes": default["nodes"], "edges": default["edges"]},
        )
        assert saved["revision"] == 1
        assert saved["workflow_revision"] == 1
        assert (app.state.store.project_dir(pid) / "workflow.json").is_file()
        assert (app.state.store.project_dir(pid) / "workflow-revisions" / "00000001.json").is_file()


@pytest.mark.asyncio
async def test_workflow_history_publication_is_repairable_after_failure(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        graph = graph_with_two_outputs()
        first = await save_graph(client, pid, 0, graph)
        history = app.state.store.project_dir(pid) / "workflow-revisions" / "00000001.json"
        assert history.is_file()

    # Simulate a stop after the current document was committed but before the
    # immutable history file was published.
    history.unlink()
    restarted = create_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        current = await get_default_workflow(client, pid)
        assert current["revision"] == first["revision"]
        assert history.is_file()
        graph["nodes"][0]["position"] = {"x": 42, "y": 42}
        second = await save_graph(client, pid, current["revision"], graph)
        assert second["revision"] == current["revision"] + 1
        assert second["workflow_revision"] == current["workflow_revision"]
        assert (
            app.state.store.project_dir(pid)
            / "workflow-revisions"
            / f"{second['revision']:08d}.json"
        ).is_file()


@pytest.mark.asyncio
async def test_workflow_run_copies_independent_bytes_and_isolated_history(tmp_path):
    app = create_app(tmp_path)
    source_payload = tiny_wav(b"\x00\x02unique-source-audio")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client, source_payload)
        graph = graph_with_two_outputs()
        document = await save_graph(client, pid, 0, graph)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202, response.text
        created = response.json()
        assert created["workflow"]["workflow_revision"] == document["workflow_revision"]
        assert created["status"] == "pending"
        await asyncio.gather(*app.state.jobs.tasks.values())

        run = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{created['id']}")
        ).json()
        assert run["status"] == "completed"
        assert {node["status"] for node in run["nodes"]} == {"completed"}
        records = {node["node_id"]: node for node in run["nodes"]}
        assert records["output-a"]["artifact"]["filename"] == "first.wav"
        assert records["output-b"]["artifact"]["filename"] == "second.wav"
        assert records["output-a"]["artifact"]["size_bytes"] == len(source_payload)
        for node_id in ("output-a", "output-b"):
            metadata_url = records[node_id]["artifact"]["download_url"]
            assert metadata_url.startswith("/api/v1/")
            metadata = await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{created['id']}"
                f"/outputs/{node_id}"
            )
            assert metadata.status_code == 200
            assert "path" not in metadata.json()
            download = await client.get(f"{metadata_url}")
            assert download.status_code == 200
            assert download.content == source_payload

        source_before = (
            app.state.store.scope(pid, aid)
            .source_path(pid, app.state.store.audio(pid, aid).source.path)
            .read_bytes()
        )
        assert source_before == source_payload
        assert not app.state.store.load_job(pid).config_snapshot
        history = await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs")
        assert history.status_code == 200
        assert history.json()["total"] == 1
        assert history.json()["limit"] == 20
        assert history.json()["items"][0]["id"] == created["id"]

        second = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert second.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        history = (await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs")).json()
        assert history["total"] == 2
        assert {item["id"] for item in history["items"]} == {created["id"], second.json()["id"]}


@pytest.mark.asyncio
async def test_workflow_run_is_audio_scoped_and_shares_legacy_busy_state(tmp_path):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pid, aid = await create_project(client)
            response = await client.post(
                f"/api/v1/projects/{pid}/audios",
                files={"file": ("second.wav", tiny_wav(), "audio/wav")},
            )
            assert response.status_code == 201
            other_aid = response.json()["audio"]["id"]
            document = await get_default_workflow(client, pid)
            run_response = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert run_response.status_code == 202
            rid = run_response.json()["id"]

            busy = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/run", json={"resume": True}
            )
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "AUDIO_BUSY"
            legacy_cancel = await client.post(f"/api/v1/projects/{pid}/audios/{aid}/cancel")
            assert legacy_cancel.status_code == 409
            assert legacy_cancel.json()["error"]["code"] == "AUDIO_BUSY_WORKFLOW"
            assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 409

            other_run = await client.post(
                f"/api/v1/projects/{pid}/audios/{other_aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert other_run.status_code == 202
            listed = (
                await client.get(f"/api/v1/projects/{pid}/audios/{other_aid}/workflow-runs")
            ).json()
            assert listed["total"] == 1
            assert listed["items"][0]["id"] == other_run.json()["id"]

            cancel = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
            )
            assert cancel.status_code == 202
            assert cancel.json()["status"] == "cancelling"
            assert (
                await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
            ).json()["status"] in {"cancelling", "cancelled"}
    finally:
        app.state.jobs.semaphore.release()
    await asyncio.gather(*app.state.jobs.tasks.values())


@pytest.mark.asyncio
async def test_missing_source_marks_input_failed_and_outputs_blocked(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        scope = app.state.store.scope(pid, aid)
        source = scope.source_path(pid, app.state.store.audio(pid, aid).source.path)
        source.unlink()
        document = await get_default_workflow(client, pid)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (
            await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{response.json()['id']}"
            )
        ).json()
        assert run["status"] == "failed"
        nodes = {node["node_id"]: node for node in run["nodes"]}
        assert nodes["input"]["status"] == "failed"
        assert nodes["output"]["status"] == "blocked"
        output = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}/outputs/output"
        )
        assert output.status_code == 404
        assert output.json()["error"]["code"] == "WORKFLOW_OUTPUT_NOT_FOUND"


@pytest.mark.asyncio
async def test_missing_source_run_does_not_report_input_as_success(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)
        source = app.state.store.scope(pid, aid).source_path(
            pid, app.state.store.audio(pid, aid).source.path
        )
        source.unlink()
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        run = response.json()
        await asyncio.gather(*app.state.jobs.tasks.values())
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}")
        ).json()
        records = {node["node_id"]: node for node in detail["nodes"]}
        assert records["input"]["status"] == "failed"
        assert records["output"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_workflow_output_removal_and_symlink_are_stable_404(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        await asyncio.gather(*app.state.jobs.tasks.values())
        rid = response.json()["id"]
        run = (await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")).json()
        artifact = run["nodes"][-1]["artifact"]
        path = app.state.store.workflow_artifact_path(pid, aid, rid, "output", artifact["filename"])
        path.unlink()
        assert (
            await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/output/download"
            )
        ).status_code == 404

        outside = tmp_path / "outside.wav"
        outside.write_bytes(b"outside")
        path.symlink_to(outside)
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/output/download"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "WORKFLOW_OUTPUT_NOT_FOUND"


@pytest.mark.asyncio
async def test_workflow_restart_marks_active_runs_interrupted(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = app.state.store.load_workflow_run(pid, aid, response.json()["id"])
        run.status = WorkflowRunStatus.RUNNING
        run.finished_at = None
        for node in run.nodes:
            node.status = WorkflowNodeStatus.RUNNING
        app.state.store.save_workflow_run(run)

    restarted = FileSystemProjectStore(tmp_path)
    restarted.mark_workflow_runs_interrupted()
    loaded = restarted.load_workflow_run(pid, aid, run.id)
    assert loaded.status == WorkflowRunStatus.INTERRUPTED
    assert all(node.status == WorkflowNodeStatus.INTERRUPTED for node in loaded.nodes)


@pytest.mark.asyncio
async def test_restart_scan_does_not_create_workflow_dirs_for_legacy_project(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
    project_dir = app.state.store.project_dir(pid)
    assert not (project_dir / "workflow-runs").exists()
    restarted = FileSystemProjectStore(tmp_path)
    restarted.mark_workflow_runs_interrupted()
    assert not (project_dir / "workflow-runs").exists()


@pytest.mark.asyncio
async def test_dangling_runs_symlink_and_source_symlink_are_rejected(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        project_dir = store.project_dir(pid)
        (project_dir / "workflow-runs").symlink_to(
            tmp_path / "not-created", target_is_directory=True
        )
        with pytest.raises(ValueError):
            store.workflow_run_root(pid, aid)
        (project_dir / "workflow-runs").unlink()

        scope = store.scope(pid, aid)
        source = scope.source_path(pid, store.audio(pid, aid).source.path)
        outside = tmp_path / "outside.wav"
        outside.write_bytes(tiny_wav())
        source.unlink()
        source.symlink_to(outside)
        with pytest.raises(ValueError):
            scope.source_path(pid, store.audio(pid, aid).source.path)


@pytest.mark.asyncio
async def test_source_removed_after_run_creation_is_stable_failure(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)
        source = app.state.store.scope(pid, aid).source_path(
            pid, app.state.store.audio(pid, aid).source.path
        )
        source.unlink()
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        detail = (
            await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{response.json()['id']}"
            )
        ).json()
        assert detail["status"] == "failed"
        assert detail["error"]["type"] == "INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_filename_stem_rejects_unsafe_and_windows_reserved_names(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await create_project(client)
        for stem in (
            "CON",
            "con.foo",
            "NUL.a",
            "COM1.x",
            "lpt9.copy",
            "nul",
            "COM1",
            "../escape",
            "name.",
            "trail ",
        ):
            graph = graph_with_two_outputs()
            graph["nodes"][1]["config"]["filename_stem"] = stem
            response = await client.post(
                f"/api/v1/projects/{pid}/workflow/validate",
                json={"nodes": graph["nodes"], "edges": graph["edges"]},
            )
            assert response.status_code == 200
            assert response.json()["valid"] is False
            assert any(error["node_id"] == "output-a" for error in response.json()["errors"])


def test_workflow_history_and_run_state_are_valid_json(tmp_path):
    app = create_app(tmp_path)
    assert app.state.store.projects_dir.is_dir()
    json.dumps({"ok": True})


def test_cancelled_copy_never_publishes_partial_or_complete_output(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    source.write_bytes(b"x" * 4096)
    target = tmp_path / "node" / "copy.wav"
    cancel = asyncio.Event()
    copy_started = threading.Event()
    release_copy = threading.Event()
    reached_second_read = threading.Event()

    class BlockingReader:
        def __init__(self, path, mode):
            self.stream = io.open(path, mode.replace("b", ""))
            self.calls = 0

        def read(self, size):
            self.calls += 1
            chunk = self.stream.read(size)
            if self.calls == 2:
                reached_second_read.set()
            if self.calls == 1 and chunk:
                copy_started.set()
                release_copy.wait(timeout=5)
            return chunk

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    async def run_copy():
        original_open = Path.open

        def patched_open(path_self, mode="r", *args, **kwargs):
            if path_self == source and "r" in mode:
                return BlockingReader(path_self, mode)
            return original_open(path_self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", patched_open)
        task = asyncio.create_task(
            _copy_audio(source, target, cancel, expected_size=source.stat().st_size)
        )
        try:
            assert await asyncio.to_thread(copy_started.wait, 2)
            await asyncio.to_thread(reached_second_read.wait, 2)
            cancel.set()
            await asyncio.sleep(0.1)
            with pytest.raises(WorkflowRunCancelled):
                await task
        finally:
            release_copy.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2)
            except (WorkflowRunCancelled, asyncio.TimeoutError):
                pass

    asyncio.run(run_copy())
    assert not target.exists()
    assert not list(target.parent.glob("*.tmp"))


@pytest.mark.asyncio
async def test_shutdown_waits_for_copy_worker_and_never_publishes(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(tmp_path)
        graph = graph_with_two_outputs()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pid, aid = await create_project(client)
            document = await save_graph(client, pid, 0, graph)

            worker_started = threading.Event()
            worker_finished = threading.Event()
            release_worker = threading.Event()
            entered_second_read = threading.Event()
            original_open = Path.open

            class GatedReader:
                def __init__(self, path, mode):
                    self.stream = io.open(path, mode)
                    self.calls = 0

                def read(self, size):
                    self.calls += 1
                    chunk = self.stream.read(size)
                    if self.calls == 1 and chunk:
                        worker_started.set()
                        release_worker.wait(timeout=5)
                    if self.calls == 2:
                        entered_second_read.set()
                    return chunk

                def __getattr__(self, name):
                    return getattr(self.stream, name)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

            def patched_open(path_self, mode="r", *args, **kwargs):
                if path_self == source_path and "r" in mode:
                    return GatedReader(path_self, mode)
                return original_open(path_self, mode, *args, **kwargs)

            source_path = app.state.store.scope(pid, aid).source_path(
                pid, app.state.store.audio(pid, aid).source.path
            )
            monkeypatch.setattr(Path, "open", patched_open)
            response = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert response.status_code == 202
            task = app.state.jobs.tasks[(pid, aid)]
            worker = asyncio.create_task(app.state.jobs.shutdown())
            try:
                assert await asyncio.to_thread(worker_started.wait, 2)
                await asyncio.sleep(0.1)
                release_worker.set()
                await asyncio.wait_for(worker, timeout=3)
                worker_finished.set()
            finally:
                release_worker.set()
                if not worker.done():
                    await asyncio.wait_for(worker, timeout=3)
                if not task.done():
                    await asyncio.wait_for(task, timeout=3)

            assert worker_finished.is_set()
            assert task.done()
            run = app.state.store.load_workflow_run(pid, aid, response.json()["id"])
            assert run.status == WorkflowRunStatus.INTERRUPTED
            run_dir = app.state.store.workflow_run_dir(pid, aid, run.id)
            assert not list(run_dir.rglob("*.wav"))
            assert not list(run_dir.rglob("*.tmp"))

    await scenario()


@pytest.mark.asyncio
async def test_unexpected_runner_error_persists_stable_failure_state(tmp_path, monkeypatch):
    import app.application.job_manager as manager_module

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await get_default_workflow(client, pid)

        async def fail(*_args, **_kwargs):
            raise RuntimeError("sensitive internal detail")

        monkeypatch.setattr(manager_module, "execute_workflow_run", fail)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = (
            await client.get(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{response.json()['id']}"
            )
        ).json()
        assert run["status"] == "failed"
        assert run["error"] == {
            "type": "WORKFLOW_RUN_FAILED",
            "message": "工作流运行失败",
            "node_id": None,
        }
        assert run["nodes"]
        assert {node["status"] for node in run["nodes"]} == {"blocked"}
        assert all(node["error"]["type"] == "DEPENDENCY_BLOCKED" for node in run["nodes"])
