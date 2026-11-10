import asyncio
import io
import json
from dataclasses import replace
from pathlib import PurePosixPath
from zipfile import ZipFile

import pytest
from app.domain.batch import BatchRecord
from app.domain.node_config import normalize_input_config
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    CapabilityField,
    node_catalog,
    validate_node_definition,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from test_batches import finished, setup, submit


async def save_input(client, pid, document, config, input_type="audio_input"):
    document["nodes"][0]["config"] = config
    document["nodes"][0]["type"] = input_type
    return await client.put(
        f"/api/v1/projects/{pid}/workflow",
        json={
            "expected_revision": document["revision"],
            "nodes": document["nodes"],
            "edges": document["edges"],
        },
    )


@pytest.mark.parametrize(
    "config",
    [
        {"audio_ids": "aud_1"},
        {"audio_ids": None},
        {"audio_ids": [1]},
        {"audio_ids": [True]},
        {"audio_ids": [""]},
        {"audio_ids": ["../a"]},
        {"audio_ids": ["a\\b"]},
        {"audio_ids": ["a", "a"]},
        {"audio_ids": [], "audio_id": "a"},
        {"unknown": []},
        {"audio_ids": [f"aud_{index}" for index in range(10001)]},
    ],
)
def test_input_collection_rejects_invalid_shapes(config):
    with pytest.raises(ValueError):
        normalize_input_config(config)


@pytest.mark.parametrize(
    "config",
    [{}, {"audio_id": "a"}, {"audio_ids": []}, {"audio_ids": ["a"]}, {"audio_ids": ["b", "a"]}],
)
def test_input_collection_normalization_preserves_explicit_order_and_old_modes(config):
    assert normalize_input_config(config) == config


