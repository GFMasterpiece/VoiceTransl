import asyncio
import json

import pytest
from app.domain.node_execution import NodeExecutionError
from app.domain.transcript import Segment, Transcript
from app.main import create_app
from app.storage.secret_store import SecretStore
from httpx import ASGITransport, AsyncClient

from test_batches import finished, setup, submit

KEY_A = "sk-INLINE-SECRET-A-MUST-NEVER-BE-SERIALIZED"
KEY_B = "sk-INLINE-SECRET-B-MUST-NEVER-BE-SERIALIZED"


@pytest.fixture
def cloud(monkeypatch):
    from app.adapters import qwen_asr

    state = {"calls": [], "fail": set()}

    async def transcribe(**kwargs):
        state["calls"].append(kwargs)
        if kwargs["audio_path"] in state["fail"]:
            raise NodeExecutionError("TEST_CLOUD_FAILED", "测试识别失败")
        return Transcript(
            project_id=kwargs["project_id"],
            stage="raw",
            language=kwargs["language"],
            segments=[Segment(id="s1", source_text="Recognized text", language=kwargs["language"])],
        )

    monkeypatch.setattr(qwen_asr, "transcribe", transcribe)
    return state


def graph(document, config=None):
    return {
        "expected_revision": document["revision"],
        "nodes": [
            document["nodes"][0],
            {"id": "cloud", "type": "qwen_asr", "config": config or {}},
            {"id": "output", "type": "text_output", "config": {"format": "txt"}},
        ],
        "edges": [
            {
                "id": "a",
                "source": "input",
                "source_port": "audio",
                "target": "cloud",
                "target_port": "audio",
            },
            {
                "id": "b",
                "source": "cloud",
                "source_port": "transcript",
                "target": "output",
                "target_port": "transcript",
            },
        ],
    }


def update_body(document, key=KEY_A, request_id="key-save"):
    return {
        "expected_revision": document["revision"],
        "nodes": document["nodes"],
        "edges": document["edges"],
        "request_id": request_id,
        "credential_updates": [{"node_id": "cloud", "operation": "set", "api_key": key}],
    }


def config_of(document):
    return next(node["config"] for node in document["nodes"] if node["id"] == "cloud")


async def save(client, pid, payload):
    response = await client.put(f"/api/v1/projects/{pid}/workflow", json=payload)
    assert response.status_code == 200, response.text
    assert KEY_A not in response.text and KEY_B not in response.text
    return response.json()


async def run(client, app, pid, aid, document):
    response = await client.post(
        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
        json={"workflow_revision": document["workflow_revision"]},
    )
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.jobs.tasks.values())
    return app.state.store.load_workflow_run(pid, aid, response.json()["id"])


