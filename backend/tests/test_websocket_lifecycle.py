import asyncio
from pathlib import Path

import pytest
from app.main import create_app


class WebSocketHarness:
    def __init__(self, app, project_id: str, fail_send_after: int | None = None):
        self.app = app
        self.project_id = project_id
        self.incoming = asyncio.Queue()
        self.sent = []
        self._connected = False
        self.fail_send_after = fail_send_after

    async def receive(self):
        if not self._connected:
            self._connected = True
            return {"type": "websocket.connect"}
        return await self.incoming.get()

    async def send(self, message):
        if self.fail_send_after is not None and len(self.sent) >= self.fail_send_after:
            raise RuntimeError("peer closed while sending")
        self.sent.append(message)

    async def run(self):
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": f"/api/v1/projects/{self.project_id}/events",
            "raw_path": f"/api/v1/projects/{self.project_id}/events".encode(),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        return await self.app(scope, self.receive, self.send)


async def _project(app) -> str:
    # Use the storage API directly so the test only exercises the websocket
    # ASGI lifecycle after a valid project exists.
    from app.domain import Job, Project, SourceAsset

    value = Project(
        id="prj_ws_lifecycle",
        name="websocket",
        source=SourceAsset(path="source/original.wav", original_name="original.wav", size_bytes=0),
    )
    app.state.store.create_project(value)
    app.state.store.save_job(Job(id="job_ws_lifecycle", project_id=value.id))
    return value.id


@pytest.mark.asyncio
async def test_idle_disconnect_unsubscribes_and_finishes(tmp_path: Path):
    app = create_app(tmp_path)
    project_id = await _project(app)
    harness = WebSocketHarness(app, project_id)
    route_task = asyncio.create_task(harness.run())

    for _ in range(100):
        if any(message.get("type") == "websocket.send" for message in harness.sent):
            break
        await asyncio.sleep(0)
    assert harness.sent[0]["type"] == "websocket.accept"
    assert harness.sent[1]["type"] == "websocket.send"
    assert harness.sent[1]["text"]
    assert project_id in app.state.events._subs

    await harness.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(route_task, timeout=1)
    assert project_id not in app.state.events._subs


@pytest.mark.asyncio
async def test_snapshot_then_event_uses_real_asgi_messages(tmp_path: Path):
    app = create_app(tmp_path)
    project_id = await _project(app)
    harness = WebSocketHarness(app, project_id)
    route_task = asyncio.create_task(harness.run())

    for _ in range(100):
        if len(harness.sent) >= 2:
            break
        await asyncio.sleep(0)
    snapshot = harness.sent[1]["text"]
    assert '"type":"job.snapshot"' in snapshot

    await app.state.events.publish(
        {
            "schema_version": "1.0",
            "event_id": "evt_test",
            "type": "job.started",
            "project_id": project_id,
            "job_id": "job_ws_lifecycle",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "data": {},
        }
    )
    for _ in range(100):
        if len(harness.sent) >= 3:
            break
        await asyncio.sleep(0)
    assert '"type":"job.started"' in harness.sent[2]["text"]

    await harness.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(route_task, timeout=1)
    assert project_id not in app.state.events._subs


@pytest.mark.asyncio
async def test_send_failure_still_cleans_up_subscription(tmp_path: Path):
    app = create_app(tmp_path)
    project_id = await _project(app)
    harness = WebSocketHarness(app, project_id, fail_send_after=2)
    route_task = asyncio.create_task(harness.run())

    for _ in range(100):
        if len(harness.sent) >= 2:
            break
        await asyncio.sleep(0)
    await app.state.events.publish(
        {
            "schema_version": "1.0",
            "event_id": "evt_failure",
            "type": "job.started",
            "project_id": project_id,
            "job_id": "job_ws_lifecycle",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "data": {},
        }
    )
    await asyncio.wait_for(route_task, timeout=1)
    assert project_id not in app.state.events._subs


@pytest.mark.asyncio
async def test_endpoint_cancellation_cleans_up_before_awaiting_children(tmp_path: Path):
    app = create_app(tmp_path)
    project_id = await _project(app)
    harness = WebSocketHarness(app, project_id)
    route_task = asyncio.create_task(harness.run())

    for _ in range(100):
        if len(harness.sent) >= 2:
            break
        await asyncio.sleep(0)
    route_task.cancel()
    await asyncio.wait_for(route_task, timeout=1)
    assert project_id not in app.state.events._subs
