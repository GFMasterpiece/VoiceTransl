import asyncio
import json
from dataclasses import replace

import pytest
from app.domain.node_registry import NODE_DEFINITIONS
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import setup
from test_inline_credentials import cloud as cloud
from test_inline_credentials import graph, run, save, update_body


async def cloud_graph(client, app, count=1):
    pid, aids, document = await setup(client, count)
    document = await save(client, pid, graph(document))
    document = await save(client, pid, update_body(document))
    completed = await run(client, app, pid, aids[0], document)
    assert completed.status.value == "completed"
    return pid, aids, document, completed


def rerun_body(document, source, node="output", request="rerun"):
    return {
        "workflow_revision": document["workflow_revision"],
        "request_id": request,
        "rerun": {"source_run_id": source.id, "from_node_id": node},
    }


async def submit(client, app, pid, aid, body):
    response = await client.post(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs", json=body)
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.jobs.tasks.values())
    return app.state.store.load_workflow_run(pid, aid, response.json()["id"])


async def plan(client, pid, aid, body):
    response = await client.post(
        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/plan", json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_output_rerun_uses_new_saved_config_and_copies_results_without_cloud(tmp_path, cloud):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        old_bytes = (
            app.state.store.workflow_run_dir(pid, aids[0], source.id) / "run.json"
        ).read_bytes()
        for node in document["nodes"]:
            if node["id"] == "output":
                node["config"]["naming_mode"] = "prefix_sequence"
                node["config"]["filename_stem"] = "changed"
        # An unrelated branch has no key. It is valid graph data but not executed.
        document["nodes"].extend(
            [
                {"id": "other_cloud", "type": "qwen_asr", "config": {}},
                {"id": "other_out", "type": "text_output", "config": {"format": "txt"}},
            ]
        )
        document["edges"].extend(
            [
                {
                    "id": "x",
                    "source": "input",
                    "source_port": "audio",
                    "target": "other_cloud",
                    "target_port": "audio",
                },
                {
                    "id": "y",
                    "source": "other_cloud",
                    "source_port": "transcript",
                    "target": "other_out",
                    "target_port": "transcript",
                },
            ]
        )
        document = await save(
            client,
            pid,
            {
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        body = rerun_body(document, source)
        preview = await plan(client, pid, aids[0], body)
        assert preview["valid"], preview
        assert preview["execute_node_ids"] == ["output"]
        assert set(preview["reuse_node_ids"]) == {"input", "cloud"}
        result = await submit(client, app, pid, aids[0], body)
        assert result.status.value == "completed", result.error
        assert len(cloud["calls"]) == 1
        records = {n.node_id: n for n in result.nodes}
        assert records["cloud"].execution_kind == "reused"
        assert (
            records["cloud"].artifact.download_url
            != next(n for n in source.nodes if n.node_id == "cloud").artifact.download_url
        )
        assert records["output"].artifact.filename == "changed_00001.txt"
        assert records["other_cloud"].status.value == "skipped"
        assert records["other_cloud"].artifact is None
        assert (
            app.state.store.workflow_run_dir(pid, aids[0], source.id) / "run.json"
        ).read_bytes() == old_bytes
        assert result.rerun.source_run_id == source.id
        # Replay must succeed before revision/busy checks and return this same run.
        replay = await submit(client, app, pid, aids[0], body)
        assert replay.id == result.id
        conflict = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={**body, "rerun": {**body["rerun"], "from_node_id": "cloud"}},
        )
        assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_changed_ancestor_config_implementation_and_same_size_source_rejected(
    tmp_path, cloud, monkeypatch
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        body = rerun_body(document, source)
        old_definition = NODE_DEFINITIONS["qwen_asr"]
        monkeypatch.setitem(
            NODE_DEFINITIONS, "qwen_asr", replace(old_definition, execution_version="changed")
        )
        preview = await plan(client, pid, aids[0], body)
        assert preview["issues"][0]["code"] == "RERUN_UPSTREAM_CHANGED"
        monkeypatch.setitem(NODE_DEFINITIONS, "qwen_asr", old_definition)
        for node in document["nodes"]:
            if node["id"] == "cloud":
                node["config"]["model"] = "other-compatible-model"
        edited = await save(
            client,
            pid,
            {
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        preview = await plan(client, pid, aids[0], rerun_body(edited, source))
        assert preview["issues"][0]["code"] == "RERUN_UPSTREAM_CHANGED"
        # Restore config so the next rejection specifically exercises source bytes.
        original = next(n for n in source.workflow.nodes if n.id == "cloud").config
        next(n for n in edited["nodes"] if n["id"] == "cloud")["config"] = original
        edited = await save(
            client,
            pid,
            {
                "expected_revision": edited["revision"],
                "nodes": edited["nodes"],
                "edges": edited["edges"],
            },
        )
        path = (
            app.state.store.project_dir(pid)
            / app.state.store.load_project(pid).audios[0].source.path
        )
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        preview = await plan(client, pid, aids[0], rerun_body(edited, source))
        assert preview["issues"][0]["code"] == "RERUN_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_old_run_read_unchanged_and_no_checkpoint_refused(tmp_path, cloud):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        path = app.state.store.workflow_run_dir(pid, aids[0], source.id) / "run.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        for field in ("rerun", "source_identity", "request_id", "request_digest"):
            legacy.pop(field, None)
        for node in legacy["nodes"]:
            for field in ("checkpoint", "execution_kind", "reused_from"):
                node.pop(field, None)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before = path.read_bytes()
        preview = await plan(client, pid, aids[0], rerun_body(document, source))
        assert preview["issues"][0]["code"] == "RERUN_CHECKPOINT_UNAVAILABLE"
        assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_parameter_descendants_reuse_side_ancestor_and_changed_parameter_rejected(
    tmp_path, cloud
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        body["nodes"].extend(
            [
                {"id": "lang", "type": "string_value", "config": {"value": "ja"}},
                {"id": "name", "type": "source_info", "config": {}},
            ]
        )
        body["edges"].extend(
            [
                {
                    "id": "lang-edge",
                    "source": "lang",
                    "source_port": "string",
                    "target": "cloud",
                    "target_port": "language",
                },
                {
                    "id": "name-edge",
                    "source": "name",
                    "source_port": "stem",
                    "target": "output",
                    "target_port": "filename_stem",
                },
            ]
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        source = await run(client, app, pid, aids[0], document)
        assert source.status.value == "completed"
        # A parameter selected for execution pulls in cloud and output, while
        # source_info is an independent necessary ancestor at the merge.
        preview = await plan(client, pid, aids[0], rerun_body(document, source, "lang"))
        assert preview["valid"], preview
        assert set(preview["execute_node_ids"]) == {"lang", "cloud", "output"}
        assert set(preview["reuse_node_ids"]) == {"input", "name"}
        result = await submit(client, app, pid, aids[0], rerun_body(document, source, "lang"))
        assert result.status.value == "completed", result.error
        assert len(cloud["calls"]) == 2
        # Changing a parameter needed by reused ASR prevents output-only rerun.
        next(n for n in document["nodes"] if n["id"] == "lang")["config"]["value"] = "en"
        document = await save(
            client,
            pid,
            {
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        preview = await plan(client, pid, aids[0], rerun_body(document, source))
        assert not preview["valid"]
        assert preview["issues"][0]["node_id"] == "lang"


@pytest.mark.asyncio
async def test_queued_rerun_uses_copied_checkpoint_and_cancellation_is_safe(tmp_path, cloud):
    import shutil

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        jobs = app.state.jobs
        await jobs.semaphore.acquire()
        body = rerun_body(document, source)
        url = f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs"
        response = await client.post(url, json=body)
        assert response.status_code == 202, response.text
        rid = response.json()["id"]
        # Frozen results have their own files; removing old run cannot cause
        # the queued execution to read a different/latest source run.
        shutil.rmtree(app.state.store.workflow_run_dir(pid, aids[0], source.id))
        jobs.semaphore.release()
        await asyncio.gather(*jobs.tasks.values())
        result = app.state.store.load_workflow_run(pid, aids[0], rid)
        assert result.status.value == "completed", result.error
        assert len(cloud["calls"]) == 1
        # This new run is itself a valid reuse source.
        await jobs.semaphore.acquire()
        response = await client.post(url, json=rerun_body(document, result, request="cancel"))
        assert response.status_code == 202, response.text
        rid = response.json()["id"]
        jobs.cancel_workflow(pid, aids[0], rid, force=True)
        await asyncio.gather(*jobs.tasks.values(), return_exceptions=True)
        jobs.semaphore.release()
        cancelled = app.state.store.load_workflow_run(pid, aids[0], rid)
        assert cancelled.status.value == "cancelled"
        assert len(cloud["calls"]) == 1


@pytest.mark.asyncio
async def test_batch_item_rerun_preserves_sequence_without_mutating_batch(tmp_path, cloud):
    from test_batches import finished
    from test_batches import submit as submit_batch

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 2)
        body = graph(document)
        body["nodes"][0]["config"]["audio_ids"] = aids
        body["nodes"][-1]["config"].update(filename_stem="sub", naming_mode="prefix_sequence")
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        batch = await submit_batch(client, pid, aids, document)
        batch = await finished(app, pid, batch["id"])
        assert batch.status == "completed"
        original = batch.model_dump(mode="json")
        source = app.state.store.load_workflow_run(pid, aids[1], batch.items[1].run_id)
        result = await submit(client, app, pid, aids[1], rerun_body(document, source))
        assert result.status.value == "completed", result.error
        assert result.batch_id is None
        assert result.batch_item_sequence == 2
        assert (
            next(n for n in result.nodes if n.node_id == "output").artifact.filename
            == "sub_00002.txt"
        )
        assert app.state.batches.get(pid, batch.id).model_dump(mode="json") == original
        assert len(cloud["calls"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("via_shutdown", [False, True])
async def test_preparation_cancel_publishes_terminal_run_and_replays(
    tmp_path, cloud, monkeypatch, via_shutdown
):
    import app.application.workflow_checkpoints as checkpoints
    from app.domain.workflow import WorkflowRerunRequest

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        entered = asyncio.Event()
        cleaned = asyncio.Event()

        async def cancelled_copy(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        monkeypatch.setattr(checkpoints, "copy_checkpoint", cancelled_copy)
        rerun = WorkflowRerunRequest(source_run_id=source.id, from_node_id="output")
        pending = asyncio.create_task(
            app.state.jobs.start_workflow(
                pid,
                aids[0],
                document["workflow_revision"],
                rerun=rerun,
                request_id="cancel-prepare",
            )
        )
        await entered.wait()
        if via_shutdown:
            await app.state.jobs.shutdown()
        else:
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        assert cleaned.is_set()
        records = app.state.store.list_workflow_runs(pid, aids[0], limit=100, offset=0)[0]
        prepared = next(r for r in records if r.request_id == "cancel-prepare")
        assert prepared.status.value == "interrupted"
        assert not app.state.jobs.running(pid, aids[0])
        repeated = await app.state.jobs.start_workflow(
            pid, aids[0], document["workflow_revision"], rerun=rerun, request_id="cancel-prepare"
        )
        assert repeated.id == prepared.id
        assert repeated.status.value == "interrupted"


@pytest.mark.asyncio
async def test_pending_replay_and_invalid_frozen_copy_do_not_recompute(tmp_path, cloud):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        await app.state.jobs.semaphore.acquire()
        body = rerun_body(document, source)
        url = f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs"
        first = await client.post(url, json=body)
        second = await client.post(url, json=body)
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        root = app.state.store.workflow_run_dir(pid, aids[0], first.json()["id"])
        checkpoint = root / ".checkpoints" / "cloud" / "checkpoint.json"
        checkpoint.write_bytes(b"invalid")
        app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        failed = app.state.store.load_workflow_run(pid, aids[0], first.json()["id"])
        assert failed.status.value == "failed"
        assert len(cloud["calls"]) == 1
        assert next(n for n in failed.nodes if n.node_id == "output").status.value == "blocked"


@pytest.mark.asyncio
async def test_layout_edge_ids_irrelevant_but_manual_connected_fallback_is_fingerprinted(
    tmp_path, cloud
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        body = graph(document)
        body["nodes"].append({"id": "lang", "type": "string_value", "config": {"value": "ja"}})
        body["edges"].append(
            {
                "id": "l",
                "source": "lang",
                "source_port": "string",
                "target": "cloud",
                "target_port": "language",
            }
        )
        document = await save(client, pid, body)
        document = await save(client, pid, update_body(document))
        source = await run(client, app, pid, aids[0], document)
        for node in document["nodes"]:
            node["position"] = {"x": 200, "y": 300}
        for edge in document["edges"]:
            edge["id"] = "renamed-" + edge["id"]
        document = await save(
            client,
            pid,
            {
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        assert (await plan(client, pid, aids[0], rerun_body(document, source)))["valid"]
        next(n for n in document["nodes"] if n["id"] == "cloud")["config"]["language"] = "en"
        document = await save(
            client,
            pid,
            {
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        preview = await plan(client, pid, aids[0], rerun_body(document, source))
        assert preview["issues"][0]["code"] == "RERUN_UPSTREAM_CHANGED"
        assert preview["issues"][0]["node_id"] == "cloud"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted", "running"])
async def test_completed_upstream_in_terminal_source_reusable_but_active_source_refused(
    tmp_path, cloud, status
):
    from app.domain.workflow import WorkflowRunStatus

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        source.status = WorkflowRunStatus(status)
        app.state.store.save_workflow_run(source)
        preview = await plan(client, pid, aids[0], rerun_body(document, source))
        assert preview["valid"] == (status != "running")
        if status == "running":
            assert preview["issues"][0]["code"] == "RERUN_SOURCE_ACTIVE"


@pytest.mark.asyncio
@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_shutdown_cancels_preflight_and_prevents_new_publication(
    tmp_path, cloud, monkeypatch, suppress_cancel
):
    import app.application.job_manager as jobs_module
    from app.domain.workflow import WorkflowRerunRequest

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        jobs = app.state.jobs
        original = jobs_module.prepare_reuse
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def preflight(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not suppress_cancel:
                    raise
                return await original(*args)
            finally:
                cleaned.set()

        monkeypatch.setattr(jobs_module, "prepare_reuse", preflight)
        rerun = WorkflowRerunRequest(source_run_id=source.id, from_node_id="output")
        pending = asyncio.create_task(
            jobs.start_workflow(
                pid, aids[0], document["workflow_revision"], rerun=rerun, request_id="preflight"
            )
        )
        await entered.wait()
        await jobs.shutdown()
        assert pending.done()
        assert cleaned.is_set()
        assert not jobs.tasks
        assert not jobs.preparations
        assert app.state.store.list_workflow_runs(pid, aids[0], limit=100, offset=0)[1] == 1
        monkeypatch.setattr(jobs_module, "prepare_reuse", original)
        with pytest.raises(RuntimeError, match="WORKFLOW_STOPPING"):
            await jobs.start_workflow(
                pid, aids[0], document["workflow_revision"], rerun=rerun, request_id="after-stop"
            )
        assert app.state.store.list_workflow_runs(pid, aids[0], limit=100, offset=0)[1] == 1


@pytest.mark.asyncio
async def test_run_publication_survives_metadata_touch_failure_and_replays(
    tmp_path, cloud, monkeypatch
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document, source = await cloud_graph(client, app)
        store = app.state.store
        original = store._touch_project
        calls = 0

        def fail_first(project_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("metadata unavailable after run publication")
            return original(project_id)

        monkeypatch.setattr(store, "_touch_project", fail_first)
        await app.state.jobs.semaphore.acquire()
        url = f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs"
        body = rerun_body(document, source, request="metadata-failure")
        first = await client.post(url, json=body)
        assert first.status_code == 202, first.text
        assert app.state.jobs.running(pid, aids[0])
        second = await client.post(url, json=body)
        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"]
        app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        result = store.load_workflow_run(pid, aids[0], first.json()["id"])
        assert result.status.value == "completed", result.error
        assert (await client.post(url, json=body)).json()["id"] == result.id
        assert store.list_workflow_runs(pid, aids[0], limit=100, offset=0)[1] == 2
        assert len(cloud["calls"]) == 1
