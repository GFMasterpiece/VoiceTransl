import asyncio
import json
from dataclasses import replace

import pytest
from app.application.workflow_profiles import (
    ProfileSnapshotError,
    freeze_workflow_profiles,
    profile_from_snapshot,
)
from app.domain.node_execution import NodeExecutionCancelled
from app.domain.node_registry import (
    NODE_DEFINITIONS,
    CapabilityField,
    validate_node_definition,
)
from app.domain.provider_profile import ProviderProfile
from app.domain.schema import SchemaVersionError
from app.domain.workflow import WorkflowDocument, default_workflow, workflow_snapshot
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_batches import finished, setup, submit

PROFILE_BINDING = {
    "config_key": "provider_profile_id",
    "provider_type": "test_cloud",
    "label": "Test Cloud",
    "defaults": {
        "base_url": "https://original.example/api",
        "api_key_env": "TEST_CLOUD_KEY",
        "default_timeout": 120,
    },
}


def connection(**changes):
    return ProviderProfile(
        id="cloud", provider_type="test_cloud", display_name="Cloud", **PROFILE_BINDING["defaults"]
    ).model_copy(update=changes)


@pytest.fixture
def profile_node(monkeypatch):
    state = {"observed": [], "failing": set(), "cancel_detail": False, "cancel_plain": False}
    original = NODE_DEFINITIONS["asr"]

    async def execute(context):
        profile = profile_from_snapshot(
            context.run.workflow, context.node.config["provider_profile_id"], "test_cloud"
        )
        state["observed"].append(profile)
        if context.run.audio_id in state["failing"]:
            raise RuntimeError("injected failure")
        if state["cancel_detail"]:
            raise NodeExecutionCancelled("CLOUD_CANCEL_UNKNOWN", "本地已停止，远端取消状态未知")
        if state["cancel_plain"]:
            raise NodeExecutionCancelled()
        node = context.node.model_copy(update={"config": {"provider": "mock", "language": "ja"}})
        return await original.execute(replace(context, node=node))

    definition = replace(
        original,
        type="profile_test",
        execute=execute,
        validate_config=lambda value: value,
        normalize_config=None,
        fields=(*original.fields, CapabilityField(key="provider_profile_id", type="string")),
        presentation={"provider_profile_input": PROFILE_BINDING},
    )
    validate_node_definition(definition)
    monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
    state["definition"] = definition
    return state


def profile_graph(document, profile_id="cloud"):
    graph = dict(document)
    graph["nodes"] = [
        document["nodes"][0],
        {"id": "cloud", "type": "profile_test", "config": {"provider_profile_id": profile_id}},
        {
            "id": "output",
            "type": "text_output",
            "config": {"format": "txt", "filename_stem": "out"},
        },
    ]
    graph["edges"] = [
        {
            "id": "in-cloud",
            "source": "input",
            "source_port": "audio",
            "target": "cloud",
            "target_port": "audio",
        },
        {
            "id": "cloud-out",
            "source": "cloud",
            "source_port": "transcript",
            "target": "output",
            "target_port": "transcript",
        },
    ]
    return graph


