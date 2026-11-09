import asyncio
from types import SimpleNamespace

import pytest
from app.application.adjustable_limiter import AdjustableLimiter
from app.application.job_manager import JobManager
from app.application.workflow_connections import ConnectionSnapshotError
from app.application.workflow_credentials import prepare_workflow_credentials
from app.domain.workflow import WorkflowNode


@pytest.mark.asyncio
async def test_resize_preserves_holders_and_fifo_cancelled_waiters():
    jobs = JobManager(None, None, max_concurrent=2)
    limiter = jobs.semaphore
    await limiter.acquire()
    await limiter.acquire()
    acquired = []

    async def wait(number):
        await limiter.acquire()
        acquired.append(number)

    waiters = [asyncio.create_task(wait(i)) for i in range(4)]
    await asyncio.sleep(0)
    waiters[1].cancel()
    await asyncio.gather(waiters[1], return_exceptions=True)
    jobs.set_max_concurrent(1)
    limiter.release()
    await asyncio.sleep(0)
    assert acquired == []
    limiter.release()
    await asyncio.sleep(0)
    assert acquired == [0]
    jobs.set_max_concurrent(3)
    await asyncio.gather(waiters[0], waiters[2], waiters[3])
    assert acquired == [0, 2, 3]
    assert jobs.semaphore is limiter
    for _ in acquired:
        limiter.release()
    async with limiter:
        pass


@pytest.mark.asyncio
async def test_cancel_after_grant_returns_reserved_capacity():
    limiter = AdjustableLimiter(1)
    await limiter.acquire()
    first = asyncio.create_task(limiter.acquire())
    second = asyncio.create_task(limiter.acquire())
    await asyncio.sleep(0)
    limiter.release()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await asyncio.wait_for(second, 1)
    limiter.release()


def test_default_materialization_is_explicit_new_and_uses_registry(monkeypatch):
    import app.application.workflow_credentials as module

    calls = []

    def operation(store, method, *args, **kwargs):
        calls.append((method, args, kwargs))
        if method == "read_default":
            assert args == ("qwen_asr", "qwen_asr", "opaque-token")
            return "secret-key"
        if method == "create":
            return "ref-" + args[1]

    monkeypatch.setattr(module, "credential_operation", operation)
    nodes = [WorkflowNode(id=name, type="qwen_asr", config={}) for name in ["a", "b"]]
    updates = [
        SimpleNamespace(
            node_id=n.id, operation="use_default", default_credential_version="opaque-token"
        )
        for n in nodes
    ]
    result = prepare_workflow_credentials(None, "pid", nodes, updates, "req", 0)
    refs = [next(value for value in n.config.values()) for n in result]
    assert refs == ["ref-a", "ref-b"]
    assert all(
        "opaque-token" not in str(n.config) and "secret-key" not in str(n.config) for n in result
    )
    assert [n.config for n in nodes] == [{}, {}]
    with pytest.raises(ConnectionSnapshotError, match="仅新节点"):
        prepare_workflow_credentials(None, "pid", nodes, updates, "req", 0, existing_node_ids={"a"})
    assert prepare_workflow_credentials(None, "pid", nodes, [], None, 0) == nodes
    assert (
        prepare_workflow_credentials(
            None, "pid", nodes, updates, "req", 0, existing_node_ids={"a", "b"}, replay=True
        )
        == result
    )


@pytest.mark.asyncio
async def test_default_api_replay_rotation_type_ownership_and_existing_node(tmp_path):
    from app.main import create_app
    from app.storage.secret_store import SecretStore
    from httpx import ASGITransport, AsyncClient

    from test_batches import setup
    from test_inline_credentials import graph

    app = create_app(tmp_path)
    secrets = SecretStore(tmp_path)
    token = secrets.create_default("qwen_asr", "qwen_asr", "old-key", request_id="old")
    secrets.create_default("qwen_asr", "qwen_asr", "new-key", request_id="new")
    wrong = secrets.create_default(
        "qwen_asr_streaming", "qwen_asr_streaming", "wrong-key", request_id="other"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, document = await setup(client, 1)
        body = graph(document)
        body.update(
            request_id="default-save",
            credential_updates=[
                {
                    "node_id": "cloud",
                    "operation": "use_default",
                    "default_credential_version": wrong,
                }
            ],
        )
        url = f"/api/v1/projects/{pid}/workflow"
        assert (await client.put(url, json=body)).status_code == 422
        body["credential_updates"][0]["default_credential_version"] = token
        response = await client.put(url, json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        ref = next(n for n in saved["nodes"] if n["id"] == "cloud")["config"]["credential_ref"]
        assert secrets.read(pid, "cloud", "qwen_asr", ref) == "old-key"
        replay = await client.put(url, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == saved
        assert token not in response.text and "old-key" not in response.text
        existing = dict(
            body,
            expected_revision=saved["revision"],
            nodes=saved["nodes"],
            edges=saved["edges"],
            request_id="existing",
        )
        assert (await client.put(url, json=existing)).status_code == 422


@pytest.mark.asyncio
async def test_batch_workers_shrink_after_current_item_and_grow_immediately(tmp_path):
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    from test_batches import finished, setup, submit

    app = create_app(tmp_path)
    jobs, batches = app.state.jobs, app.state.batches
    jobs.delay_ms = 0
    jobs.set_max_concurrent(3)
    for _ in range(3):
        await jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 6)
        batch = await submit(client, pid, aids, document)
        for _ in range(10):
            await asyncio.sleep(0)
        assert len(batches.workers) == 3
        jobs.set_max_concurrent(1)
        batches.resize()
        for _ in range(3):
            jobs.semaphore.release()
        for _ in range(1000):
            if len(batches.workers) <= 1:
                break
            await asyncio.sleep(0.001)
        assert len(batches.workers) <= 1
        await finished(app, pid, batch["id"])
    await batches.shutdown()
    await jobs.shutdown()


@pytest.mark.asyncio
async def test_batch_resize_dispatches_new_workers_without_waiting(tmp_path, monkeypatch):
    from app.main import create_app

    app = create_app(tmp_path)
    batches, jobs = app.state.batches, app.state.jobs
    stop = asyncio.Event()

    async def worker():
        await stop.wait()

    monkeypatch.setattr(batches, "_worker", worker)
    batches.pending.extend([("pending", i) for i in range(4)])
    batches.resize()
    assert len(batches.workers) == 1
    jobs.set_max_concurrent(3)
    batches.resize()
    assert len(batches.workers) == 3
    batches.pending.clear()
    stop.set()
    await asyncio.gather(*batches.workers)
    await asyncio.sleep(0)
    assert not batches.workers
