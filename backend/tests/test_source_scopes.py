import asyncio
from dataclasses import replace

import pytest
from app.domain.node_execution import NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import finished, setup
from test_llm_translate_node import setup as llm_setup
from test_llm_translate_node import start as llm_start
from test_llm_translate_node import translator as translator
from test_workflow import save_graph


@pytest.mark.asyncio
async def test_custom_source_resource_aliases_freeze_and_obey_continuous_order(
    tmp_path, monkeypatch
):
    from app.domain.node_config import normalize_input_config
    from app.domain.node_registry import validate_node_definition

    aliases = {"group_id": "pack", "audio_id": "clip", "audio_ids": "clips"}
    reverse = {value: key for key, value in aliases.items()}

    def normalize(config):
        values = normalize_input_config({reverse[key]: value for key, value in config.items()})
        return {aliases[key]: value for key, value in values.items()}

    original = NODE_DEFINITIONS["audio_input"]
    definition = replace(
        original,
        type="alias_source",
        fields=tuple(
            field.model_copy(update={"key": aliases[field.key]}) for field in original.fields
        ),
        normalize_config=normalize,
        validate_config=normalize,
        presentation={
            "resource_input": {
                "resource_type": "audio",
                "config_key": "clip",
                "collection_key": "clips",
                "group_key": "pack",
            }
        },
    )
    validate_node_definition(definition)
    monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.jobs.set_max_concurrent(2)
    output = NODE_DEFINITIONS["audio_output"]
    called = []
    active = False

    async def execute(ctx):
        nonlocal active
        assert not active
        active = True
        called.append(ctx.run.audio_id)
        try:
            await asyncio.sleep(0.01)
            return await output.execute(ctx)
        finally:
            active = False

    monkeypatch.setitem(NODE_DEFINITIONS, "audio_output", replace(output, execute=execute))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 2)
        group = await grouped(client, pid, list(reversed(aids)), "continuous")
        document = await canvas(client, pid, document, [group])
        document["nodes"][0].update(type="alias_source", config={"pack": group["id"]})
        document = await save_graph(
            client,
            pid,
            document["revision"],
            {"nodes": document["nodes"], "edges": document["edges"]},
        )
        response = await run_target(client, pid, document, {"kind": "source", "node_id": "s0"})
        assert response.status_code == 202, response.text
        result = await finished(app, pid, response.json()["id"])
        assert result.status == "completed" and called == list(reversed(aids))
        assert [item.source_sequence for item in result.items] == [1, 2]
        assert result.workflow.group_snapshots[group["id"]]["mode"] == "continuous"
        assert result.workflow.nodes[0].config == {"pack": group["id"]}
        history = await client.get(f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs")
        assert history.json()["items"][0]["source_group_name"] == group["name"]
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


def test_parameter_edges_propagate_sources_and_reject_cross_source_merges(monkeypatch):
    from app.domain.workflow import (
        WorkflowEdge,
        WorkflowNode,
        WorkflowSnapshot,
        validate_workflow_graph,
    )
    from app.domain.workflow_scope import plan_source_scope

    definition = replace(
        NODE_DEFINITIONS["preprocess"],
        type="scope_probe",
        output_ports=("string",),
        fields=(),
        normalize_config=lambda value: {},
        validate_config=lambda value: {},
    )
    monkeypatch.setitem(NODE_DEFINITIONS, "scope_probe", definition)
    nodes = [
        WorkflowNode(id="s0", type="audio_input"),
        WorkflowNode(id="s1", type="audio_input"),
        WorkflowNode(id="p0", type="scope_probe"),
        WorkflowNode(id="p1", type="scope_probe"),
        WorkflowNode(
            id="join",
            type="text_join",
            config={"items": [{"id": "a", "value": ""}, {"id": "b", "value": ""}]},
        ),
        WorkflowNode(id="out", type="audio_output"),
    ]
    edges = [
        WorkflowEdge(id="a", source="s0", source_port="audio", target="p0", target_port="audio"),
        WorkflowEdge(id="b", source="s1", source_port="audio", target="p1", target_port="audio"),
        WorkflowEdge(
            id="c", source="p0", source_port="string", target="join", target_port="text_a"
        ),
        WorkflowEdge(
            id="d", source="p1", source_port="string", target="join", target_port="text_b"
        ),
        WorkflowEdge(
            id="e", source="join", source_port="string", target="out", target_port="filename_stem"
        ),
        WorkflowEdge(id="f", source="s0", source_port="audio", target="out", target_port="audio"),
    ]
    issues = validate_workflow_graph(nodes, edges)
    assert any(issue.code == "SOURCE_SCOPE_MERGE" and issue.node_id == "join" for issue in issues)
    with pytest.raises(ValueError, match="SOURCE_SCOPE_MERGE"):
        plan_source_scope(
            WorkflowSnapshot(revision=0, workflow_revision=0, nodes=nodes, edges=edges), "s0"
        )
    nodes = [node for node in nodes if node.id != "s1"]
    edges[1] = edges[1].model_copy(update={"source": "s0"})
    assert not validate_workflow_graph(nodes, edges)


@pytest.mark.asyncio
async def test_unbound_new_group_draft_never_falls_back_to_current_audio(tmp_path):
    from app.domain.node_config import normalize_input_config

    from test_workflow import create_project, get_default_workflow

    app = create_app(tmp_path)
    assert normalize_input_config({}) == {}
    assert normalize_input_config({"group_id": ""}) == {"group_id": ""}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aid = await create_project(c)
        document = await get_default_workflow(c, pid, legacy=False)
        assert document["nodes"][0]["config"] == {"group_id": ""}
        saved = await save_graph(
            c, pid, document["revision"], {"nodes": document["nodes"], "edges": document["edges"]}
        )
        r = await run_target(c, pid, saved, {"kind": "canvas"}, context_audio_id=aid)
        assert r.status_code == 422 and r.json()["error"]["code"] == "INPUT_GROUP_REQUIRED"
        r = await c.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": saved["workflow_revision"]},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "INPUT_GROUP_REQUIRED"
        assert not app.state.jobs.tasks and not app.state.jobs.reservations


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_first", [False, True])
async def test_source_identity_normalizes_inferred_and_explicit_source(tmp_path, explicit_first):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 1)

        async def submit(extra):
            r = await c.post(
                f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
                json={"workflow_revision": doc["workflow_revision"], **extra},
            )
            assert r.status_code == 202, r.text
            await asyncio.gather(*app.state.jobs.tasks.values())
            return app.state.store.load_workflow_run(pid, aids[0], r.json()["id"])

        source = await submit({"source_node_id": "input"} if explicit_first else {})
        source_path = app.state.store.workflow_run_dir(pid, aids[0], source.id) / "run.json"
        before = source_path.read_bytes()
        result = await submit(
            {
                **({} if explicit_first else {"source_node_id": "input"}),
                "request_id": "rerun",
                "rerun": {"source_run_id": source.id, "from_node_id": "output"},
            }
        )
        assert result.status.value == "completed", result.error
        assert next(x for x in result.nodes if x.node_id == "input").execution_kind == "reused"
        history = await c.get(f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs")
        item = next(x for x in history.json()["items"] if x["id"] == source.id)
        assert item["source_node_id"] == "input" and "source_group_name" not in item
        assert source_path.read_bytes() == before
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_blocked_propagation_storage_failure_releases_all_reservations(tmp_path, monkeypatch):
    from collections import deque

    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 3)
        group = await grouped(c, pid, aids, "continuous")
        doc = await canvas(c, pid, doc, [group])
        app.state.batches.stopping = True
        # Submission remains authorized while dispatch is deliberately paused.
        monkeypatch.setattr(app.state.batches, "_dispatch", lambda: None)
        app.state.batches.stopping = False
        r = await run_target(c, pid, doc, {"kind": "canvas"})
        assert r.status_code == 202, r.text
        manager = app.state.batches
        batch = manager.get(pid, r.json()["id"])
        batch.items[0].status = "failed"
        manager._release(batch, batch.items[0])
        manager.pending = deque([(batch.id, 1), (batch.id, 2)])
        saves = []
        save = manager.store.save

        def unavailable(record):
            saves.append(record.id)
            raise OSError("temporary storage failure")

        monkeypatch.setattr(manager.store, "save", unavailable)
        from app.application.batch_manager import BatchManager

        BatchManager._dispatch(manager)
        assert [x.status for x in batch.items] == ["failed", "blocked", "blocked"]
        assert len(saves) == 1
        assert not manager.pending and not manager.workers and not app.state.jobs.reservations
        monkeypatch.setattr(manager.store, "save", save)
        # A subsequent explicit submission reconciles the terminal record, never replays work.
        replay = await run_target(c, pid, doc, {"kind": "canvas"})
        assert replay.status_code == 202 and replay.json()["id"] == batch.id
        assert manager.store.load(pid, batch.id).counts["blocked"] == 2
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