async def save_profile_graph(client, pid, document, profile_id="cloud"):
    graph = profile_graph(document, profile_id)
    response = await client.put(
        f"/api/v1/projects/{pid}/workflow",
        json={
            "expected_revision": document["revision"],
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_single_submission_freezes_profiles_and_never_serializes_env_value(
    tmp_path, profile_node, monkeypatch
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    monkeypatch.setenv("TEST_CLOUD_KEY", "SECRET_SENTINEL_MUST_NOT_PERSIST")
    app.state.store.save_provider_profiles([connection()])
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202, response.text
        frozen = response.json()["workflow"]["provider_profiles_snapshot"]["cloud"]
        assert frozen["base_url"] == "https://original.example/api"
        assert frozen["api_key_env"] == "TEST_CLOUD_KEY"
        assert "SECRET_SENTINEL" not in response.text
        app.state.store.save_provider_profiles(
            [connection(base_url="https://changed.example", enabled=False)]
        )
        app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert [profile.base_url for profile in profile_node["observed"]] == [
            "https://original.example/api"
        ]
        for path in tmp_path.rglob("*.json"):
            assert b"SECRET_SENTINEL" not in path.read_bytes()
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_batch_freezes_once_retry_and_deleted_profile_use_original_snapshot(
    tmp_path, profile_node, monkeypatch
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.store.save_provider_profiles([connection()])
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        document = await save_profile_graph(client, pid, document)
        profile_node["failing"].add(aids[1])
        reads = 0
        original = app.state.store.load_provider_profiles

        def counted():
            nonlocal reads
            reads += 1
            return original()

        monkeypatch.setattr(app.state.store, "load_provider_profiles", counted)
        created = await submit(client, pid, aids, document)
        assert reads == 1
        app.state.store.save_provider_profiles(
            [connection(base_url="https://changed.example", enabled=False)]
        )
        duplicate = await submit(client, pid, aids, document)
        assert duplicate["id"] == created["id"]
        assert reads == 1
        app.state.jobs.semaphore.release()
        batch = await finished(app, pid, created["id"])
        assert reads == 1
        assert batch.status == "partial"
        assert len(profile_node["observed"]) == 3
        assert all(
            profile.base_url == "https://original.example/api"
            for profile in profile_node["observed"]
        )
        # A saved editable reference prevents deletion; historical snapshots alone do not.
        blocked = await client.delete("/api/v1/provider-profiles/cloud")
        assert blocked.status_code == 409
        default = default_workflow(pid)
        saved = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": document["revision"],
                "nodes": [node.model_dump() for node in default.nodes],
                "edges": [edge.model_dump() for edge in default.edges],
            },
        )
        assert saved.status_code == 200
        assert (await client.delete("/api/v1/provider-profiles/cloud")).status_code == 204
        profile_node["failing"].clear()
        before_retry = reads
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 202, response.text
        retry = await finished(app, pid, response.json()["id"])
        assert retry.status == "completed"
        assert reads == before_retry
        assert (
            retry.workflow.provider_profiles_snapshot["cloud"].base_url
            == "https://original.example/api"
        )
        assert retry.items[0].sequence == 2
        assert profile_node["observed"][-1].base_url == "https://original.example/api"
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile_id,profiles,code",
    [
        ("", [], "PROVIDER_PROFILE_REQUIRED"),
        ("cloud", [], "PROVIDER_PROFILE_NOT_FOUND"),
        ("cloud", [connection(enabled=False)], "PROVIDER_PROFILE_DISABLED"),
        ("cloud", [connection(provider_type="other")], "PROVIDER_PROFILE_TYPE_MISMATCH"),
        ("cloud", [connection(base_url=None)], "PROVIDER_PROFILE_BASE_URL_MISSING"),
        ("cloud", [connection(api_key_env=None)], "PROVIDER_PROFILE_API_KEY_ENV_MISSING"),
        (
            "cloud",
            [connection(base_url="https://example.com/api?key=secret")],
            "PROVIDER_PROFILE_BASE_URL_INVALID",
        ),
        (
            "cloud",
            [connection(base_url="https://example.com/api#secret")],
            "PROVIDER_PROFILE_BASE_URL_INVALID",
        ),
    ],
)
async def test_bad_connections_are_rejected_before_single_or_batch_reservation(
    tmp_path, profile_node, profile_id, profiles, code
):
    app = create_app(tmp_path)
    app.state.store.save_provider_profiles(profiles)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document, profile_id)
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
                    "request_id": "bad",
                },
            ),
        ]:
            response = await client.post(f"/api/v1/projects/{pid}/{endpoint}", json=payload)
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == code
        assert not app.state.jobs.tasks
        assert not app.state.jobs.reservations
        assert not app.state.batches.store.list(pid)
        assert app.state.store.list_workflow_runs(pid, aids[0], limit=10, offset=0)[1] == 0


@pytest.mark.asyncio
async def test_missing_frozen_profile_is_never_backfilled_from_current_store(
    tmp_path, profile_node
):
    app = create_app(tmp_path)
    app.state.store.save_provider_profiles([connection()])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        graph = WorkflowDocument.model_validate(profile_graph(document))
        snapshot = workflow_snapshot(graph)
        assert snapshot.provider_profiles_snapshot is None
        with pytest.raises(ProfileSnapshotError) as caught:
            await app.state.jobs.start_workflow_from_snapshot(
                pid, aids[0], snapshot, "bat_" + "0" * 32, sequence=1
            )
        assert caught.value.code == "PROVIDER_PROFILE_SNAPSHOT_MISSING"
        assert snapshot.provider_profiles_snapshot is None
        assert not app.state.jobs.tasks


