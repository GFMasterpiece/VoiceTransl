"""Output prefixes use frozen batch sequences without changing old exact names."""

import asyncio
import io
import json
from zipfile import ZipFile

import pytest
from app.application.output_naming import output_filename
from app.application.workflow_runner import execute_workflow_run
from app.domain.node_config import AudioOutputConfig, TextOutputConfig
from app.domain.node_execution import NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS
from app.domain.workflow import (
    WorkflowDocument,
    WorkflowNode,
    WorkflowNodeRecord,
    WorkflowRun,
    workflow_snapshot,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from test_batches import finished, setup, submit
from test_node_parameters import edge, run_graph
from test_workflow import create_project, save_graph, tiny_wav


def output_graph(mode=None, stem="sample", *, wired=False, format_name="srt"):
    config = {"filename_stem": stem}
    if mode is not None:
        config["naming_mode"] = mode
    graph = {
        "nodes": [
            dict(id="input", type="audio_input", config={}),
            dict(id="asr", type="asr", config={"language": "ja"}),
            dict(id="audio", type="audio_output", config=dict(config)),
            dict(id="text", type="text_output", config={**config, "format": format_name}),
        ],
        "edges": [
            edge("input-audio", "input", "audio", "audio", "audio"),
            edge("input-asr", "input", "audio", "asr", "audio"),
            edge("asr-text", "asr", "transcript", "text", "transcript"),
        ],
    }
    if wired:
        graph["nodes"].append(dict(id="prefix", type="string_value", config={"value": stem}))
        for target in ("audio", "text"):
            graph["edges"].append(
                edge(f"prefix-{target}", "prefix", "string", target, "filename_stem")
            )
            next(item for item in graph["nodes"] if item["id"] == target)["config"][
                "filename_stem"
            ] = "manual"
    return graph


@pytest.mark.parametrize("model", [AudioOutputConfig, TextOutputConfig])
def test_missing_mode_reads_exact_while_catalog_new_node_default_is_prefix(model):
    assert model().naming_mode == "exact"
    definition = NODE_DEFINITIONS["text_output" if model is TextOutputConfig else "audio_output"]
    field = next(item for item in definition.fields if item.key == "naming_mode")
    assert field.default == "exact"
    assert field.creation_default == "prefix_sequence"
    assert field.type == "select"
    assert [option["value"] for option in field.options] == ["exact", "prefix_sequence"]
    assert field.input_port is None
    assert "naming_mode" not in definition.input_ports
    assert definition.normalize_config({})["naming_mode"] == "exact"


@pytest.mark.parametrize("mode", [None, "exact"])
def test_legacy_exact_batch_needs_no_sequence(mode):
    config = {"filename_stem": "unchanged"}
    if mode:
        config["naming_mode"] = mode
    assert (
        output_filename(AudioOutputConfig(**config), ".wav", batch_id="legacy") == "unchanged.wav"
    )


@pytest.mark.parametrize("sequence", [1, 2, 87, 10000, 123456])
def test_sequence_is_stable_and_at_least_five_digits(sequence):
    config = AudioOutputConfig(filename_stem="prefix", naming_mode="prefix_sequence")
    assert (
        output_filename(config, ".flac", batch_id="batch", batch_item_sequence=sequence)
        == f"prefix_{sequence:05d}.flac"
    )
    assert config.filename_stem == "prefix"
    assert output_filename(config, ".flac", batch_item_sequence=sequence) == "prefix_00001.flac"


@pytest.mark.parametrize("sequence", [None, 0, -1, True, "1", 1.5])
def test_numbered_batch_missing_valid_sequence_has_explicit_error(sequence):
    config = AudioOutputConfig(filename_stem="prefix", naming_mode="prefix_sequence")
    with pytest.raises(NodeExecutionError) as error:
        output_filename(config, ".wav", batch_id="batch", batch_item_sequence=sequence)
    assert error.value.code == "OUTPUT_SEQUENCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "stem", ["../unsafe", "a/b", "a\\b", "CON", "NUL.txt", "a\x00b", "a.", " a", "x" * 129]
)
@pytest.mark.parametrize("mode", ["exact", "prefix_sequence"])
def test_unsafe_prefix_rejected_before_number_can_hide_it(stem, mode):
    for model in (AudioOutputConfig, TextOutputConfig):
        with pytest.raises(ValidationError):
            model(filename_stem=stem, naming_mode=mode)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected", [(None, "sample"), ("exact", "sample"), ("prefix_sequence", "sample_00001")]
)
async def test_audio_and_text_exact_or_numbered_suffixes_and_parameter_records(
    tmp_path, mode, expected
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = output_graph(mode, wired=True, format_name="vtt")
        document = await save_graph(client, pid, 0, graph)
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed", run
        for target, extension in (("audio", ".wav"), ("text", ".vtt")):
            record = records[target]
            assert record["artifact"]["filename"] == expected + extension
            assert record["config"]["filename_stem"] == "manual"
            assert record["effective_config"]["filename_stem"] == "sample"
            assert record["effective_config"]["naming_mode"] == (mode or "exact")
            assert record["parameter_sources"]["filename_stem"]["source_node_id"] == "prefix"
            assert record["parameter_sources"]["naming_mode"] == {"kind": "config"}
        assert (
            await client.get(records["audio"]["artifact"]["download_url"])
        ).content == tiny_wav()
        assert (await client.get(records["text"]["artifact"]["download_url"])).text.startswith(
            "WEBVTT"
        )


@pytest.mark.asyncio
async def test_maximum_128_character_prefix_writes_audio_and_text_without_truncation(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    stem = "x" * 128
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        document = await save_graph(client, pid, 0, output_graph("prefix_sequence", stem))
        run, records = await run_graph(app, client, pid, aid, document)
        assert run["status"] == "completed", run
        assert records["audio"]["artifact"]["filename"] == stem + "_00001.wav"
        assert records["text"]["artifact"]["filename"] == stem + "_00001.srt"
        for target in ("audio", "text"):
            assert records[target]["effective_config"]["filename_stem"] == stem
            assert (
                await client.get(records[target]["artifact"]["download_url"])
            ).status_code == 200


@pytest.mark.asyncio
async def test_missing_sequence_fails_both_numbered_outputs_without_artifacts(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
    graph = output_graph("prefix_sequence")
    document = WorkflowDocument(
        project_id=pid,
        nodes=[WorkflowNode.model_validate(item) for item in graph["nodes"]],
        edges=graph["edges"],
    )
    run = WorkflowRun(
        id="wfr_" + "a" * 32,
        project_id=pid,
        audio_id=aid,
        batch_id="bat_" + "b" * 32,
        workflow_revision=0,
        workflow=workflow_snapshot(document),
        nodes=[
            WorkflowNodeRecord(node_id=node.id, node_type=node.type, config=node.config)
            for node in document.nodes
        ],
    )
    app.state.store.create_workflow_run(run)
    await execute_workflow_run(app.state.store, run, asyncio.Event())
    assert run.status.value == "failed"
    for target in ("audio", "text"):
        record = next(item for item in run.nodes if item.node_id == target)
        assert record.error.type == "OUTPUT_SEQUENCE_UNAVAILABLE"
        assert record.artifact is None
    assert all(
        not list((app.state.store.workflow_run_dir(pid, aid, run.id) / "nodes" / target).glob("*"))
        for target in ("audio", "text")
    )


@pytest.mark.asyncio
async def test_old_persisted_graph_and_run_read_without_rewriting_or_renaming(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = output_graph()
        document = WorkflowDocument(project_id=pid, nodes=graph["nodes"], edges=graph["edges"])
        workflow_path = app.state.store.project_dir(pid) / "workflow.json"
        workflow_path.write_text(document.model_dump_json(), encoding="utf-8")
        old_workflow = workflow_path.read_bytes()
        loaded = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
        assert all("naming_mode" not in node["config"] for node in loaded["nodes"])
        run, records = await run_graph(app, client, pid, aid, loaded)
        assert records["audio"]["artifact"]["filename"] == "sample.wav"
        assert records["text"]["artifact"]["filename"] == "sample.srt"
        assert workflow_path.read_bytes() == old_workflow
    run_path = app.state.store.workflow_run_dir(pid, aid, run["id"]) / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload.pop("batch_item_sequence", None)
    for record in payload["nodes"]:
        record["config"].pop("naming_mode", None)
        if record["effective_config"]:
            record["effective_config"].pop("naming_mode", None)
        if record["parameter_sources"]:
            record["parameter_sources"].pop("naming_mode", None)
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    old_run = run_path.read_bytes()
    restarted = create_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=restarted), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{run['id']}"
        )
        assert response.status_code == 200, response.text
        assert response.json()["batch_item_sequence"] is None
        artifacts = [item["artifact"] for item in response.json()["nodes"] if item["artifact"]]
        assert {item["filename"] for item in artifacts} == {
            "sample.wav",
            "sample.srt",
            "transcript.json",
        }
        assert (
            await client.get(records["audio"]["artifact"]["download_url"])
        ).content == tiny_wav()
    assert run_path.read_bytes() == old_run
    assert workflow_path.read_bytes() == old_workflow


@pytest.mark.asyncio
async def test_batch_completion_order_does_not_change_numbering_and_retry_keeps_original_sequence(
    tmp_path, monkeypatch
):
    from app.application import job_manager

    monkeypatch.setenv("VOICETRANSL_MAX_CONCURRENT", "2")
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    original = job_manager.execute_workflow_run
    second_done = asyncio.Event()
    completions = []
    aids = []
    fail_last = True

    async def controlled(store, run, cancel_event, **kwargs):
        if run.audio_id == aids[-1] and fail_last:
            raise RuntimeError("injected retryable failure")
        if run.audio_id == aids[0]:
            await second_done.wait()
        result = await original(store, run, cancel_event, **kwargs)
        completions.append(run.audio_id)
        if run.audio_id == aids[1]:
            second_done.set()
        return result

    monkeypatch.setattr(job_manager, "execute_workflow_run", controlled)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pid, aids, _default = await setup(client, 3)
            graph = output_graph("prefix_sequence", "wired-prefix", wired=True)
            graph["nodes"][0]["config"] = {"audio_ids": aids}
            document = await save_graph(client, pid, 0, graph)
            created = await submit(client, pid, aids, document)
            batch = await finished(app, pid, created["id"])
            assert batch.status == "partial"
            assert completions.index(aids[1]) < completions.index(aids[0])
            assert [item.sequence for item in batch.items] == [1, 2, 3]
            for item in batch.items[:2]:
                run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
                assert run.batch_item_sequence == item.sequence
                records = {record.node_id: record for record in run.nodes}
                assert records["audio"].artifact.filename == f"wired-prefix_{item.sequence:05d}.wav"
                assert records["text"].artifact.filename == f"wired-prefix_{item.sequence:05d}.srt"
                assert records["audio"].effective_config["filename_stem"] == "wired-prefix"
            failed_run_id = batch.items[-1].run_id
            fail_last = False
            retried = await client.post(
                f"/api/v1/projects/{pid}/batches/{batch.id}/retry",
                json={"request_id": "retry-stable-number"},
            )
            assert retried.status_code == 202, retried.text
            retry = await finished(app, pid, retried.json()["id"])
            assert retry.status == "completed"
            assert retry.total == 1
            assert retry.items[0].index == 0
            assert retry.items[0].sequence == 3
            assert retry.items[0].run_id != failed_run_id
            run = app.state.store.load_workflow_run(pid, aids[-1], retry.items[0].run_id)
            assert run.batch_item_sequence == 3
            actual = {record.artifact.filename for record in run.nodes if record.artifact}
            assert {"wired-prefix_00003.wav", "wired-prefix_00003.srt"} <= actual
            download = await client.get(f"/api/v1/projects/{pid}/batches/{retry.id}/download")
            assert download.status_code == 200
            with ZipFile(io.BytesIO(download.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                outputs = manifest["items"][0]["outputs"]
                assert {output["artifact"]["filename"] for output in outputs} == actual
                for output in outputs:
                    assert output["path"].endswith(output["artifact"]["filename"])
    finally:
        await app.state.batches.shutdown()
        await app.state.jobs.shutdown()
