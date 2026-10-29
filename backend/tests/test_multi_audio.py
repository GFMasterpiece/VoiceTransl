import asyncio
import json

import pytest
from app.domain import JobStatus, default_pipeline_config
from app.domain.project import PRIMARY_AUDIO_ID
from app.exporters import render_vtt
from app.main import create_app
from app.storage import FileSystemProjectStore, TranscriptProtectedError
from httpx import ASGITransport, AsyncClient

from test_websocket_lifecycle import WebSocketHarness


async def upload(client, url="/api/v1/projects", language="ja", payload=b"first"):
    response = await client.post(
        url,
        files={"file": ("same.wav", payload, "audio/wav")},
        data={"source_language": language, "target_language": "zh-CN"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_extra_only_run_has_parent_identity_and_isolated_artifacts(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await upload(client)
        pid = first["project"]["id"]
        assert first["project"]["audios"][0]["id"] == PRIMARY_AUDIO_ID
        base = f"/api/v1/projects/{pid}"
        second = await upload(client, base + "/audios", payload=b"second")
        aid = second["audio"]["id"]
        assert second["job"]["project_id"] == pid
        assert second["job"]["audio_id"] == aid
        assert len((await client.get("/api/v1/projects")).json()["items"]) == 1
        items = (await client.get(base + "/audios")).json()["items"]
        assert [item["id"] for item in items] == [PRIMARY_AUDIO_ID, aid]
        scope = app.state.store.scope(pid, aid)
        assert scope.source_path(pid, second["audio"]["source"]["path"]).read_bytes() == b"second"
        response = await client.post(base + "/audios/run", json={"audio_ids": [aid]})
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        current = scope.load_job(pid)
        assert current.status == JobStatus.COMPLETED
        assert app.state.store.load_job(pid).status == JobStatus.PENDING
        assert current.stages["export"].artifact == f"audios/{aid}/output"
        assert list((app.state.store.project_dir(pid) / "output").iterdir()) == []
        metadata = json.loads((scope.audio_dir() / "cache/preprocess.json").read_text())
        assert metadata["source_path"] == second["audio"]["source"]["path"]
        outputs = (await client.get(base + f"/audios/{aid}/outputs")).json()["items"]
        assert len(outputs) == 3
        assert (await client.get(outputs[0]["download_url"])).status_code == 200
        assert (await client.get(base + "/outputs")).json() == {"items": []}
        assert (await client.get(base + f"/audios/{aid}/transcript")).json()[
            "stage"
        ] == "translated"
        current.status = JobStatus.INTERRUPTED
        scope.save_job(current)
        response = await client.post(base + f"/audios/{aid}/run", json={"resume": True})
        assert response.json()["job_id"] == current.id
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert scope.load_job(pid).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_common_config_overrides_snapshots_and_languages(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        english = (await upload(client, base + "/audios", language="en"))["audio"]["id"]
        config = default_pipeline_config().model_dump(mode="json")
        config["translation"]["model"] = "common"
        assert (await client.put(base + "/config", json=config)).status_code == 200
        inherited = (await client.get(base + f"/audios/{english}/config")).json()
        assert inherited["source_language"] == inherited["asr"]["language"] == "en"
        assert inherited["translation"]["model"] == "common"
        assert (
            await client.put(base + f"/audios/{english}/config", json=config)
        ).status_code == 422
        for selected in [PRIMARY_AUDIO_ID, aid]:
            assert (
                await client.put(base + f"/audios/{selected}/config", json=config)
            ).status_code == 200
        await client.post(base + "/audios/run", json={"audio_ids": [PRIMARY_AUDIO_ID, aid]})
        await asyncio.gather(*app.state.jobs.tasks.values())
        for selected in [PRIMARY_AUDIO_ID, aid]:
            snapshot = app.state.jobs.job(pid, selected).config_snapshot
            assert snapshot.translation.model == "common"
        config["translation"]["model"] = "new default"
        await client.put(base + "/config", json=config)
        assert (await client.get(base + f"/audios/{PRIMARY_AUDIO_ID}/config")).json()[
            "translation"
        ]["model"] == "common"
        assert (await client.get(base + f"/audios/{aid}/config")).json()["translation"][
            "model"
        ] == "common"
        assert (await client.get(base + f"/audios/{english}/config")).json()["translation"][
            "model"
        ] == "new default"
        assert app.state.jobs.job(pid, aid).config_snapshot.translation.model == "common"


@pytest.mark.asyncio
async def test_primary_audio_override_does_not_change_unselected_audio_default(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        default = (await client.get(base + f"/audios/{aid}/config")).json()
        common_path = app.state.store.project_dir(pid) / "config.json"
        common_before = common_path.read_bytes()
        selected = default_pipeline_config().model_dump(mode="json")
        selected["translation"]["model"] = "primary only"
        response = await client.put(base + f"/audios/{PRIMARY_AUDIO_ID}/config", json=selected)
        assert response.status_code == 200
        assert (await client.get(base + f"/audios/{aid}/config")).json() == default
        assert (await client.get(base + "/config")).json() == default
        assert (app.state.store.project_dir(pid) / "primary-config.json").is_file()
        assert common_path.read_bytes() == common_before
        await client.post(base + "/run", json={"resume": True})
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert app.state.store.load_job(pid).config_snapshot.translation.model == "primary only"
        assert app.state.jobs.job(pid, aid).status == JobStatus.PENDING


@pytest.mark.asyncio
async def test_inheritance_language_conflict_requires_override_but_resume_keeps_snapshot(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios", language="en"))["audio"]["id"]
        config = default_pipeline_config().model_dump(mode="json")
        config["target_language"] = config["translation"]["target_language"] = "en"
        assert (await client.put(base + "/config", json=config)).status_code == 200
        response = await client.get(base + f"/audios/{aid}/config")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_PIPELINE_CONFIG"
        response = await client.post(base + "/audios/run", json={"audio_ids": [aid]})
        assert response.json()["items"][0]["error"]["code"] == "INVALID_PIPELINE_CONFIG"
        scope = app.state.store.scope(pid, aid)
        interrupted = scope.load_job(pid)
        interrupted.status = JobStatus.INTERRUPTED
        interrupted.config_snapshot = default_pipeline_config("en")
        scope.save_job(interrupted)
        response = await client.post(base + f"/audios/{aid}/run", json={"resume": True})
        assert response.status_code == 202
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert scope.load_job(pid).status == JobStatus.COMPLETED
        assert scope.load_job(pid).config_snapshot.target_language == "zh-CN"


@pytest.mark.asyncio
async def test_batch_validation_busy_and_queued_cancel_are_audio_scoped(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    await app.state.jobs.semaphore.acquire()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        for ids, status in [([aid, aid], 422), ([aid, "missing"], 404), ([], 422)]:
            assert (
                await client.post(base + "/audios/run", json={"audio_ids": ids})
            ).status_code == status
            assert not app.state.jobs.tasks
        result = await client.post(
            base + "/audios/run", json={"audio_ids": [PRIMARY_AUDIO_ID, aid]}
        )
        assert all("job_id" in item for item in result.json()["items"])
        busy = await client.post(base + "/audios/run", json={"audio_ids": [aid]})
        assert busy.json()["items"][0]["error"]["code"] == "JOB_ALREADY_RUNNING"
        assert (await client.delete(base)).status_code == 409
        assert (
            await client.put(
                base + f"/audios/{aid}/config",
                json=default_pipeline_config().model_dump(mode="json"),
            )
        ).status_code == 409
        assert (await client.post(base + f"/audios/{aid}/cancel")).status_code == 200
        assert app.state.jobs.job(pid, PRIMARY_AUDIO_ID).status == JobStatus.PENDING
        app.state.jobs.semaphore.release()
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert app.state.jobs.job(pid, aid).status == JobStatus.CANCELLED
        assert app.state.jobs.job(pid, PRIMARY_AUDIO_ID).status == JobStatus.COMPLETED
        assert (await client.delete(base)).status_code == 204


@pytest.mark.asyncio
async def test_append_limit_concurrency_and_unknown_audio_safety(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        uploaded = await asyncio.gather(*(upload(client, base + "/audios") for _ in range(3)))
        assert len(app.state.store.load_project(pid).audios) == 4
        app.state.settings.max_upload_mb = 0
        assert (
            await client.post(
                base + "/audios",
                files={"file": ("big.wav", b"x")},
                data={"source_language": "ja"},
            )
        ).status_code == 413
        root = app.state.store.project_dir(pid) / "audios"
        assert len(list(root.iterdir())) == 3
        assert len(app.state.store.load_project(pid).audios) == 4
        for operation in ["job", "config", "outputs", "transcript"]:
            response = await client.get(base + f"/audios/missing/{operation}")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "AUDIO_NOT_FOUND"
        assert not app.state.jobs.tasks
        aid = uploaded[0]["audio"]["id"]
        scope = app.state.store.scope(pid, aid)
        with pytest.raises(ValueError):
            scope.resolve_project_path(pid, "../../source/original.wav")
        with pytest.raises(ValueError):
            scope.resolve_artifact_path(pid, uploaded[1]["audio"]["source"]["path"])
        with pytest.raises(ValueError):
            app.state.store.resolve_artifact_path(
                pid, "./" + uploaded[0]["audio"]["source"]["path"]
            )
        assert app.state.store.resolve_artifact_path(pid, "./cache/preprocess.json") == (
            app.state.store.project_dir(pid) / "cache/preprocess.json"
        )
        linked = app.state.store.project_dir(pid) / "cache/linked-source.wav"
        try:
            linked.symlink_to(scope.source_path(pid, uploaded[0]["audio"]["source"]["path"]))
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symlinks for this security test")
            raise
        with pytest.raises(ValueError):
            app.state.store.resolve_artifact_path(pid, "cache/linked-source.wav")
        with pytest.raises(ValueError):
            scope.resolve_output(pid, "../original.wav")
        directory = scope.audio_dir()
        (directory / "output").rmdir()
        try:
            (directory / "output").symlink_to(app.state.store.project_dir(pid) / "output")
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows account cannot create symlinks for this security test")
            raise
        with pytest.raises(ValueError):
            scope.resolve_output(pid, "zh.srt")


@pytest.mark.asyncio
async def test_restart_recovers_all_jobs_and_legacy_primary_without_moving_source(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        aid = (await upload(client, f"/api/v1/projects/{pid}/audios"))["audio"]["id"]
    for selected, status in [(PRIMARY_AUDIO_ID, JobStatus.RUNNING), (aid, JobStatus.PENDING)]:
        scope = app.state.store.scope(pid, selected)
        job = scope.load_job(pid)
        job.status = status
        job.config_snapshot = default_pipeline_config()
        scope.save_job(job)
    restarted = FileSystemProjectStore(tmp_path)
    restarted.mark_interrupted()
    for selected in [PRIMARY_AUDIO_ID, aid]:
        assert restarted.scope(pid, selected).load_job(pid).status == JobStatus.INTERRUPTED
    directory = restarted.project_dir(pid)
    metadata = json.loads((directory / "project.json").read_text())
    metadata.pop("audios")
    (directory / "project.json").write_text(json.dumps(metadata))
    legacy_job = json.loads((directory / "job.json").read_text())
    legacy_job.pop("audio_id")
    (directory / "job.json").write_text(json.dumps(legacy_job))
    assert restarted.load_project(pid).audios[0].id == PRIMARY_AUDIO_ID
    assert restarted.load_job(pid).audio_id == PRIMARY_AUDIO_ID
    assert (directory / "source/original.wav").read_bytes() == b"first"


@pytest.mark.asyncio
async def test_failed_extra_does_not_affect_primary_or_protected_transcript(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        scope = app.state.store.scope(pid, aid)
        scope.source_path(pid, scope.load_project(pid).source.path).unlink()
        await client.post(base + "/audios/run", json={"audio_ids": [aid, PRIMARY_AUDIO_ID]})
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert scope.load_job(pid).status == JobStatus.FAILED
        assert app.state.store.load_job(pid).status == JobStatus.COMPLETED
        transcript = app.state.store.load_transcript(pid, "raw")
        scope.save_transcript(pid, "raw", transcript)
        edited = transcript.model_copy(deep=True)
        edited.segments[0].source_text = "manual edit"
        with pytest.raises(TranscriptProtectedError):
            scope.save_transcript(pid, "raw", edited)


@pytest.mark.asyncio
async def test_audio_websocket_filters_siblings_and_parent_receives_all(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        aid = (await upload(client, f"/api/v1/projects/{pid}/audios"))["audio"]["id"]
    parent = WebSocketHarness(app, pid)
    audio = WebSocketHarness(app, pid, audio_id=aid)
    tasks = [asyncio.create_task(harness.run()) for harness in [parent, audio]]
    for _ in range(100):
        if len(parent.sent) == len(audio.sent) == 2:
            break
        await asyncio.sleep(0)
    assert json.loads(audio.sent[1]["text"])["audio_id"] == aid
    for selected in [PRIMARY_AUDIO_ID, aid]:
        await app.state.events.publish(
            {
                "type": "job.started",
                "project_id": pid,
                "audio_id": selected,
                "job_id": app.state.jobs.job(pid, selected).id,
            }
        )
    for _ in range(100):
        if len(parent.sent) >= 4 and len(audio.sent) >= 3:
            break
        await asyncio.sleep(0)
    assert len(parent.sent) == 4
    assert len(audio.sent) == 3
    assert json.loads(audio.sent[-1]["text"])["audio_id"] == aid
    for harness in [parent, audio]:
        await harness.incoming.put({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
    assert pid not in app.state.events._subs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "formats,bilingual,expected",
    [
        (["srt", "lrc", "vtt"], True, {"zh.srt", "ja-zh.srt", "zh.lrc", "zh.vtt", "ja-zh.vtt"}),
        (["vtt"], False, {"zh.vtt"}),
        (["srt"], False, {"zh.srt"}),
        (["lrc"], True, {"zh.lrc"}),
    ],
)
async def test_export_snapshot_formats_apply_to_each_audio_and_resume(
    tmp_path, formats, bilingual, expected
):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = formats
        config["export"]["bilingual"] = bilingual
        for selected in [PRIMARY_AUDIO_ID, aid]:
            assert (
                await client.put(base + f"/audios/{selected}/config", json=config)
            ).status_code == 200
        await client.post(base + "/audios/run", json={"audio_ids": [PRIMARY_AUDIO_ID, aid]})
        await asyncio.gather(*app.state.jobs.tasks.values())
        for selected in [PRIMARY_AUDIO_ID, aid]:
            scope = app.state.store.scope(pid, selected)
            current = scope.load_job(pid)
            assert current.status == JobStatus.COMPLETED
            outputs = (await client.get(base + f"/audios/{selected}/outputs")).json()["items"]
            assert {item["name"] for item in outputs} == expected
            if "vtt" in formats:
                vtt = (
                    await client.get(
                        next(item["download_url"] for item in outputs if item["name"] == "zh.vtt")
                    )
                ).text
                assert vtt.startswith("WEBVTT\n\n")
                assert "00:00:00.000 --> " in vtt
            current.status = JobStatus.INTERRUPTED
            scope.save_job(current)
            response = await client.post(base + f"/audios/{selected}/run", json={"resume": True})
            assert response.json()["job_id"] == current.id
            await asyncio.gather(*app.state.jobs.tasks.values())
            assert scope.load_job(pid).status == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_rerun_prunes_only_selected_audio_known_generated_outputs(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        await client.post(base + "/audios/run", json={"audio_ids": [PRIMARY_AUDIO_ID, aid]})
        await asyncio.gather(*app.state.jobs.tasks.values())
        scope = app.state.store.scope(pid, aid)
        scope.save_output_text(pid, "user-notes.txt", "keep")
        config = default_pipeline_config().model_dump(mode="json")
        config["export"]["formats"] = ["vtt"]
        config["export"]["bilingual"] = False
        await client.put(base + f"/audios/{aid}/config", json=config)
        await client.post(base + f"/audios/{aid}/run", json={"resume": False})
        await asyncio.gather(*app.state.jobs.tasks.values())
        assert {path.name for path in scope.list_outputs(pid)} == {"zh.vtt"}
        assert (scope.audio_dir() / "output" / "user-notes.txt").read_text(
            encoding="utf-8"
        ) == "keep"
        assert {path.name for path in app.state.store.list_outputs(pid)} == {
            "zh.srt",
            "ja-zh.srt",
            "zh.lrc",
        }


def test_vtt_timestamps_and_bilingual_text_preserve_punctuation():
    from app.domain import Segment

    segment = Segment(
        id="one",
        start=1.2345,
        end=59.9995,
        source_text="原文, source",
        translated_text="译文, target",
    )
    rendered = render_vtt([segment], bilingual=True)
    assert "00:00:01.235 --> 00:01:00.000" in rendered
    assert "原文, source\n译文, target" in rendered