@pytest.mark.asyncio
async def test_old_cloud_batch_retry_does_not_backfill_missing_snapshot(tmp_path, profile_node):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.store.save_provider_profiles([connection()])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document)
        profile_node["failing"].add(aids[0])
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        assert batch.status == "failed"
        path = app.state.batches.store.path(pid, batch.id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["workflow"].pop("provider_profiles_snapshot")
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()
        response = await client.post(
            f"/api/v1/projects/{pid}/batches/{batch.id}/retry", json={"request_id": "retry"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PROVIDER_PROFILE_SNAPSHOT_MISSING"
        assert path.read_bytes() == before
        assert len(app.state.batches.store.list(pid)) == 1
        assert not app.state.jobs.reservations
    await app.state.batches.shutdown()
    await app.state.jobs.shutdown()


@pytest.mark.asyncio
async def test_capabilities_report_invalid_profile_document_with_stable_error(
    tmp_path, profile_node
):
    app = create_app(tmp_path)
    (tmp_path / "provider_profiles.json").write_text("not-json", encoding="utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/capabilities")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_PROVIDER_PROFILES"


@pytest.mark.asyncio
async def test_capabilities_options_are_dynamic_filtered_and_do_not_modify_graph(
    tmp_path, profile_node
):
    app = create_app(tmp_path)
    app.state.store.save_provider_profiles(
        [
            connection(),
            connection(id="disabled", enabled=False),
            connection(id="other", provider_type="other"),
        ]
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document)
        path = app.state.store.project_dir(pid) / "workflow.json"
        before = path.read_bytes()
        for profiles, expected in [(None, [{"value": "cloud", "label": "Cloud"}]), ([], [])]:
            if profiles is not None:
                app.state.store.save_provider_profiles(profiles)
            response = await client.get("/api/v1/capabilities")
            assert response.status_code == 200, response.text
            assert response.json()["workflow"]["schema_version"] == "1.10"
            node = next(
                node
                for node in response.json()["workflow"]["nodes"]
                if node["type"] == "profile_test"
            )
            assert node["presentation"]["provider_profile_input"] == PROFILE_BINDING
            field = next(
                field for field in node["config"]["fields"] if field["key"] == "provider_profile_id"
            )
            assert field["type"] == "select" and field["input_port"] is None
            assert field["options"] == expected
            assert path.read_bytes() == before
        assert profile_node["definition"].fields[-1].type == "string"


@pytest.mark.parametrize(
    "change",
    [
        {"defaults": {}},
        {"defaults": {**PROFILE_BINDING["defaults"], "api_key": "secret"}},
        {"defaults": {**PROFILE_BINDING["defaults"], "api_key_env": None}},
        {"defaults": {**PROFILE_BINDING["defaults"], "default_timeout": True}},
        {"provider_type": "bad/type"},
        {"config_key": "not_declared"},
        {"label": ""},
        {"extra": "x"},
    ],
)
def test_invalid_profile_declarations_cannot_be_registered(profile_node, change):
    definition = replace(
        profile_node["definition"],
        presentation={"provider_profile_input": {**PROFILE_BINDING, **change}},
    )
    with pytest.raises(ValueError):
        validate_node_definition(definition)


def test_profile_binding_cannot_be_wired(profile_node):
    definition = profile_node["definition"]
    definition = replace(
        definition,
        fields=tuple(
            item.model_copy(update={"input_port": "language"})
            if item.key == "provider_profile_id"
            else item
            for item in definition.fields
        ),
    )
    with pytest.raises(ValueError, match="cannot be connected parameters"):
        validate_node_definition(definition)


@pytest.mark.parametrize("hours,flag", [(48, True), (1.5, False), (None, True)])
def test_optional_profile_data_handling_is_validated_and_copied(
    profile_node, monkeypatch, hours, flag
):
    from app.domain.node_registry import node_catalog

    handling = {"temporary_upload_retention_hours": hours, "remote_cancel_pending_only": flag}
    definition = replace(
        profile_node["definition"],
        presentation={"provider_profile_input": {**PROFILE_BINDING, "data_handling": handling}},
    )
    validate_node_definition(definition)
    monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
    published = next(node for node in node_catalog() if node["type"] == definition.type)
    copy = published["presentation"]["provider_profile_input"]["data_handling"]
    assert copy == handling
    copy["remote_cancel_pending_only"] = not flag
    assert handling["remote_cancel_pending_only"] == flag


@pytest.mark.parametrize(
    "handling",
    [
        None,
        {},
        {"temporary_upload_retention_hours": 48},
        {"temporary_upload_retention_hours": True, "remote_cancel_pending_only": True},
        {"temporary_upload_retention_hours": 0, "remote_cancel_pending_only": True},
        {"temporary_upload_retention_hours": float("inf"), "remote_cancel_pending_only": True},
        {"temporary_upload_retention_hours": float("nan"), "remote_cancel_pending_only": True},
        {"temporary_upload_retention_hours": 48, "remote_cancel_pending_only": 1},
        {"temporary_upload_retention_hours": 48, "remote_cancel_pending_only": True, "extra": 1},
    ],
)
def test_invalid_profile_data_handling_cannot_be_registered(profile_node, handling):
    definition = replace(
        profile_node["definition"],
        presentation={"provider_profile_input": {**PROFILE_BINDING, "data_handling": handling}},
    )
    with pytest.raises(ValueError):
        validate_node_definition(definition)


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", [False, True])
async def test_cancel_details_are_persisted_only_when_provided(tmp_path, profile_node, detail):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.store.save_provider_profiles([connection()])
    profile_node["cancel_detail"] = detail
    profile_node["cancel_plain"] = not detail
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aids[0]}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202, response.text
        await asyncio.gather(*app.state.jobs.tasks.values())
        run = app.state.store.load_workflow_run(pid, aids[0], response.json()["id"])
        assert run.status.value == "cancelled"
        cloud = next(node for node in run.nodes if node.node_id == "cloud")
        if detail:
            assert cloud.error.type == "CLOUD_CANCEL_UNKNOWN"
            assert cloud.error.message == "本地已停止，远端取消状态未知"
        else:
            assert cloud.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("document_type", ["run", "batch"])
async def test_nested_profile_versions_rejected_and_not_rewritten_during_recovery(
    tmp_path, profile_node, document_type
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    app.state.store.save_provider_profiles([connection()])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        document = await save_profile_graph(client, pid, document)
        created = await submit(client, pid, aids, document)
        batch = await finished(app, pid, created["id"])
        item = batch.items[0]
        path = (
            app.state.store.workflow_run_dir(pid, aids[0], item.run_id) / "run.json"
            if document_type == "run"
            else app.state.batches.store.path(pid, batch.id)
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["workflow"]["provider_profiles_snapshot"]["cloud"].pop("schema_version")
        payload["status"] = "running"
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = path.read_bytes()
        with pytest.raises(SchemaVersionError):
            if document_type == "run":
                app.state.store.load_workflow_run(pid, aids[0], item.run_id)
            else:
                app.state.batches.store.load(pid, batch.id)
    restarted = create_app(tmp_path)
    async with restarted.router.lifespan_context(restarted):
        assert path.read_bytes() == before


def test_old_snapshot_omits_none_profile_field_and_freezing_does_not_mutate_input(
    tmp_path, profile_node
):
    app = create_app(tmp_path)
    snapshot = workflow_snapshot(default_workflow("prj_x"))
    assert "provider_profiles_snapshot" not in snapshot.model_dump()
    assert freeze_workflow_profiles(app.state.store, snapshot).provider_profiles_snapshot is None
    graph = WorkflowDocument.model_validate(
        profile_graph(default_workflow("prj_x").model_dump(mode="json"))
    )
    original = workflow_snapshot(graph)
    app.state.store.save_provider_profiles([connection()])
    frozen = freeze_workflow_profiles(app.state.store, original)
    assert original.provider_profiles_snapshot is None
    assert frozen.provider_profiles_snapshot["cloud"].api_key_env == "TEST_CLOUD_KEY"
    # The global model still reads legacy profiles with query/fragment URLs;
    # the restriction applies when a cloud node attempts to use the profile.
    assert ProviderProfile.model_validate(
        connection(base_url="https://old.example?old=1#fragment").model_dump()
    ).base_url
