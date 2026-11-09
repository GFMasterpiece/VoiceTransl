import json
from dataclasses import replace

import pytest
from app.application.workflow_connections import (
    ConnectionSnapshotError,
    freeze_workflow_connections,
    inline_connection_from_snapshot,
    validate_frozen_workflow_connections,
)
from app.domain.batch import BatchItem, BatchRecord, item_counts
from app.domain.node_registry import NODE_DEFINITIONS, validate_node_definition
from app.domain.workflow import WorkflowSnapshot, workflow_snapshot
from app.main import create_app
from app.nodes.qwen_asr import normalize
from httpx import ASGITransport, AsyncClient

from test_batches import setup
from test_inline_credentials import KEY_A, config_of, graph, run, save, update_body


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_profile_id", "old"),
        ("provider_profile_id", ""),
        ("connection_mode", "inline"),
        ("connection_mode", "profile"),
        ("api_key_env", "DASHSCOPE_API_KEY"),
        ("api_key_env", None),
    ],
)
@pytest.mark.asyncio
async def test_retired_connection_config_is_readable_but_cannot_save_run_batch_or_retry(
    tmp_path, field, value
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        saved = await save(client, pid, graph(document))
        payload = graph(saved, {field: value})
        validation = await client.post(
            f"/api/v1/projects/{pid}/workflow/validate",
            json={"nodes": payload["nodes"], "edges": payload["edges"]},
        )
        assert validation.status_code == 200 and not validation.json()["valid"]
        assert validation.json()["errors"][0]["code"] == "LEGACY_CONNECTION_UNSUPPORTED"
        rejected = await client.put(f"/api/v1/projects/{pid}/workflow", json=payload)
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "LEGACY_CONNECTION_UNSUPPORTED"
        assert "删除并重新添加" in rejected.json()["error"]["message"]
        # Simulate an untouched on-disk document from the retired connection era.
        path = app.state.store._workflow_path(pid)
        old = {**saved, "nodes": payload["nodes"]}
        path.write_text(json.dumps(old), encoding="utf-8")
        before = path.read_bytes()
        read = await client.get(f"/api/v1/projects/{pid}/workflow")
        assert read.status_code == 200
        assert config_of(read.json())[field] == value
        rejected = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "LEGACY_CONNECTION_UNSUPPORTED"
        rejected = await client.post(
            f"/api/v1/projects/{pid}/batches",
            json={
                "workflow_revision": saved["workflow_revision"],
                "audio_ids": aids,
                "request_id": "old",
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "LEGACY_CONNECTION_UNSUPPORTED"
        snapshot = WorkflowSnapshot(
            revision=saved["revision"],
            workflow_revision=saved["workflow_revision"],
            nodes=payload["nodes"],
            edges=payload["edges"],
            provider_profiles_snapshot={"old": {"id": "old", "schema_version": "unknown"}},
        )
        with pytest.raises(ConnectionSnapshotError) as error:
            validate_frozen_workflow_connections(snapshot, app.state.store, pid)
        assert error.value.code == "LEGACY_CONNECTION_UNSUPPORTED"
        items = [
            BatchItem(
                index=0,
                sequence=1,
                audio_id=aids[0],
                audio_name="old.wav",
                source_language="ja",
                status="failed",
            )
        ]
        batch = BatchRecord(
            id="bat_" + "a" * 32,
            project_id=pid,
            status="failed",
            total=1,
            workflow_revision=snapshot.workflow_revision,
            workflow=snapshot,
            request_id="old",
            request_digest="a" * 64,
            items=items,
            counts=item_counts(items),
        )
        app.state.batches.store.save(batch)
        batch_path = app.state.batches.store.path(pid, batch.id)
        before_batch = batch_path.read_bytes()
        rejected = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry-old"}
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "LEGACY_CONNECTION_UNSUPPORTED"
        assert batch_path.read_bytes() == before_batch
        assert path.read_bytes() == before
        assert not app.state.jobs.tasks and not app.state.jobs.reservations


@pytest.mark.asyncio
async def test_provider_routes_and_storage_are_gone_and_corrupt_old_file_is_ignored(tmp_path):
    old = tmp_path / "provider_profiles.json"
    old.write_text('{"schema_version":"unknown","profiles":not valid json', encoding="utf-8")
    before = old.read_bytes()
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for method, path in [
            ("GET", "/provider-profiles"),
            ("POST", "/provider-profiles"),
            ("GET", "/provider-profiles/old"),
            ("PUT", "/provider-profiles/old"),
            ("DELETE", "/provider-profiles/old"),
        ]:
            assert (await client.request(method, f"/api/v1{path}")).status_code == 404
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["workflow"]["schema_version"] == "1.14"
        for node in response.json()["workflow"]["nodes"]:
            assert "provider_profile_input" not in (node["presentation"] or {})
            binding = (node["presentation"] or {}).get("inline_connection_input")
            if binding:
                assert not {"mode_key", "mode_value", "api_key_env_key"} & set(binding)
        for name in ("load_provider_profiles", "save_provider_profiles", "provider_profile_in_use"):
            assert not hasattr(app.state.store, name)
    assert old.read_bytes() == before


@pytest.mark.asyncio
async def test_env_never_supplies_a_node_credential_even_when_populated(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", KEY_A)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        saved = await save(client, pid, graph(document))
        rejected = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "CREDENTIAL_REQUIRED"
        assert not (tmp_path / ".credentials").exists()
        assert set(config_of(saved)) == {
            "base_url",
            "timeout_seconds",
            "region",
            "credential_ref",
            "model",
            "language",
            "additional_parameters",
        }


def test_removed_profile_registration_and_inline_roles_are_rejected():
    original = NODE_DEFINITIONS["qwen_asr"]
    with pytest.raises(ValueError, match="no longer supported"):
        validate_node_definition(replace(original, presentation={"provider_profile_input": {}}))
    for key in ("mode_key", "mode_value", "api_key_env_key"):
        presentation = {
            "inline_connection_input": {
                **original.presentation["inline_connection_input"],
                key: None,
            }
        }
        with pytest.raises(ValueError, match="invalid inline connection declaration"):
            validate_node_definition(replace(original, presentation=presentation))


@pytest.mark.asyncio
async def test_old_profile_snapshots_are_passive_and_artifacts_remain_downloadable(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        completed = await run(client, app, pid, aids[0], document)
        path = app.state.store.workflow_run_dir(pid, aids[0], completed.id) / "run.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = {
            "unmatched-key": {"id": "other", "schema_version": "retired", "unrecognized": [1, 2]}
        }
        payload["workflow"]["provider_profiles_snapshot"] = raw
        payload["workflow"]["nodes"][0]["config"]["unrecognized_historical"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()
        read = await client.get(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs/{completed.id}"
        )
        assert read.status_code == 200
        assert read.json()["workflow"]["provider_profiles_snapshot"] == raw
        output = next(record for record in completed.nodes if record.artifact)
        assert (await client.get(output.artifact.download_url)).status_code == 200
        assert path.read_bytes() == before
        job_path = app.state.store.project_dir(pid) / "job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["provider_profiles_snapshot"] = raw
        job_path.write_text(json.dumps(job), encoding="utf-8")
        before_job = job_path.read_bytes()
        assert (await client.get(f"/api/v1/projects/{pid}/job")).json()[
            "provider_profiles_snapshot"
        ] == raw
        assert job_path.read_bytes() == before_job


def test_fresh_snapshots_do_not_add_passive_profile_metadata():
    from app.domain.workflow import default_workflow

    snapshot = workflow_snapshot(default_workflow("fresh"))
    assert "provider_profiles_snapshot" not in snapshot.model_dump(mode="json")
    assert normalize({})["credential_ref"] is None


@pytest.mark.asyncio
async def test_dynamic_node_owned_connection_freezes_without_any_profile_api(tmp_path, monkeypatch):
    from app.adapters import qwen_asr
    from app.domain.transcript import Segment, Transcript

    calls = []

    async def transcribe(**kwargs):
        calls.append(kwargs)
        return Transcript(
            project_id=kwargs["project_id"],
            stage="raw",
            language=kwargs["language"],
            segments=[
                Segment(id="s", source_text="extension", language=kwargs["language"]),
            ],
        )

    monkeypatch.setattr(qwen_asr, "transcribe", transcribe)
    original = NODE_DEFINITIONS["qwen_asr"]
    plugin = replace(
        original,
        type="custom_cloud",
        presentation={
            "inline_connection_input": {
                **original.presentation["inline_connection_input"],
                "provider_type": "custom_cloud",
                "label": "Custom cloud",
            }
        },
    )
    validate_node_definition(plugin)
    monkeypatch.setitem(NODE_DEFINITIONS, plugin.type, plugin)
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        payload = graph(document)
        payload["nodes"][1]["type"] = plugin.type
        saved = await save(client, pid, payload)
        saved = await save(client, pid, update_body(saved))
        assert (await run(client, app, pid, aids[0], saved)).status.value == "completed"
        assert calls[0]["connection"].provider_type == plugin.type
        frozen = freeze_workflow_connections(
            app.state.store, workflow_snapshot(app.state.store.load_workflow(pid)), pid
        )
        _, key = inline_connection_from_snapshot(app.state.store, frozen, "cloud", pid)
        assert key == KEY_A