@pytest.mark.asyncio
async def test_minimal_inline_draft_key_save_replay_clear_and_no_plaintext(tmp_path, cloud, caplog):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save(client, pid, graph(document))
        config = config_of(document)
        assert config["base_url"] == "https://maas.qianwenaiapi.com/api/v1"
        assert config["timeout_seconds"] == 3600
        assert config["region"] is config["credential_ref"] is None
        validation = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": document["nodes"], "edges": document["edges"]},
        )
        assert validation.json()["valid"]
        rejected = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "CREDENTIAL_REQUIRED"
        payload = update_body(document)
        saved = await save(client, pid, payload)
        ref = config_of(saved)["credential_ref"]
        assert ref.startswith("cred_")
        assert SecretStore(tmp_path).read(pid, "cloud", "qwen_asr", ref) == KEY_A
        repeated = await save(client, pid, payload)
        assert repeated == saved
        changed = {
            **payload,
            "credential_updates": [{"node_id": "cloud", "operation": "set", "api_key": KEY_B}],
        }
        conflict = await client.put(f"/api/v1/projects/{pid}/workflow", json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "CREDENTIAL_REQUEST_CONFLICT"
        assert KEY_A not in conflict.text and KEY_B not in conflict.text
        completed = await run(client, app, pid, aids[0], saved)
        assert completed.status.value == "completed"
        assert cloud["calls"][-1]["api_key"] == KEY_A
        assert cloud["calls"][-1]["connection"].default_timeout == 3600
        assert completed.workflow.provider_profiles_snapshot is None
        current = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
        assert config_of(current)["credential_ref"] == ref
        clear = {
            **update_body(current, request_id="clear"),
            "credential_updates": [{"node_id": "cloud", "operation": "clear"}],
        }
        cleared = await save(client, pid, clear)
        assert config_of(cleared)["credential_ref"] is None
        assert SecretStore(tmp_path).read(pid, "cloud", "qwen_asr", ref) == KEY_A
        assert await save(client, pid, clear) == cleared
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert KEY_A.encode() not in path.read_bytes()
                assert KEY_B.encode() not in path.read_bytes()
        assert KEY_A not in caplog.text and KEY_B not in caplog.text


@pytest.mark.asyncio
async def test_rotation_does_not_retarget_queued_batch_or_retry(tmp_path, cloud):
    app = create_app(tmp_path)
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        document = await save(client, pid, graph(document))
        saved = await save(client, pid, update_body(document))
        failed_path = app.state.store.scope(pid, aids[1]).source_path(
            pid, app.state.store.audio(pid, aids[1]).source.path
        )
        cloud["fail"].add(failed_path)
        created = await submit(client, pid, aids, saved)
        rotated = await save(client, pid, update_body(saved, KEY_B, "rotate"))
        assert config_of(rotated)["credential_ref"] != config_of(saved)["credential_ref"]
        app.state.jobs.semaphore.release()
        batch = await finished(app, pid, created["id"])
        assert batch.status == "partial"
        assert len(cloud["calls"]) == 3
        assert {call["api_key"] for call in cloud["calls"]} == {KEY_A}
        cloud["fail"].clear()
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 202, response.text
        retried = await finished(app, pid, response.json()["id"])
        assert retried.status == "completed"
        assert cloud["calls"][-1]["api_key"] == KEY_A
        assert retried.items[0].sequence == 2
        assert (
            config_of((await client.get(f"/api/v1/projects/{pid}/workflow")).json())[
                "credential_ref"
            ]
            == config_of(rotated)["credential_ref"]
        )
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("post_commit", [False, True])
async def test_save_failures_preserve_old_key_or_recover_committed_graph(
    tmp_path, cloud, monkeypatch, post_commit
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, document = await setup(client, 1)
        document = await save(client, pid, graph(document))
        saved = await save(client, pid, update_body(document))
        old_ref = config_of(saved)["credential_ref"]
        old_bytes = app.state.store._workflow_path(pid).read_bytes()
        payload = update_body(saved, KEY_B, "failure")

        def failure(*args, **kwargs):
            raise OSError("injected filesystem failure")

        method = "_touch_project" if post_commit else "save_workflow"
        with monkeypatch.context() as context:
            context.setattr(app.state.store, method, failure)
            response = await client.put(f"/api/v1/projects/{pid}/workflow", json=payload)
        if post_commit:
            assert response.status_code == 200, response.text
            assert config_of(response.json())["credential_ref"] != old_ref
        else:
            assert response.status_code == 503
            assert app.state.store._workflow_path(pid).read_bytes() == old_bytes
        assert SecretStore(tmp_path).read(pid, "cloud", "qwen_asr", old_ref) == KEY_A
        recovered = await save(client, pid, payload)
        assert (
            SecretStore(tmp_path).read(
                pid, "cloud", "qwen_asr", config_of(recovered)["credential_ref"]
            )
            == KEY_B
        )
        assert KEY_B not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update",
    [
        {"node_id": "cloud", "operation": KEY_A, "api_key": KEY_A},
        {"node_id": "cloud", "operation": "clear", "api_key": KEY_A},
        {"node_id": "cloud", "operation": "set", "api_key": {"bad": KEY_A}},
    ],
)
async def test_malformed_write_only_updates_never_echo_input(tmp_path, update, caplog):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, document = await setup(client, 1)
        payload = graph(document)
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={**payload, "request_id": "bad", "credential_updates": [update]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert KEY_A not in response.text and KEY_A not in caplog.text
        assert not app.state.store._workflow_path(pid).exists()


@pytest.mark.asyncio
async def test_foreign_ref_and_missing_master_never_fallback_to_env(tmp_path, cloud, monkeypatch):
    app = create_app(tmp_path)
    monkeypatch.setenv("INLINE_BACKUP_KEY", KEY_B)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save(
            client,
            pid,
            graph(document),
        )
        saved = await save(client, pid, update_body(document))
        copied = json.loads(json.dumps(saved))
        next(node for node in copied["nodes"] if node["id"] == "cloud")["id"] = "copy"
        for edge in copied["edges"]:
            edge["source"] = "copy" if edge["source"] == "cloud" else edge["source"]
            edge["target"] = "copy" if edge["target"] == "cloud" else edge["target"]
        response = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": saved["revision"],
                "nodes": copied["nodes"],
                "edges": copied["edges"],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CREDENTIAL_OWNER_MISMATCH"
        (tmp_path / ".credentials" / "master.key").unlink()
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CREDENTIAL_MASTER_KEY_MISSING"
        assert not cloud["calls"] and not app.state.jobs.tasks
