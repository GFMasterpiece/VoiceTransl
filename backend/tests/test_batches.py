import asyncio
import io
import json
from zipfile import ZipFile

import pytest
from app.domain.batch import ACTIVE_BATCH_STATUSES
from app.domain.schema import SCHEMA_VERSION
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_workflow import create_project, get_default_workflow, tiny_wav


async def setup(client, count=3):
    pid, aid = await create_project(client)
    aids = [aid]
    for _ in range(count - 1):
        response = await client.post(
            f"/api/v1/projects/{pid}/audios",
            files={"file": ("same.wav", tiny_wav(), "audio/wav")},
            data={"source_language": "ja"},
        )
        assert response.status_code == 201, response.text
        aids.append(response.json()["audio"]["id"])
    document = await get_default_workflow(client, pid)
    return pid, aids, document


async def submit(client, pid, aids, document, request_id="create"):
    response = await client.post(
        f"/api/v1/projects/{pid}/batches",
        json={
            "workflow_revision": document["workflow_revision"],
            "audio_ids": aids,
            "request_id": request_id,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def finished(app, pid, bid):
    async with asyncio.timeout(10):
        while app.state.batches.get(pid, bid).status in ACTIVE_BATCH_STATUSES:
            await asyncio.sleep(0.005)
    return app.state.batches.get(pid, bid)


@pytest.mark.asyncio
async def test_frozen_workflow_busy_boundary_idempotency_and_pagination(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        batch = await submit(client, pid, aids, document)
        bid = batch["id"]
        assert batch["counts"]["queued"] == 3
        assert len(app.state.batches.workers) <= app.state.jobs.max_concurrent
        assert len(app.state.jobs.reservations) == 3
        duplicate = await submit(client, pid, aids, document)
        assert duplicate["id"] == bid
        conflict = await client.post(
            f"/api/v1/projects/{pid}/batches",
            json={
                "workflow_revision": document["workflow_revision"],
                "audio_ids": aids[:1],
                "request_id": "create",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "BATCH_REQUEST_CONFLICT"
        busy = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[-1]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "AUDIO_BUSY"
        assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 409
        document["nodes"][1]["config"]["filename_stem"] = "changed"
        changed = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        assert changed.status_code == 200
        items = (
            await client.get(f"/api/v1/projects/{pid}/batches/{bid}/items?limit=1&offset=1")
        ).json()
        assert items["total"] == 3
        assert items["items"][0]["audio_id"] == aids[1]
        app.state.jobs.semaphore.release()
        done = await finished(app, pid, bid)
        assert done.status == "completed"
        assert len({item.run_id for item in done.items}) == 3
        for item in done.items:
            run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
            assert run.batch_id == bid
            assert run.workflow.nodes[1].config["filename_stem"] != "changed"
            assert run.workflow_revision == document["workflow_revision"]
        assert not app.state.jobs.reservations
        listed = (await client.get(f"/api/v1/projects/{pid}/batches?limit=1&offset=0")).json()
        assert listed["total"] == 1
        assert "workflow" not in listed["items"][0]
        assert "items" not in (await client.get(f"/api/v1/projects/{pid}/batches/{bid}")).json()
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_failure_isolation_retry_new_history_and_zip(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    from app.application import job_manager

    original = job_manager.execute_workflow_run
    fail_aid = None

    async def fail_one(store, run, event, **kwargs):
        if run.audio_id == fail_aid:
            raise RuntimeError("injected failure")
        await original(store, run, event, **kwargs)

    monkeypatch.setattr(job_manager, "execute_workflow_run", fail_one)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        fail_aid = aids[1]
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        assert batch.status == "partial"
        assert batch.counts["failed"] == 1
        assert batch.counts["completed"] == 2
        failed_run = batch.items[1].run_id
        fail_aid = None
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 202, response.text
        retried = await finished(app, pid, response.json()["id"])
        assert retried.retry_of == batch.id
        assert retried.total == 1
        assert retried.status == "completed"
        assert retried.items[0].run_id != failed_run
        assert app.state.batches.get(pid, batch.id).items[1].status == "failed"
        runs, total = app.state.store.list_workflow_runs(pid, aids[1], limit=10, offset=0)
        assert total == 2
        assert {run.id for run in runs} == {failed_run, retried.items[0].run_id}
        response = await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        assert response.status_code == 200, response.text
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format"] == "voicetransl.batch-results"
            assert manifest["schema_version"] == SCHEMA_VERSION
            assert len(manifest["items"]) == 3
            outputs = [output["path"] for item in manifest["items"] for output in item["outputs"]]
            assert len(outputs) == len(set(outputs)) == 2
            assert all(archive.read(path) == tiny_wav() for path in outputs)
            assert all(".." not in path.split("/") for path in archive.namelist())
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_cancel_queued_active_and_retry_cancelled_opt_in(tmp_path):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        created = await submit(client, pid, aids, document)
        await asyncio.sleep(0)
        response = await client.post(f"/api/v1/projects/{pid}/batches/{created['id']}/cancel")
        assert response.status_code == 202
        assert response.json()["counts"]["cancelled"] >= 2
        app.state.jobs.semaphore.release()
        done = await finished(app, pid, created["id"])
        assert done.status == "cancelled"
        assert done.counts["cancelled"] == 3
        assert not app.state.jobs.reservations
        again = await client.post(f"/api/v1/projects/{pid}/batches/{done.id}/cancel")
        assert again.status_code == 200
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{done.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 422
        app.state.jobs.delay_ms = 0
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{done.id}/retry",
            json={"request_id": "retry", "include_cancelled": True},
        )
        assert response.status_code == 202
        assert (await finished(app, pid, response.json()["id"])).status == "completed"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_shutdown_and_startup_interrupt_never_auto_resume(tmp_path):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        created = await submit(client, pid, aids, document)
        await asyncio.sleep(0)
        # Save the pre-shutdown image to simulate a process loss at this point.
        path = app.state.batches.store.path(pid, created["id"])
        active_payload = path.read_bytes()
        await app.state.batches.shutdown()
        await app.state.jobs.shutdown()
        assert not app.state.jobs.reservations
        assert not app.state.jobs.tasks
        assert app.state.batches.get(pid, created["id"]).counts["interrupted"] == 3
        path.write_bytes(active_payload)
    restarted = create_app(tmp_path)
    async with restarted.router.lifespan_context(restarted):
        batch = restarted.state.batches.get(pid, created["id"])
        assert batch.status == "interrupted"
        assert batch.counts["interrupted"] == 3
        assert not restarted.state.batches.workers
        assert not restarted.state.jobs.tasks
        if batch.items[0].run_id:
            run = restarted.state.store.load_workflow_run(pid, aids[0], batch.items[0].run_id)
            assert run.status.value == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["audio_input", "alternate_resource"])
async def test_explicit_binding_and_zip_metadata_path_refused(tmp_path, monkeypatch, input_type):
    if input_type != "audio_input":
        from dataclasses import replace

        from app.domain.node_registry import NODE_DEFINITIONS

        monkeypatch.setitem(
            NODE_DEFINITIONS, input_type, replace(NODE_DEFINITIONS["audio_input"], type=input_type)
        )
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document["nodes"][0]["type"] = input_type
        document["nodes"][0]["config"]["audio_id"] = aids[0]
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        assert response.status_code == 200
        response = await client.post(
            f"/api/v1/projects/{pid}/batches",
            json={
                "workflow_revision": response.json()["workflow_revision"],
                "audio_ids": aids,
                "request_id": "bound",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "BATCH_EXPLICIT_AUDIO_BINDING"
        document = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
        document["nodes"][0]["config"] = {}
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": document["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        created = await submit(client, pid, aids, response.json())
        batch = await finished(app, pid, created["id"])
        path = app.state.batches.store.path(pid, batch.id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["items"][0]["origin"] = {
            "root_id": "x",
            "root_name": "root",
            "relative_path": "../escape.wav",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert (
            await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        ).status_code == 404
        assert (await client.get(f"/api/v1/projects/{pid}/batches/bat_bad")).status_code == 404
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", [2, 3, 4, 6])
async def test_transient_state_write_failure_releases_all_work(tmp_path, monkeypatch, fail_on):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    real_save = app.state.batches.store.save
    calls = 0

    def fail_once(batch):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise OSError("injected storage failure")
        return real_save(batch)

    monkeypatch.setattr(app.state.batches.store, "save", fail_once)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        batch = await submit(client, pid, aids, document)
        await finished(app, pid, batch["id"])
        await asyncio.sleep(0)
        assert not app.state.jobs.reservations
        assert not app.state.jobs.tasks
        assert all(
            item.status not in {"running", "queued"}
            for item in app.state.batches.store.load(pid, batch["id"]).items
        )
        for item in app.state.batches.store.load(pid, batch["id"]).items:
            if item.run_id:
                run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
                assert run.status.value not in {"pending", "running", "cancelling"}
                if fail_on == 3 and item.index == 0:
                    assert run.status.value == "cancelled"
        next_batch = await submit(client, pid, aids, document, "after-error")
        assert (await finished(app, pid, next_batch["id"])).status == "completed"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_dispatch_after_completed_worker_callback(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        done = asyncio.create_task(asyncio.sleep(0))
        await done
        manager = app.state.batches
        manager.workers.add(done)
        done.add_done_callback(manager._worker_done)
        # create() does not yield while the project mutation lock is available:
        # the old completed worker is still counted when _dispatch first runs.
        batch = await manager.create(pid, document["workflow_revision"], aids, "race")
        assert (await finished(app, pid, batch.id)).status == "completed"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_cancel_waits_for_active_cleanup_before_releasing_reservation(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    from app.application import job_manager

    started = asyncio.Event()
    cleaning = asyncio.Event()
    finish_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def cancellable(store, run, event, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish_cleanup.wait()
            cleaned.set()

    monkeypatch.setattr(job_manager, "execute_workflow_run", cancellable)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        batch = await submit(client, pid, aids, document)
        await asyncio.wait_for(started.wait(), 5)
        response = await client.post(f"/api/v1/projects/{pid}/batches/{batch['id']}/cancel")
        assert response.status_code == 202
        await asyncio.wait_for(cleaning.wait(), 5)
        assert not cleaned.is_set()
        assert (pid, aids[0]) in app.state.jobs.reservations
        assert (await client.delete(f"/api/v1/projects/{pid}")).status_code == 409
        finish_cleanup.set()
        done = await finished(app, pid, batch["id"])
        assert done.status == "cancelled"
        assert done.counts["cancelled"] == 3
        assert cleaned.is_set()
        assert not app.state.jobs.reservations
        assert not app.state.jobs.tasks
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_zip_missing_outputs_and_runs_reported_and_temporary_removed(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    import app.api.batches as api

    original_temp = api.tempfile.mkstemp
    temporary_paths = []

    def temporary(*args, **kwargs):
        descriptor, path = original_temp(*args, **kwargs)
        temporary_paths.append(path)
        return descriptor, path

    monkeypatch.setattr(api.tempfile, "mkstemp", temporary)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        first = batch.items[0]
        run = app.state.store.load_workflow_run(pid, first.audio_id, first.run_id)
        node = run.nodes[-1]
        path = app.state.store.workflow_artifact_path(
            pid, first.audio_id, run.id, node.node_id, node.artifact.filename
        )
        path.unlink()
        second = batch.items[1]
        (
            app.state.store.workflow_run_dir(pid, second.audio_id, second.run_id) / "run.json"
        ).unlink()
        response = await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        assert response.status_code == 200, response.text
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["items"][0]["missing_outputs"][0]["node_id"] == "output"
            assert manifest["items"][1]["export_error"]["type"] == "BATCH_RUN_MISSING"
            assert len(manifest["items"][2]["outputs"]) == 1
        from pathlib import Path

        assert temporary_paths and all(not Path(path).exists() for path in temporary_paths)
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_zip_roots_collisions_and_origin_are_frozen(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        for root in ("root", "ROOT"):
            response = await client.post(
                f"/api/v1/projects/{pid}/audios",
                files={"file": ("same.wav", tiny_wav(), "audio/wav")},
                data={
                    "source_language": "en",
                    "origin_root_id": root,
                    "origin_root_name": "same",
                    "origin_relative_path": "sub/same.wav",
                },
            )
            assert response.status_code == 201, response.text
            aids.append(response.json()["audio"]["id"])
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        assert batch.items[1].origin.root_id == "root"
        assert batch.items[1].source_language == "en"
        response = await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        assert response.status_code == 200
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            paths = [item["outputs"][0]["path"] for item in manifest["items"]]
            assert len({path.casefold() for path in paths}) == 3
            roots = [path.split("/")[0].casefold() for path in paths[1:]]
            assert roots[0] != roots[1]
            assert all("/sub/" in path for path in paths[1:])
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("total", 99), ("schema_version", "99.0"), ("counts", {"queued": -1})]
)
async def test_corrupt_and_unknown_batches_preserved_on_recovery(tmp_path, field, value):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        created = await submit(client, pid, aids, document)
        await asyncio.sleep(0)
        path = app.state.batches.store.path(pid, created["id"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        await app.state.batches.shutdown()
        await app.state.jobs.shutdown()
        payload[field] = value
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()
    restarted = create_app(tmp_path)
    async with restarted.router.lifespan_context(restarted):
        assert path.read_bytes() == before
        assert not restarted.state.jobs.tasks


@pytest.mark.asyncio
async def test_batch_run_association_and_legacy_run_read_do_not_rewrite(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        item = batch.items[0]
        run_response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs/{item.run_id}"
        )
        assert run_response.json()["batch_id"] == batch.id
        path = app.state.store.workflow_run_dir(pid, aids[0], item.run_id) / "run.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("batch_id")
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()
        assert app.state.store.load_workflow_run(pid, aids[0], item.run_id).batch_id is None
        assert path.read_bytes() == before
        single = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert single.status_code == 202
        assert single.json()["batch_id"] is None
        task = app.state.jobs.tasks.get((pid, aids[0]))
        if task:
            await task
        # An otherwise valid run from another batch cannot supply this batch's
        # export, even if its graph and project/audio ownership match.
        payload["batch_id"] = "bat_" + "0" * 32
        path.write_text(json.dumps(payload), encoding="utf-8")
        response = await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        assert response.status_code == 422
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_temporary_download_removed_on_disconnected_send(tmp_path):
    from app.api.batches import _TemporaryZipResponse

    path = tmp_path / "partial.zip"
    path.write_bytes(b"test")
    response = _TemporaryZipResponse(path, media_type="application/zip")

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        raise OSError("client disconnected")

    with pytest.raises(OSError, match="client disconnected"):
        await response(
            {"type": "http", "method": "GET", "headers": [], "extensions": {}}, receive, send
        )
    assert not path.exists()


@pytest.mark.asyncio
async def test_paginated_items_do_not_read_each_run_or_project(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        created = await submit(client, pid, aids, document)
        await finished(app, pid, created["id"])
        project_reads = 0
        real_project = app.state.store.load_project

        def project(*args, **kwargs):
            nonlocal project_reads
            project_reads += 1
            return real_project(*args, **kwargs)

        def unexpected_run(*args, **kwargs):
            raise AssertionError("pagination must not load workflow runs")

        monkeypatch.setattr(app.state.store, "load_project", project)
        monkeypatch.setattr(app.state.store, "load_workflow_run", unexpected_run)
        response = await client.get(
            f"/api/v1/projects/{pid}/batches/{created['id']}/items?limit=1&offset=1"
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
        assert project_reads == 1
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()
