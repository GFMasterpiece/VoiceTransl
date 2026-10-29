import asyncio
import subprocess
import threading
import wave
from io import BytesIO
from pathlib import Path

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


def wav_bytes(*, sample_rate=16000, channels=1, payload=b"\x00\x01\x02\x03"):
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(payload)
    return stream.getvalue()


def node(node_id, node_type, **config):
    return {"id": node_id, "type": node_type, "config": config, "position": {"x": 0, "y": 0}}


def edge(edge_id, source, target, port="audio"):
    return {
        "id": edge_id,
        "source": source,
        "source_port": port,
        "target": target,
        "target_port": port,
    }


async def create_project(client, payload=None):
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("input.wav", payload or wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    project = response.json()["project"]
    return project["id"], project["audios"][0]["id"]


async def save(client, pid, graph, revision=0):
    response = await client.put(
        f"/api/v1/projects/{pid}/workflow", json={"expected_revision": revision, **graph}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def run(client, app, pid, aid, revision):
    response = await client.post(
        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
        json={"workflow_revision": revision},
    )
    assert response.status_code == 202, response.text
    await asyncio.gather(*list(app.state.jobs.tasks.values()))
    return response.json()["id"]


@pytest.mark.asyncio
async def test_mock_text_only_and_multiple_outputs(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("asr", "asr", language="en"),
                node("txt", "text_output", filename_stem="plain", format="txt"),
                node("srt", "text_output", filename_stem="captions", format="srt"),
            ],
            "edges": [
                edge("e1", "input", "asr"),
                edge("e2", "asr", "txt", "transcript"),
                edge("e3", "asr", "srt", "transcript"),
            ],
        }
        document = await save(client, pid, graph)
        assert document["nodes"][1]["config"]["provider"] == "mock"
        assert document["nodes"][1]["config"]["language"] == "en"
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        assert detail["status"] == "completed", detail
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert records["asr"]["artifact"]["port"] == "transcript"
        assert records["asr"]["artifact"]["media_type"] == "application/json"
        assert records["txt"]["artifact"]["media_type"] == "text/plain"
        assert records["srt"]["artifact"]["media_type"] == "application/x-subrip"
        txt = await client.get(records["txt"]["artifact"]["download_url"])
        assert txt.status_code == 200
        assert txt.content.decode("utf-8").startswith("こんにちは。\n")
        assert "translated_text" not in records["asr"]
        artifact = (
            app.state.store.workflow_run_dir(pid, aid, rid) / "nodes" / "asr" / "transcript.json"
        ).read_text(encoding="utf-8")
        assert '"stage": "raw"' in artifact
        assert '"language": "en"' in artifact
        assert str(tmp_path) not in artifact


@pytest.mark.asyncio
async def test_txt_survives_missing_timestamps_but_subtitle_branch_fails(tmp_path, monkeypatch):
    import app.application.job_manager as manager_module
    import app.application.workflow_asr as workflow_module
    from app.application.workflow_asr import ASRSettings
    from app.domain import Segment, Transcript

    class NoTimestamps:
        async def transcribe(self, audio, project):
            return Transcript(
                project_id=project.id,
                stage="raw",
                language="ja",
                duration=0,
                segments=[Segment(id="one", source_text="first\r\nsecond\rthird")],
            )

    original = manager_module.execute_workflow_run

    async def capture(store, run, cancel_event, **kwargs):
        settings = kwargs["asr_settings"]
        return await original(
            store,
            run,
            cancel_event,
            **{
                **kwargs,
                "asr_settings": ASRSettings(**{**settings.__dict__, "process_factory": None}),
            },
        )

    class PatchedProvider(NoTimestamps):
        pass

    monkeypatch.setattr(workflow_module, "MockASRProvider", lambda _delay: PatchedProvider())
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("asr", "asr", language="en"),
                node("txt", "text_output", filename_stem="plain", format="txt"),
                node("srt", "text_output", filename_stem="captions", format="srt"),
                node("audio", "audio_output", filename_stem="copy"),
            ],
            "edges": [
                edge("e1", "input", "asr"),
                edge("e2", "asr", "txt", "transcript"),
                edge("e3", "asr", "srt", "transcript"),
                edge("e4", "input", "audio"),
            ],
        }
        document = await save(client, pid, graph)
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert detail["status"] == "partial"
        assert records["asr"]["status"] == "completed"
        assert records["txt"]["status"] == "completed"
        assert records["audio"]["status"] == "completed"
        assert records["srt"]["status"] == "failed"
        assert records["srt"]["error"]["type"] == "TEXT_OUTPUT_TIMESTAMPS_INVALID"
        content = (await client.get(records["txt"]["artifact"]["download_url"])).content
        assert content == b"first\nsecond\nthird\n"
        transcript = (
            app.state.store.workflow_run_dir(pid, aid, rid) / "nodes" / "asr" / "transcript.json"
        ).read_text()
        assert "first\\r\\nsecond" in transcript