@pytest.mark.asyncio
@pytest.mark.parametrize("input_type", ["audio_input", "resource_alias"])
async def test_collection_empty_single_many_and_order_are_enforced(
    tmp_path, monkeypatch, input_type
):
    if input_type != "audio_input":
        monkeypatch.setitem(
            NODE_DEFINITIONS, input_type, replace(NODE_DEFINITIONS["audio_input"], type=input_type)
        )
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        saved = await save_input(client, pid, document, {"audio_ids": []}, input_type)
        assert saved.status_code == 200, saved.text
        document = saved.json()
        assert document["nodes"][0]["config"] == {"audio_ids": []}
        for endpoint, payload in [
            (
                f"audios/{aids[0]}/workflow-runs",
                {"workflow_revision": document["workflow_revision"]},
            ),
            (
                "batches",
                {
                    "workflow_revision": document["workflow_revision"],
                    "audio_ids": aids,
                    "request_id": "empty",
                },
            ),
        ]:
            rejected = await client.post(f"/api/v1/projects/{pid}/{endpoint}", json=payload)
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["error"]["code"] == "INPUT_AUDIO_COLLECTION_EMPTY"
        saved = await save_input(client, pid, document, {"audio_ids": [aids[1]]}, input_type)
        assert saved.status_code == 200
        document = saved.json()
        mismatch = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["error"]["code"] == "INPUT_AUDIO_MISMATCH"
        single = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[1]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert single.status_code == 202, single.text
        assert single.json()["batch_item_sequence"] is None
        await asyncio.gather(*app.state.jobs.tasks.values())
        ordered = [aids[2], aids[0], aids[1]]
        saved = await save_input(client, pid, document, {"audio_ids": ordered}, input_type)
        assert saved.status_code == 200
        document = saved.json()
        one = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[2]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert one.status_code == 422
        assert one.json()["error"]["code"] == "INPUT_AUDIO_COLLECTION"
        for wrong in (ordered[:1], list(reversed(ordered)), [*ordered, "not-owned"]):
            response = await client.post(
                f"/api/v1/projects/{pid}/batches",
                json={
                    "workflow_revision": document["workflow_revision"],
                    "audio_ids": wrong,
                    "request_id": "wrong",
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "BATCH_AUDIO_COLLECTION_MISMATCH"
        created = await submit(client, pid, ordered, document)
        batch = await finished(app, pid, created["id"])
        assert batch.status == "completed"
        assert [item.audio_id for item in batch.items] == ordered
        assert [item.sequence for item in batch.items] == [1, 2, 3]
        for item in batch.items:
            run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
            assert run.batch_item_sequence == item.sequence
            assert run.workflow.nodes[0].config["audio_ids"] == ordered
            assert run.nodes[-1].artifact.filename == f"audio-copy_{item.sequence:05d}.wav"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [["not-owned"], ["a", "a"], ["../escape"], None])
async def test_invalid_resource_collection_cannot_be_saved(tmp_path, selection):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, document = await setup(client, 1)
        response = await save_input(client, pid, document, {"audio_ids": selection})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_WORKFLOW"
        assert not (app.state.store.project_dir(pid) / "workflow-v3.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_retry_preserves_original_sequences_two_and_four(tmp_path, monkeypatch, legacy):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    from app.application import job_manager

    execute = job_manager.execute_workflow_run
    failing = set()

    async def fail_selected(store, run, event, **kwargs):
        if run.audio_id in failing:
            raise RuntimeError("injected failure")
        return await execute(store, run, event, **kwargs)

    monkeypatch.setattr(job_manager, "execute_workflow_run", fail_selected)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 4)
        saved = await save_input(client, pid, document, {"audio_ids": aids})
        document = saved.json()
        failing.update([aids[1], aids[3]])
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        assert batch.counts["failed"] == 2
        original_path = app.state.batches.store.path(pid, batch.id)
        if legacy:
            payload = json.loads(original_path.read_text(encoding="utf-8"))
            for item in payload["items"]:
                item.pop("sequence")
            original_path.write_text(json.dumps(payload), encoding="utf-8")
        before = original_path.read_bytes()
        failing.clear()
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        if legacy:
            assert response.status_code == 404, response.text
            assert original_path.read_bytes() == before
            return
        assert response.status_code == 202, response.text
        retry = await finished(app, pid, response.json()["id"])
        assert retry.status == "completed"
        assert [item.index for item in retry.items] == [0, 1]
        assert [item.sequence for item in retry.items] == [2, 4]
        assert [item.audio_id for item in retry.items] == [aids[1], aids[3]]
        assert original_path.read_bytes() == before
        for item in retry.items:
            run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
            assert run.batch_item_sequence == item.sequence
            assert run.workflow.nodes[0].config["audio_ids"] == aids
            assert run.nodes[-1].artifact.filename == f"audio-copy_{item.sequence:05d}.wav"
        response = await client.get(f"/api/v1/projects/{pid}/batches/{retry.id}/download")
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert [item["sequence"] for item in manifest["items"]] == [2, 4]
        old_run = (
            app.state.store.workflow_run_dir(pid, retry.items[0].audio_id, retry.items[0].run_id)
            / "run.json"
        )
        run_payload = json.loads(old_run.read_text(encoding="utf-8"))
        run_payload.pop("batch_item_sequence")
        old_run.write_text(json.dumps(run_payload), encoding="utf-8")
        old_before = old_run.read_bytes()
        assert (
            app.state.store.load_workflow_run(
                pid, retry.items[0].audio_id, retry.items[0].run_id
            ).batch_item_sequence
            is None
        )
        assert old_run.read_bytes() == old_before
        run_payload["batch_item_sequence"] = 1
        old_run.write_text(json.dumps(run_payload), encoding="utf-8")
        invalid = await client.get(f"/api/v1/projects/{pid}/batches/{retry.id}/download")
        assert invalid.status_code == 422
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.parametrize("field", ["audio_id", "audio_ids"])
def test_resource_collection_fields_cannot_be_wired_parameters(field):
    definition = NODE_DEFINITIONS["audio_input"]
    # Turn it into a normal optional-input node so the resource parameter
    # prohibition is exercised separately from source-node restrictions.
    invalid = replace(
        definition,
        type="collection_test",
        is_source=False,
        input_ports=("string",),
        required_input_ports=(),
        fields=tuple(
            item.model_copy(update={"input_port": "string"}) if item.key == field else item
            for item in definition.fields
        ),
    )
    with pytest.raises(ValueError, match="resource bindings cannot be connected"):
        validate_node_definition(invalid)


def test_legacy_resource_declaration_gets_explicit_null_collection_in_catalog(monkeypatch):
    definition = replace(
        NODE_DEFINITIONS["audio_input"],
        type="legacy_input",
        presentation={"resource_input": {"resource_type": "audio", "config_key": "audio_id"}},
        fields=(CapabilityField(key="audio_id", type="string"),),
    )
    validate_node_definition(definition)
    monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
    published = next(node for node in node_catalog() if node["type"] == definition.type)
    assert published["presentation"]["resource_input"]["collection_key"] is None
    assert "collection_key" not in definition.presentation["resource_input"]


def test_batch_sequence_collision_and_missing_sequence_are_rejected():
    from app.domain.workflow import default_workflow, workflow_snapshot

    payload = {
        "id": "bat_" + "0" * 32,
        "project_id": "prj_x",
        "workflow_revision": 0,
        "workflow": workflow_snapshot(default_workflow("prj_x")).model_dump(mode="json"),
        "request_id": "x",
        "request_digest": "x",
        "total": 2,
        "counts": {
            "queued": 2,
            "running": 0,
            "completed": 0,
            "partial": 0,
            "failed": 0,
            "cancelled": 0,
            "interrupted": 0,
        },
        "items": [
            {
                "index": 0,
                "audio_id": "a",
                "audio_name": "a.wav",
                "source_language": "ja",
                "sequence": 1,
            },
            {
                "index": 1,
                "audio_id": "b",
                "audio_name": "b.wav",
                "source_language": "ja",
                "sequence": 1,
            },
        ],
    }
    with pytest.raises(ValidationError, match="duplicate batch sequence"):
        BatchRecord.model_validate(payload)
    payload["items"][0]["sequence"] = 2
    payload["items"][1]["sequence"] = 4
    assert [item.sequence for item in BatchRecord.model_validate(payload).items] == [2, 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["x" * 128, "." + "x" * 127])
async def test_zip_keeps_full_128_character_prefix_sequence_and_extension(tmp_path, prefix):
    from test_output_naming import output_graph

    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document.update(output_graph(mode="prefix_sequence", stem=prefix))
        saved = await save_input(client, pid, document, {"audio_ids": aids})
        assert saved.status_code == 200, saved.text
        created = await submit(client, pid, aids, saved.json())
        batch = await finished(app, pid, created["id"])
        assert batch.status == "completed"
        response = await client.get(f"/api/v1/projects/{pid}/batches/{batch.id}/download")
        assert response.status_code == 200, response.text
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            outputs = {output["node_id"]: output for output in manifest["items"][0]["outputs"]}
            for node_id, extension in (("audio", ".wav"), ("text", ".srt")):
                output = outputs[node_id]
                expected = f"{prefix}_00001{extension}"
                assert output["artifact"]["filename"] == expected
                assert PurePosixPath(output["path"]).name == expected
                assert archive.read(output["path"])
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()
