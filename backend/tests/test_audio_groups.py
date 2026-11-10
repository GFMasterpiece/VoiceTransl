import asyncio
import errno
import json
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest
from app.domain.audio_group import AudioGroup, AudioGroupValues
from app.domain.node_execution import NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS, TYPE_DEFINITIONS, validate_node_result
from app.main import create_app
from app.nodes import group_info
from app.storage.audio_group_store import AudioGroupStore
from httpx import ASGITransport, AsyncClient

from test_batches import setup


async def create_group(client, pid, **values):
    response = await client.post(
        f"/api/v1/projects/{pid}/audio-groups", json={"name": "场景一", **values}
    )
    assert response.status_code == 201, response.text
    return response.json()


def update(group, **values):
    return {
        "expected_revision": group["revision"],
        **{key: group[key] for key in ("name", "audio_ids", "background", "mode")},
        **values,
    }


@pytest.mark.asyncio
async def test_groups_reuse_assets_atomic_order_revision_pagination_and_delete(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client)
        project_path = app.state.store.project_dir(pid) / "project.json"
        project_before = project_path.read_bytes()
        sources = {
            aid: app.state.store.scope(pid, aid)
            .source_path(pid, app.state.store.audio(pid, aid).source.path)
            .read_bytes()
            for aid in aids
        }
        group = await create_group(
            client,
            pid,
            name="  第一组  ",
            audio_ids=aids,
            background="password → 密码",
            mode="shared",
        )
        assert (
            group["name"] == "第一组"
            and group["schema_version"] == "1.0"
            and group["revision"] == 1
        )
        assert group["audio_ids"] == aids and "project_id" not in group
        other = await create_group(client, pid, audio_ids=aids[:1])
        response = await client.get(f"/api/v1/projects/{pid}/audio-groups?limit=1&offset=1")
        page = response.json()
        assert page["total"] == 2 and page["limit"] == 1 and page["offset"] == 1
        assert page["items"][0]["id"] == group["id"]
        assert page["items"][0]["audio_count"] == 3
        assert not {"background", "audio_ids", "schema_version"} & page["items"][0].keys()
        response = await client.put(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}",
            json=update(group, audio_ids=[aids[2], aids[0]], mode="continuous"),
        )
        assert response.status_code == 200, response.text
        changed = response.json()
        assert changed["revision"] == 2 and changed["audio_ids"] == [aids[2], aids[0]]
        assert changed["created_at"] == group["created_at"]
        stale = await client.put(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}", json=update(group)
        )
        assert (
            stale.status_code == 409 and stale.json()["error"]["code"] == "GROUP_REVISION_CONFLICT"
        )
        rejected = await client.delete(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}?expected_revision=1"
        )
        assert rejected.status_code == 409
        assert (
            await client.delete(
                f"/api/v1/projects/{pid}/audio-groups/{group['id']}?expected_revision=2"
            )
        ).status_code == 204
        assert (
            await client.get(f"/api/v1/projects/{pid}/audio-groups/{group['id']}")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/projects/{pid}/audio-groups/{other['id']}")
        ).status_code == 200
        assert app.state.store.load_workflow(pid).revision == document["revision"]
        assert project_path.read_bytes() == project_before
        assert len(app.state.store.load_project(pid).audios) == 3
        for aid, payload in sources.items():
            assert (
                app.state.store.scope(pid, aid)
                .source_path(pid, app.state.store.audio(pid, aid).source.path)
                .read_bytes()
                == payload
            )