def test_group_snapshots_validate_version_and_identifier():
    from app.domain.workflow import WorkflowSnapshot
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowSnapshot(
            revision=0,
            workflow_revision=0,
            group_snapshots={"grp_" + "a" * 32: {"schema_version": "999", "id": "unmatched"}},
        )


async def grouped(client, pid, aids, mode="independent", name="group"):
    r = await client.post(
        f"/api/v1/projects/{pid}/audio-groups",
        json={"name": name, "audio_ids": aids, "background": "frozen background", "mode": mode},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["independent", "shared", "continuous"])
async def test_single_group_requires_batch_for_multiple_members(tmp_path, mode):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 2)
        group = await grouped(c, pid, aids, mode)
        doc = await canvas(c, pid, doc, [group])
        r = await c.post(
            f"/api/v1/projects/{pid}/audios/{aids[1]}/workflow-runs",
            json={"workflow_revision": doc["workflow_revision"], "source_node_id": "s0"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "INPUT_AUDIO_COLLECTION"
        assert app.state.store.list_workflow_runs(pid, aids[1], limit=100, offset=0)[1] == 0
        batch = await app.state.batches.create(
            pid, doc["workflow_revision"], None, "cancel-before-worker", target={"kind": "canvas"}
        )
        # No yield between dispatcher creation and cancellation: workers have not entered.
        await app.state.batches.cancel(pid, batch.id)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not app.state.batches.claimed_audio and not app.state.batches.claimed_sources
        assert not app.state.jobs.reservations
        r = await run_target(c, pid, doc, {"kind": "canvas"}, rid="after-cancel")
        assert r.status_code == 202, r.text
        result = await finished(app, pid, r.json()["id"])
        assert result.status == "completed"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


async def canvas(client, pid, doc, groups):
    nodes = []
    edges = []
    for i, g in enumerate(groups):
        nodes.extend(
            [
                {
                    "id": f"s{i}",
                    "type": "audio_input",
                    "config": {"group_id": g["id"]},
                    "position": {"x": 0, "y": i * 300},
                },
                {
                    "id": f"o{i}",
                    "type": "audio_output",
                    "config": {},
                    "position": {"x": 700, "y": i * 300},
                },
            ]
        )
        edges.append(
            {
                "id": f"e{i}",
                "source": f"s{i}",
                "source_port": "audio",
                "target": f"o{i}",
                "target_port": "audio",
            }
        )
    return await save_graph(client, pid, doc["revision"], {"nodes": nodes, "edges": edges})


async def run_target(client, pid, doc, target, rid="canvas", **extra):
    return await client.post(
        f"/api/v1/projects/{pid}/batches",
        json={
            "workflow_revision": doc["workflow_revision"],
            "request_id": rid,
            "target": target,
            **extra,
        },
    )


@pytest.mark.asyncio
async def test_canvas_duplicate_audio_serializes_and_scope_isolates(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.jobs.set_max_concurrent(2)
    original = NODE_DEFINITIONS["audio_output"]
    active = set()
    calls = []
    peak = 0

    async def execute(ctx):
        nonlocal peak
        assert ctx.run.audio_id not in active
        assert app.state.jobs.reservations[ctx.run.project_id, ctx.run.audio_id] == ctx.run.batch_id
        active.add(ctx.run.audio_id)
        peak = max(peak, len(active))
        calls.append((ctx.run.source_node_id, ctx.run.audio_id))
        await asyncio.sleep(0.02)
        try:
            return await original.execute(ctx)
        finally:
            active.remove(ctx.run.audio_id)

    monkeypatch.setitem(NODE_DEFINITIONS, "audio_output", replace(original, execute=execute))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 2)
        groups = [
            await grouped(c, pid, [aids[0]], name="First source"),
            await grouped(c, pid, aids, name="Second source"),
        ]
        doc = await canvas(c, pid, doc, groups)
        missing = await c.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": doc["workflow_revision"]},
        )
        assert (
            missing.status_code == 422 and missing.json()["error"]["code"] == "SOURCE_NODE_REQUIRED"
        )
        response = await run_target(c, pid, doc, {"kind": "canvas"})
        assert response.status_code == 202, response.text
        batch = await finished(app, pid, response.json()["id"])
        assert batch.status == "completed"
        assert [x.source_sequence for x in batch.items] == [1, 1, 2]
        assert [x.sequence for x in batch.items] == [1, 2, 3]
        assert len(calls) == 3 and len({x.run_id for x in batch.items}) == 3
        assert peak == 2
        paths = [
            app.state.store.workflow_run_dir(pid, item.audio_id, item.run_id) / "run.json"
            for item in batch.items
        ]
        originals = [path.read_bytes() for path in paths]
        for group in groups:
            changed = await c.put(
                f"/api/v1/projects/{pid}/audio-groups/{group['id']}",
                json={
                    "expected_revision": 1,
                    "name": "Live new name",
                    "audio_ids": group["audio_ids"],
                    "background": "new",
                    "mode": "independent",
                },
            )
            assert changed.status_code == 200, changed.text
        history = await c.get(f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs")
        assert {(x["source_node_id"], x["source_group_name"]) for x in history.json()["items"]} == {
            ("s0", "First source"),
            ("s1", "Second source"),
        }
        assert [path.read_bytes() for path in paths] == originals
        for item in batch.items:
            run = app.state.store.load_workflow_run(pid, item.audio_id, item.run_id)
            assert run.source_node_id == item.source_node_id
            assert (
                next(
                    x
                    for x in run.nodes
                    if x.node_id == ("o1" if item.source_node_id == "s0" else "o0")
                ).status.value
                == "skipped"
            )
        assert not app.state.jobs.reservations
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,flag", [("partial", "include_partial"), ("cancelled", "include_cancelled")]
)
async def test_retry_requires_explicit_unfinished_predecessor(tmp_path, monkeypatch, status, flag):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    original = NODE_DEFINITIONS["audio_output"]
    calls = []
    fail = True

    async def execute(ctx):
        calls.append(ctx.run.audio_id)
        if fail:
            raise NodeExecutionError("TEST_FAILED", "failed")
        return await original.execute(ctx)

    monkeypatch.setitem(NODE_DEFINITIONS, "audio_output", replace(original, execute=execute))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 2)
        g = await grouped(c, pid, aids, "continuous")
        doc = await canvas(c, pid, doc, [g])
        response = await run_target(c, pid, doc, {"kind": "source", "node_id": "s0"})
        batch = await finished(app, pid, response.json()["id"])
        batch.items[0].status = status
        batch.refresh_counts()
        app.state.batches.store.save(batch)
        fail = False
        r = await c.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "no-opt-in"}
        )
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "BATCH_DEPENDENCY_NOT_COMPLETED"
        assert r.json()["error"]["details"]["predecessor_sequence"] == 1
        assert len(calls) == 1 and not app.state.jobs.reservations
        r = await c.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry",
            json={"request_id": "explicit", flag: True},
        )
        assert r.status_code == 202, r.text
        done = await finished(app, pid, r.json()["id"])
        assert [x.status for x in done.items] == ["completed", "completed"]
        assert len(calls) == 3
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_changed_group_background_reuses_asr_but_invalidates_llm(tmp_path, translator):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aid, doc = await llm_setup(c)
        group = await grouped(c, pid, [aid])
        doc["nodes"][0]["config"] = {"group_id": group["id"]}
        doc["nodes"].append(
            {"id": "info", "type": "group_info", "config": {"group_id": group["id"]}}
        )
        doc["edges"].append(
            {
                "id": "background",
                "source": "info",
                "source_port": "background",
                "target": "llm1",
                "target_port": "context",
            }
        )
        doc = await save_graph(
            c, pid, doc["revision"], {"nodes": doc["nodes"], "edges": doc["edges"]}
        )
        source = await llm_start(
            c,
            app,
            pid,
            aid,
            doc,
            request_id="initial-version",
            expected_group_revisions={group["id"]: 1},
        )
        assert source.status.value == "completed", source.error
        assert translator["calls"][-1]["context"] == "frozen background"
        r = await c.put(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}",
            json={
                "expected_revision": 1,
                "name": "same",
                "audio_ids": [aid],
                "background": "new background",
                "mode": "independent",
            },
        )
        assert r.status_code == 200, r.text
        conflict = await c.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={
                "workflow_revision": doc["workflow_revision"],
                "request_id": "initial-version",
                "expected_group_revisions": {group["id"]: 2},
            },
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["error"]["code"] == "WORKFLOW_REQUEST_CONFLICT"
        )
        body = {
            "workflow_revision": doc["workflow_revision"],
            "request_id": "bad-cache",
            "rerun": {"source_run_id": source.id, "from_node_id": "output"},
        }
        p = await c.post(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/plan", json=body)
        assert p.status_code == 200, p.text
        assert not p.json()["valid"]
        assert p.json()["issues"][0]["node_id"] == "llm1"
        assert len(translator["calls"]) == 1
        rerun = await llm_start(
            c,
            app,
            pid,
            aid,
            doc,
            request_id="changed-background",
            rerun={"source_run_id": source.id, "from_node_id": "llm1"},
        )
        assert rerun.status.value == "completed", rerun.error
        assert next(x for x in rerun.nodes if x.node_id == "asr").execution_kind == "reused"
        assert translator["calls"][-1]["context"] == "new background"
        assert len(translator["calls"]) == 2
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_continuous_failure_blocks_only_own_source_and_retry_frozen(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    original = NODE_DEFINITIONS["audio_output"]
    calls = []
    fail = True

    async def execute(ctx):
        calls.append((ctx.run.source_node_id, ctx.run.audio_id))
        if fail and ctx.run.source_node_id == "s0" and ctx.run.audio_id == aids[1]:
            raise NodeExecutionError("TEST_FAILED", "failed")
        return await original.execute(ctx)

    monkeypatch.setitem(NODE_DEFINITIONS, "audio_output", replace(original, execute=execute))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pid, aids, doc = await setup(c, 4)
        groups = [await grouped(c, pid, aids[:3], "continuous"), await grouped(c, pid, aids[3:])]
        doc = await canvas(c, pid, doc, groups)
        response = await run_target(c, pid, doc, {"kind": "canvas"})
        assert response.status_code == 202, response.text
        batch = await finished(app, pid, response.json()["id"])
        assert [x.status for x in batch.items] == ["completed", "failed", "blocked", "completed"]
        assert len(calls) == 3
        fail = False
        r = await c.put(
            f"/api/v1/projects/{pid}/audio-groups/{groups[0]['id']}",
            json={
                "expected_revision": 1,
                "name": "changed",
                "audio_ids": [aids[2]],
                "background": "new",
                "mode": "independent",
            },
        )
        assert r.status_code == 200, r.text
        retry = await c.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert retry.status_code == 202, retry.text
        done = await finished(app, pid, retry.json()["id"])
        assert [x.audio_id for x in done.items] == aids[1:3]
        assert [x.source_sequence for x in done.items] == [2, 3]
        assert done.completed_predecessors["s0"] == [1]
        assert [x.status for x in done.items] == ["completed", "completed"]
        assert done.workflow.group_snapshots[groups[0]["id"]]["background"] == "frozen background"
        assert len(calls) == 5 and not app.state.jobs.reservations
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()
