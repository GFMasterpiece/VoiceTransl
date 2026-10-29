import asyncio
import json

import pytest
from app.domain import PipelineStageName, StageStatus, default_pipeline_config
from app.domain.project import PRIMARY_AUDIO_ID
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from test_multi_audio import upload


async def complete(client, app, pid, aid=PRIMARY_AUDIO_ID):
    response = await client.post(f"/api/v1/projects/{pid}/audios/{aid}/run", json={"resume": False})
    assert response.status_code == 202
    await asyncio.gather(*app.state.jobs.tasks.values())
    return app.state.store.scope(pid, aid)


@pytest.mark.asyncio
async def test_stage_details_return_distinct_results_and_snapshot(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}/audios/{PRIMARY_AUDIO_ID}"
        scope = await complete(client, app, pid)
        current = scope.load_job(pid)
        # Distinguish alignment's result from ASR's time ranges so returning
        # the newest transcript or copying raw into alignment cannot pass.
        aligned = scope.load_transcript(pid, "aligned")
        aligned.segments[0].start = 0.25
        scope.save_transcript(pid, "aligned", aligned, overwrite=True)
        draft = default_pipeline_config().model_dump(mode="json")
        draft["translation"]["model"] = "edited after completion"
        assert (await client.put(base + "/config", json=draft)).status_code == 200
        results = {}
        for stage in PipelineStageName:
            response = await client.get(base + f"/stages/{stage}")
            assert response.status_code == 200, response.text
            value = response.json()
            assert value["stage"] == stage
            assert value["job_id"] == current.id
            assert value["audio_id"] == PRIMARY_AUDIO_ID
            assert value["state"] == current.stages[stage].model_dump(mode="json")
            assert value["config"] == getattr(current.config_snapshot, stage).model_dump(
                mode="json"
            )
            assert value["result_error"] is None
            results[stage] = value["result"]
        assert results["preprocess"]["kind"] == "preprocess"
        metadata = json.loads((scope.project_dir(pid) / "cache/preprocess.json").read_text())
        assert results["preprocess"]["data"] == metadata
        for stage, name in [
            ("asr", "raw"),
            ("alignment", "aligned"),
            ("translation", "translated"),
        ]:
            assert results[stage]["kind"] == "transcript"
            assert results[stage]["transcript"]["stage"] == name
        raw = results["asr"]["transcript"]["segments"][0]
        assert raw["start"] == 0
        assert raw["translated_text"] is None
        assert results["alignment"]["transcript"]["segments"][0]["start"] == 0.25
        assert results["alignment"]["transcript"]["segments"][0]["translated_text"] is None
        assert results["translation"]["transcript"]["segments"][0]["translated_text"] == "你好。"
        assert results["export"]["kind"] == "outputs"
        assert results["export"]["items"] == (await client.get(base + "/outputs")).json()["items"]
        for item in results["export"]["items"]:
            assert f"/audios/{PRIMARY_AUDIO_ID}/outputs/" in item["download_url"]
            assert (await client.get(item["download_url"])).status_code == 200
        # The compatibility endpoint still returns the latest transcript.
        assert (await client.get(f"/api/v1/projects/{pid}/transcript")).json()[
            "stage"
        ] == "translated"


@pytest.mark.asyncio
async def test_unsubmitted_and_rerun_hide_old_artifacts(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}/audios/{PRIMARY_AUDIO_ID}"
        for stage in PipelineStageName:
            value = (await client.get(base + f"/stages/{stage}")).json()
            assert value["config"] is value["result"] is value["result_error"] is None
        scope = await complete(client, app, pid)
        old_job = scope.load_job(pid)
        await app.state.jobs.semaphore.acquire()
        try:
            response = await client.post(base + "/run", json={"resume": False})
            assert response.status_code == 202
            assert response.json()["job_id"] != old_job.id
            assert (scope.project_dir(pid) / "transcript/translated.json").is_file()
            for stage in PipelineStageName:
                value = (await client.get(base + f"/stages/{stage}")).json()
                assert value["job_id"] == response.json()["job_id"]
                assert value["state"]["status"] == "pending"
                assert value["config"] is not None
                assert value["result"] is value["result_error"] is None
        finally:
            app.state.jobs.semaphore.release()
            await asyncio.gather(*app.state.jobs.tasks.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running", "failed", "skipped"])
async def test_incomplete_alignment_never_falls_back_to_raw(tmp_path, status):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        scope = await complete(client, app, pid)
        current = scope.load_job(pid)
        current.stages["alignment"].status = StageStatus(status)
        scope.save_job(current)
        value = (
            await client.get(f"/api/v1/projects/{pid}/audios/{PRIMARY_AUDIO_ID}/stages/alignment")
        ).json()
        assert value["state"]["status"] == status
        assert value["result"] is value["result_error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", list(PipelineStageName))
async def test_missing_stage_results_do_not_hide_state_or_snapshot(tmp_path, stage):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        scope = await complete(client, app, pid)
        current = scope.load_job(pid)
        artifact = scope.resolve_artifact_path(pid, current.stages[stage].artifact)
        if stage == "export":
            for path in scope.list_outputs(pid):
                path.unlink()
        else:
            artifact.unlink()
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{PRIMARY_AUDIO_ID}/stages/{stage}"
        )
        assert response.status_code == 200
        value = response.json()
        assert value["state"]["status"] == "completed"
        assert value["config"] is not None
        assert value["result"] is None
        assert value["result_error"]["code"] == "STAGE_RESULT_NOT_FOUND"
        assert str(tmp_path) not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["preprocess", "asr", "alignment", "translation"])