@pytest.mark.asyncio
async def test_text_only_cancel_is_terminal_cancelled_and_has_no_late_artifact(tmp_path):
    app = create_app(tmp_path)
    app.state.jobs.delay_ms = 1500
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("asr", "asr"),
                node("txt", "text_output"),
            ],
            "edges": [edge("e1", "input", "asr"), edge("e2", "asr", "txt", "transcript")],
        }
        document = await save(client, pid, graph)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202
        rid = response.json()["id"]
        task = app.state.jobs.tasks[(pid, aid)]
        for _ in range(200):
            current = app.state.store.load_workflow_run(pid, aid, rid)
            if any(item.status.value == "running" for item in current.nodes):
                break
            await asyncio.sleep(0.01)
        cancel = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
        )
        assert cancel.status_code == 202
        await asyncio.wait_for(task, timeout=3)
        final = app.state.store.load_workflow_run(pid, aid, rid)
        assert final.status == "cancelled"
        node_dir = app.state.store.workflow_run_dir(pid, aid, rid) / "nodes"
        assert not list(node_dir.rglob("*.json"))
        assert not list(node_dir.rglob("*.txt"))
        assert not list(node_dir.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_mixed_graph_crisp_cancel_and_shutdown_reap_fake_process(tmp_path):

    processes = []

    class Process:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False
            self.waited = False
            self.communicate_started = asyncio.Event()

        async def communicate(self):
            self.communicate_started.set()
            await asyncio.Event().wait()

        async def wait(self):
            self.waited = True
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

    async def factory(*_command):
        process = Process()
        processes.append(process)
        return process

    app = create_app(tmp_path)
    app.state.jobs.crispasr_process_factory = factory
    executable = tmp_path / "crispasr"
    executable.write_bytes(b"fake")
    executable.chmod(0o755)
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.gguf").write_bytes(b"model")
    app.state.jobs.crispasr_executable = executable
    app.state.jobs.crispasr_model_dir = models
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("asr", "asr", provider="crispasr", model="model.gguf"),
                node("out", "text_output"),
                node("audio", "audio_output", filename_stem="copy"),
            ],
            "edges": [
                edge("e1", "input", "asr"),
                edge("e2", "asr", "out", "transcript"),
                edge("e3", "input", "audio"),
            ],
        }
        document = await save(client, pid, graph)
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202
        rid = response.json()["id"]
        task = app.state.jobs.tasks[(pid, aid)]
        for _ in range(300):
            if processes:
                await asyncio.wait_for(processes[0].communicate_started.wait(), timeout=1)
                break
            await asyncio.sleep(0.01)
        assert processes
        cancel = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
        )
        assert cancel.status_code == 202
        await asyncio.wait_for(task, timeout=3)
        final = app.state.store.load_workflow_run(pid, aid, rid)
        assert final.status == "cancelled"
        assert processes[0].terminated and processes[0].waited
        records = {item.node_id: item for item in final.nodes}
        assert records["asr"].status == "cancelled"
        assert records["out"].status == "cancelled"
        assert records["audio"].status in {"completed", "cancelled"}
        if records["audio"].status == "completed":
            assert records["audio"].artifact is not None


@pytest.mark.asyncio
async def test_repeated_cancel_and_task_cancel_join_thread_without_late_publish(
    tmp_path, monkeypatch
):
    import app.application.workflow_asr as workflow_module

    entered = threading.Event()
    release = threading.Event()
    done = threading.Event()
    original = workflow_module._write_bytes_atomic

    def blocked_write(path, payload, cancellation, publish_lock):
        entered.set()
        release.wait(2)
        try:
            return original(path, payload, cancellation, publish_lock)
        finally:
            done.set()

    monkeypatch.setattr(workflow_module, "_write_bytes_atomic", blocked_write)

    async def scenario(cancel_mode):
        entered.clear()
        release.clear()
        done.clear()
        app = create_app(tmp_path)
        app.state.jobs.delay_ms = 0
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pid, aid = await create_project(client)
            graph = {
                "nodes": [
                    node("input", "audio_input"),
                    node("asr", "asr"),
                    node("txt", "text_output"),
                ],
                "edges": [edge("e1", "input", "asr"), edge("e2", "asr", "txt", "transcript")],
            }
            document = await save(client, pid, graph)
            response = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert response.status_code == 202
            rid = response.json()["id"]
            task = app.state.jobs.tasks[(pid, aid)]
            try:
                for _ in range(200):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert entered.is_set()
                if cancel_mode == "event":
                    first = await client.post(
                        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
                    )
                    second = await client.post(
                        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
                    )
                    assert first.status_code == 202
                    assert second.status_code in {200, 202}
                    expected = "cancelled"
                else:
                    task.cancel()
                    expected = "interrupted"
                await asyncio.sleep(0.05)
                assert not task.done()
            finally:
                release.set()
            if cancel_mode == "event":
                await asyncio.wait_for(task, timeout=3)
            else:
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
            assert done.is_set()
            await asyncio.sleep(0.05)
            final = app.state.store.load_workflow_run(pid, aid, rid)
            assert final.status == expected
            node_dir = app.state.store.workflow_run_dir(pid, aid, rid) / "nodes"
            assert not list(node_dir.rglob("*.json"))
            assert not list(node_dir.rglob("*.txt"))
            assert not list(node_dir.rglob("*.tmp*"))

    await scenario("event")
    await scenario("task")


