import asyncio
import json

import pytest
from app.domain.workflow import WorkflowNode, WorkflowSize
from app.main import create_app
from app.storage import FileSystemProjectStore
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from test_workflow import create_project


@pytest.mark.parametrize(
    "size",
    [
        {"width": 0, "height": 100},
        {"width": 100, "height": -1},
        {"width": 1_000_001, "height": 100},
        {"width": 100, "height": 1_000_001},
        {"width": float("nan"), "height": 100},
        {"width": 100, "height": float("inf")},
        {"width": float("-inf"), "height": 100},
        {"width": True, "height": 100},
        {"width": "300", "height": 100},
        {"width": 100},
        {"width": 100, "height": 100, "extra": 1},
    ],
)
def test_size_rejects_invalid_values(size):
    with pytest.raises(ValidationError):
        WorkflowSize.model_validate(size)


@pytest.mark.asyncio
async def test_size_layout_revision_persistence_and_snapshot(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        root = f"/api/v1/projects/{pid}"
        workflow = (await client.get(root + "/workflow")).json()
        # An old graph does not need size and remains untouched on disk when read.
        for node in workflow["nodes"]:
            node.pop("size", None)
        initial = await client.put(
            root + "/workflow",
            json={"expected_revision": 0, "nodes": workflow["nodes"], "edges": workflow["edges"]},
        )
        assert initial.status_code == 200
        initial = initial.json()
        path = app.state.store.project_dir(pid) / "workflow.json"
        legacy = json.loads(path.read_text(encoding="utf-8"))
        for node in legacy["nodes"]:
            node.pop("size", None)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        original = path.read_bytes()
        current = (await client.get(root + "/workflow")).json()
        assert all(node["size"] is None for node in current["nodes"])
        assert path.read_bytes() == original
        current["nodes"][0]["size"] = {"width": 420, "height": 275.5}
        saved = await client.put(
            root + "/workflow",
            json={
                "expected_revision": current["revision"],
                "nodes": current["nodes"],
                "edges": current["edges"],
            },
        )
        assert saved.status_code == 200, saved.text
        saved = saved.json()
        assert saved["revision"] == initial["revision"] + 1
        assert saved["workflow_revision"] == initial["workflow_revision"]
        reopened = FileSystemProjectStore(tmp_path).load_workflow(pid)
        assert reopened.nodes[0].size == WorkflowSize(width=420, height=275.5)
        assert reopened.nodes[1].size is None
        started = await client.post(
            root + f"/audios/{aid}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert started.status_code == 202, started.text
        run_url = root + f"/audios/{aid}/workflow-runs/{started.json()['id']}"
        for _ in range(100):
            run = (await client.get(run_url)).json()
            if run["status"] not in ("pending", "running"):
                break
            await asyncio.sleep(0.02)
        assert run["status"] == "completed"
        assert run["workflow"]["nodes"][0]["size"] == {"width": 420, "height": 275.5}
        saved["nodes"][0]["size"] = None
        edited = await client.put(
            root + "/workflow",
            json={
                "expected_revision": saved["revision"],
                "nodes": saved["nodes"],
                "edges": saved["edges"],
            },
        )
        assert edited.status_code == 200
        assert edited.json()["workflow_revision"] == saved["workflow_revision"]
        assert (await client.get(run_url)).json() == run
        for size in ({"width": 0, "height": 100}, {"width": 100, "height": 100, "extra": 1}):
            saved["nodes"][0]["size"] = size
            invalid = await client.put(
                root + "/workflow",
                json={
                    "expected_revision": edited.json()["revision"],
                    "nodes": saved["nodes"],
                    "edges": saved["edges"],
                },
            )
            assert invalid.status_code == 422
        old_run = app.state.store.load_workflow_run(pid, aid, run["id"])
        payload = old_run.model_dump(mode="json")
        for node in payload["workflow"]["nodes"]:
            node.pop("size", None)
        from app.domain.workflow import WorkflowRun

        assert all(node.size is None for node in WorkflowRun.model_validate(payload).workflow.nodes)


def test_legacy_node_size_defaults_to_none():
    assert WorkflowNode(id="input", type="audio_input").size is None