@pytest.mark.asyncio
async def test_same_revision_concurrent_edits_have_exactly_one_winner(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, _ = await setup(client)
        group = await create_group(client, pid, audio_ids=aids)
        responses = await asyncio.gather(
            *[
                client.put(
                    f"/api/v1/projects/{pid}/audio-groups/{group['id']}",
                    json=update(group, audio_ids=order),
                )
                for order in (list(reversed(aids)), aids[:1])
            ]
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        winner = next(response.json() for response in responses if response.status_code == 200)
        assert (
            await client.get(f"/api/v1/projects/{pid}/audio-groups/{group['id']}")
        ).json() == winner


@pytest.mark.parametrize(
    "values",
    [
        {"name": " "},
        {"name": "n" * 201},
        {"name": "bad\nname"},
        {"background": "界" * 21846},
        {"background": "\ud800"},
        {"audio_ids": ["same", "same"]},
        {"audio_ids": "not-list"},
        {"mode": "automatic_memory"},
        {"audio_ids": [1]},
        {"extra": True},
    ],
)
def test_group_values_are_bounded_and_typed(values):
    with pytest.raises(ValueError):
        AudioGroupValues.model_validate({"name": "Group", **values})


def test_background_utf8_boundary_and_member_count_boundary():
    assert len(AudioGroupValues(name="Group", background="x" * 65536).background) == 65536
    assert (
        len(AudioGroupValues(name="Group", audio_ids=[f"a{i}" for i in range(10000)]).audio_ids)
        == 10000
    )
    with pytest.raises(ValueError):
        AudioGroupValues(name="Group", audio_ids=[f"a{i}" for i in range(10001)])


@pytest.mark.asyncio
async def test_unknown_and_foreign_audio_ids_are_rejected_before_publishing(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        other, aids, _ = await setup(client, 2)
        assert other != pid
        response = await client.post(
            f"/api/v1/projects/{pid}/audio-groups", json={"name": "Group", "audio_ids": [aids[1]]}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "GROUP_AUDIO_NOT_FOUND"
        assert response.json()["error"]["details"]["audio_ids"] == [aids[1]]
        assert (await client.get(f"/api/v1/projects/{pid}/audio-groups")).json()["total"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "audio_ids", "background", "mode", "expected_revision"])
async def test_put_requires_the_complete_editable_representation(tmp_path, field):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        group = await create_group(client, pid)
        payload = update(group)
        payload.pop(field)
        response = await client.put(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}", json=payload
        )
        assert response.status_code == 422
        assert (
            await client.get(f"/api/v1/projects/{pid}/audio-groups/{group['id']}")
        ).json() == group


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [True, False])
async def test_saved_source_and_group_info_references_block_deletion(tmp_path, source):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, document = await setup(client, 1)
        group = await create_group(client, pid, audio_ids=aids)
        nodes, edges = json.loads(json.dumps(document["nodes"])), list(document["edges"])
        if source:
            nodes[0]["config"] = {"group_id": group["id"]}
            reference = nodes[0]["id"]
        else:
            reference = "group"
            nodes.append(
                {"id": reference, "type": "group_info", "config": {"group_id": group["id"]}}
            )
            edges.append(
                {
                    "id": "group_name",
                    "source": reference,
                    "source_port": "name",
                    "target": nodes[1]["id"],
                    "target_port": "filename_stem",
                }
            )
        saved = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={"expected_revision": document["revision"], "nodes": nodes, "edges": edges},
        )
        assert saved.status_code == 200, saved.text
        response = await client.delete(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}?expected_revision=1"
        )
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "GROUP_REFERENCED"
        )
        assert response.json()["error"]["details"]["node_ids"] == [reference]
        cleared = await client.put(
            f"/api/v1/projects/{pid}/workflow",
            json={
                "expected_revision": saved.json()["revision"],
                "nodes": document["nodes"],
                "edges": document["edges"],
            },
        )
        assert cleared.status_code == 200
        assert (
            await client.delete(
                f"/api/v1/projects/{pid}/audio-groups/{group['id']}?expected_revision=1"
            )
        ).status_code == 204


@pytest.mark.asyncio
async def test_group_info_reads_frozen_data_and_existing_string_codec_only(tmp_path):
    group = AudioGroup(
        id="grp_" + "a" * 32,
        revision=1,
        name="Frozen",
        background="password → 密码",
        audio_ids=[],
        mode="shared",
    )

    class NoMutableReads:
        def __getattr__(self, key):
            raise AssertionError("group_info must not read project or group storage")

    context = SimpleNamespace(
        node=SimpleNamespace(config={"group_id": group.id}),
        run=SimpleNamespace(
            workflow=SimpleNamespace(group_snapshots={group.id: group.model_dump(mode="json")})
        ),
        store=NoMutableReads(),
        project=NoMutableReads(),
    )
    result = await group_info.execute(context)
    validate_node_result(NODE_DEFINITIONS["group_info"], result)
    assert {key: value.value for key, value in result.outputs.items()} == {
        "background": "password → 密码",
        "name": "Frozen",
        "group_id": group.id,
        "mode": "shared",
    }
    codec = TYPE_DEFINITIONS["string"].checkpoint_codec
    encoded = await codec.encode(result.outputs["background"].value, None)
    assert await codec.decode(encoded, None) == group.background
    context.run.workflow.group_snapshots.clear()
    with pytest.raises(NodeExecutionError) as error:
        await group_info.execute(context)
    assert error.value.code == "GROUP_SNAPSHOT_REQUIRED"


@pytest.mark.asyncio
async def test_audio_pagination_preserves_legacy_response_and_loads_only_page_jobs(
    tmp_path, monkeypatch
):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aids, _ = await setup(client)
        original = app.state.jobs.job
        seen = []

        def load_job(project_id, audio_id):
            seen.append(audio_id)
            return original(project_id, audio_id)

        monkeypatch.setattr(app.state.jobs, "job", load_job)
        response = await client.get(f"/api/v1/projects/{pid}/audios?limit=1&offset=1")
        assert response.status_code == 200
        assert response.json()["total"] == 3 and response.json()["items"][0]["id"] == aids[1]
        assert seen == [aids[1]]
        seen.clear()
        unpaged = (await client.get(f"/api/v1/projects/{pid}/audios")).json()
        assert set(unpaged) == {"items"} and len(unpaged["items"]) == 3 and seen == aids
        assert (await client.get(f"/api/v1/projects/{pid}/audios?offset=1")).status_code == 422