@pytest.mark.parametrize("payload", ["broken json", "[]", '{"project_id":"other"}'])
async def test_corrupt_result_is_a_readable_nonfatal_error(tmp_path, stage, payload):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        scope = await complete(client, app, pid)
        current = scope.load_job(pid)
        scope.resolve_artifact_path(pid, current.stages[stage].artifact).write_text(payload)
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{PRIMARY_AUDIO_ID}/stages/{stage}"
        )
        assert response.status_code == 200
        value = response.json()
        # Preprocessing metadata deliberately remains an open dict schema.
        if stage == "preprocess" and payload.startswith("{"):
            assert value["result"]["data"] == json.loads(payload)
        else:
            assert value["result"] is None
            assert value["result_error"]["code"] == "STAGE_RESULT_NOT_FOUND"


@pytest.mark.asyncio
async def test_stage_details_are_audio_scoped_and_validate_ids(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios", payload=b"second"))["audio"]["id"]
        scope = await complete(client, app, pid, aid)
        raw = scope.load_transcript(pid, "raw")
        raw.segments[0].source_text = "second audio"
        scope.save_transcript(pid, "raw", raw, overwrite=True)
        for stage in PipelineStageName:
            value = (await client.get(base + f"/audios/{aid}/stages/{stage}")).json()
            assert value["audio_id"] == aid
            assert value["job_id"] == scope.load_job(pid).id
            assert value["result"] is not None
            primary = (await client.get(base + f"/audios/{PRIMARY_AUDIO_ID}/stages/{stage}")).json()
            assert primary["result"] is primary["config"] is None
        value = (await client.get(base + f"/audios/{aid}/stages/asr")).json()
        assert value["result"]["transcript"]["segments"][0]["source_text"] == "second audio"
        metadata = (await client.get(base + f"/audios/{aid}/stages/preprocess")).json()["result"][
            "data"
        ]
        assert metadata["source_path"].startswith(f"audios/{aid}/")
        for item in (await client.get(base + f"/audios/{aid}/stages/export")).json()["result"][
            "items"
        ]:
            assert f"/audios/{aid}/outputs/" in item["download_url"]
        response = await client.get(base + "/audios/missing/stages/asr")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AUDIO_NOT_FOUND"
        assert (await client.get(base + f"/audios/{aid}/stages/unknown")).status_code == 422
        response = await client.get(f"/api/v1/projects/missing/audios/{aid}/stages/asr")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["preprocess", "asr", "alignment", "translation", "export"])
async def test_stage_artifact_cannot_read_a_sibling_audio(tmp_path, stage):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        primary = await complete(client, app, pid)
        scope = await complete(client, app, pid, aid)
        primary_job = primary.load_job(pid)
        extra_job = scope.load_job(pid)
        for target, replacement in [
            (primary, extra_job.stages[stage].artifact),
            (scope, primary_job.stages[stage].artifact),
            (primary, "../../outside.json"),
        ]:
            current = target.load_job(pid)
            current.stages[stage].artifact = replacement
            target.save_job(current)
            response = await client.get(base + f"/audios/{current.audio_id}/stages/{stage}")
            assert response.status_code == 200
            assert response.json()["result"] is None
            assert response.json()["result_error"]["code"] == "STAGE_RESULT_NOT_FOUND"


@pytest.mark.asyncio
async def test_result_symlink_cannot_escape_the_audio(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid = (await upload(client))["project"]["id"]
        base = f"/api/v1/projects/{pid}"
        aid = (await upload(client, base + "/audios"))["audio"]["id"]
        primary = await complete(client, app, pid)
        extra = await complete(client, app, pid, aid)
        for stage in ["preprocess", "asr"]:
            primary_path = primary.resolve_artifact_path(
                pid, primary.load_job(pid).stages[stage].artifact
            )
            extra_path = extra.resolve_artifact_path(
                pid, extra.load_job(pid).stages[stage].artifact
            )
            primary_path.unlink()
            try:
                primary_path.symlink_to(extra_path)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    pytest.skip("Windows account cannot create symlinks for this security test")
                raise
            value = (await client.get(base + f"/audios/{PRIMARY_AUDIO_ID}/stages/{stage}")).json()
            assert value["result"] is None
            assert value["result_error"]["code"] == "STAGE_RESULT_NOT_FOUND"