@pytest.mark.asyncio
async def test_queued_workflow_captures_asr_and_ffmpeg_settings_at_enqueue(tmp_path, monkeypatch):
    calls = []
    ffmpeg_calls = []

    class Process:
        def __init__(self, command):
            self.command = command
            self.returncode = None

        async def communicate(self):
            output_base = Path(self.command[self.command.index("--output-file") + 1])
            output_base.with_suffix(".srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
            )
            self.returncode = 0
            return b"untrusted stdout", b"/private/path/stderr"

        async def wait(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    async def factory(*command):
        calls.append(command)
        return Process(command)

    async def fake_ffmpeg(command):
        ffmpeg_calls.append(command)
        Path(command[-1]).write_bytes(wav_bytes())
        return subprocess.CompletedProcess(command, 0, b"", b"")

    import app.application.workflow_runner as runner_module

    monkeypatch.setattr(runner_module, "_make_ffmpeg_runner", lambda _cancel: fake_ffmpeg)

    def provider(name, model):
        binary = tmp_path / name
        binary.write_bytes(b"fake")
        binary.chmod(0o755)
        models = tmp_path / f"{name}-models"
        models.mkdir()
        (models / model).write_bytes(b"model")
        return binary, models

    old_executable, old_models = provider("old-crispasr", "old.gguf")
    new_executable, new_models = provider("new-crispasr", "new.gguf")
    app = create_app(tmp_path)
    app.state.jobs.crispasr_executable = old_executable
    app.state.jobs.crispasr_model_dir = old_models
    app.state.jobs.crispasr_process_factory = factory
    app.state.jobs.ffmpeg_path = "/old/ffmpeg"
    await app.state.jobs.semaphore.acquire()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            pid, aid = await create_project(client)
            graph = {
                "nodes": [
                    node("input", "audio_input"),
                    node("pre", "preprocess", provider="ffmpeg"),
                    node("asr", "asr", provider="crispasr", model="old.gguf", language="en"),
                    node("txt", "text_output", format="txt"),
                ],
                "edges": [
                    edge("e1", "input", "pre"),
                    edge("e2", "pre", "asr"),
                    edge("e3", "asr", "txt", "transcript"),
                ],
            }
            document = await save(client, pid, graph)
            queued = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": document["workflow_revision"]},
            )
            assert queued.status_code == 202

            app.state.jobs.crispasr_executable = new_executable
            app.state.jobs.crispasr_model_dir = new_models
            app.state.jobs.ffmpeg_path = "/new/ffmpeg"
            app.state.jobs.semaphore.release()
            await asyncio.gather(*list(app.state.jobs.tasks.values()))
            detail = (
                await client.get(
                    f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{queued.json()['id']}"
                )
            ).json()
            assert detail["status"] == "completed", detail
            assert ffmpeg_calls[0][0] == "/old/ffmpeg"
            assert calls[0][0] == str(old_executable)
            model = Path(calls[0][calls[0].index("--model") + 1])
            assert model.parent == old_models.resolve()
            assert model.name == "old.gguf"

            graph["nodes"][2]["config"]["model"] = "new.gguf"
            second_document = await save(client, pid, graph, document["revision"])
            second = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
                json={"workflow_revision": second_document["workflow_revision"]},
            )
            assert second.status_code == 202
            await asyncio.gather(*list(app.state.jobs.tasks.values()))
            assert ffmpeg_calls[1][0] == "/new/ffmpeg"
            assert calls[1][0] == str(new_executable)
            assert Path(calls[1][calls[1].index("--model") + 1]).name == "new.gguf"
    finally:
        if app.state.jobs.semaphore.locked():
            app.state.jobs.semaphore.release()