@pytest.mark.asyncio
async def test_failed_atomic_update_keeps_the_old_group(tmp_path, monkeypatch):
    from app.storage import audio_group_store

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        group = await create_group(client, pid)
        path = AudioGroupStore(app.state.store).path(pid, group["id"])
        before = path.read_bytes()

        def fail(*args):
            raise OSError("Injected storage failure")

        monkeypatch.setattr(audio_group_store, "atomic_write_json", fail)
        response = await client.put(
            f"/api/v1/projects/{pid}/audio-groups/{group['id']}", json=update(group, name="Changed")
        )
        assert (
            response.status_code == 503
            and response.json()["error"]["code"] == "GROUP_STORAGE_ERROR"
        )
        assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt", [{"schema_version": "999"}, {"id": "grp_" + "b" * 32}, {"audio_ids": ["unknown"]}]
)
async def test_corrupt_group_files_are_not_silently_hidden_or_modified(tmp_path, corrupt):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        group = await create_group(client, pid)
        path = AudioGroupStore(app.state.store).path(pid, group["id"])
        path.write_text(json.dumps({**group, **corrupt}), encoding="utf-8")
        before = path.read_bytes()
        for suffix in ("", f"/{group['id']}"):
            response = await client.get(f"/api/v1/projects/{pid}/audio-groups{suffix}")
            assert response.status_code == 422
        assert path.read_bytes() == before


@pytest.mark.parametrize(
    "bad",
    [
        "..",
        "../group",
        "grp_" + "a" * 32 + ".json",
        "grp_" + "a" * 31,
        "grp_" + "a" * 32 + "\\escape",
    ],
)
def test_group_paths_reject_traversal_and_invalid_identifiers(tmp_path, bad):
    app = create_app(tmp_path)
    with pytest.raises(ValueError):
        AudioGroupStore(app.state.store).path("prj_test", bad)


@pytest.mark.asyncio
async def test_group_directory_junction_or_symlink_cannot_escape_project(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        target = tmp_path / "outside"
        target.mkdir()
        root = app.state.store.project_dir(pid) / "audio-groups"
        if os.name == "nt":
            process = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(root), str(target)], capture_output=True
            )
            if process.returncode:
                pytest.skip("Creating directory junction is not available")
        else:
            root.symlink_to(target, target_is_directory=True)
        try:
            response = await client.post(
                f"/api/v1/projects/{pid}/audio-groups", json={"name": "Unsafe"}
            )
            assert (
                response.status_code == 422 and response.json()["error"]["code"] == "GROUP_INVALID"
            )
            assert list(target.iterdir()) == []
        finally:
            if os.name == "nt":
                root.rmdir()
            else:
                root.unlink()


@pytest.mark.asyncio
async def test_cross_project_hardlink_cannot_read_overwrite_or_delete_group(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        source_pid, _, _ = await setup(client, 1)
        target_pid, _, _ = await setup(client, 1)
        group = await create_group(client, source_pid, background="OTHER-PROJECT-BACKGROUND")
        store = AudioGroupStore(app.state.store)
        source = store.path(source_pid, group["id"])
        target = store.path(target_pid, group["id"])
        target.parent.mkdir()
        before = source.read_bytes()
        try:
            os.link(source, target)
        except OSError as error:
            if error.errno in {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP} or getattr(
                error, "winerror", None
            ) in {1, 50}:
                pytest.skip("The test filesystem explicitly does not support hardlinks")
            raise
        assert source.stat().st_nlink == target.stat().st_nlink == 2
        base = f"/api/v1/projects/{target_pid}/audio-groups"
        responses = [
            await client.get(base),
            await client.get(f"{base}/{group['id']}"),
            await client.put(f"{base}/{group['id']}", json=update(group, background="Changed")),
            await client.delete(f"{base}/{group['id']}?expected_revision=1"),
        ]
        for response in responses:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "GROUP_INVALID"
            assert group["background"] not in response.text
        with pytest.raises(ValueError):
            store._write(target, AudioGroup.model_validate(group))
        assert source.read_bytes() == target.read_bytes() == before
        assert source.stat().st_nlink == target.stat().st_nlink == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,links", [(stat.S_IFDIR, 1), (stat.S_IFREG, 2)])
async def test_opened_file_fstat_is_checked_before_reading(tmp_path, monkeypatch, mode, links):
    from app.storage import audio_group_store

    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        group = await create_group(client, pid)
        store = AudioGroupStore(app.state.store)
        path = store.path(pid, group["id"])
        before = path.read_bytes()
        assert path.stat().st_nlink == 1 and path.is_file()
        monkeypatch.setattr(
            audio_group_store,
            "os",
            SimpleNamespace(fstat=lambda _fd: SimpleNamespace(st_mode=mode, st_nlink=links)),
        )
        with pytest.raises(ValueError, match="普通文件"):
            store.load(pid, group["id"])
        assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_group_record_must_be_a_regular_file(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _, _ = await setup(client, 1)
        group = await create_group(client, pid)
        path = AudioGroupStore(app.state.store).path(pid, group["id"])
        path.unlink()
        path.mkdir()
        response = await client.get(f"/api/v1/projects/{pid}/audio-groups/{group['id']}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "GROUP_INVALID"
        assert path.is_dir()
