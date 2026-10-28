import asyncio
import hashlib
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from app.application.workflow_runner import WorkflowRunCancelled, _ffmpeg_process_runner
from app.main import create_app
from app.providers.ffmpeg import PreprocessError
from httpx import ASGITransport, AsyncClient


def wav(payload: bytes, *, channels: int = 1, sample_rate: int = 8000) -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(payload))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, sample_rate * channels, channels, 8)
        + b"data"
        + struct.pack("<I", len(payload))
        + payload
    )


async def project(client, payload=None):
    response = await client.post(
        "/api/v1/projects",
        files={"file": ("input.wav", payload or wav(b"source"), "audio/wav")},
    )
    assert response.status_code == 201, response.text
    value = response.json()["project"]
    return value["id"], value["audios"][0]["id"]


def node(node_id, node_type, **config):
    return {
        "id": node_id,
        "type": node_type,
        "config": config,
        "position": {"x": 0, "y": 0},
    }


def edge(edge_id, source, target):
    return {
        "id": edge_id,
        "source": source,
        "source_port": "audio",
        "target": target,
        "target_port": "audio",
    }


def mixed_graph():
    return {
        "nodes": [
            node("output-direct", "audio_output", filename_stem="direct"),
            node("chain-b", "preprocess", provider="mock"),
            node("chain-a", "preprocess"),
            node("input", "audio_input"),
            node("prefix", "preprocess", provider="mock"),
            node("output-chain", "audio_output", filename_stem="chain"),
            node("output-prefix", "audio_output", filename_stem="prefix"),
        ],
        "edges": [
            edge("e-chain-output", "chain-b", "output-chain"),
            edge("e-prefix-output", "prefix", "output-prefix"),
            edge("e-input-direct", "input", "output-direct"),
            edge("e-chain-b", "chain-a", "chain-b"),
            edge("e-prefix", "input", "prefix"),
            edge("e-input-chain", "input", "chain-a"),
        ],
    }


async def save(client, pid, revision, graph):
    response = await client.put(
        f"/api/v1/projects/{pid}/workflow",
        json={"expected_revision": revision, **graph},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def run(client, app, pid, aid, revision):
    response = await client.post(
        f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
        json={"workflow_revision": revision},
    )
    assert response.status_code == 202, response.text
    await asyncio.gather(*app.state.jobs.tasks.values())
    return response.json()["id"]


@pytest.mark.asyncio
async def test_config_defaults_validation_and_version_semantics(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await project(client)
        graph = mixed_graph()
        first = await save(client, pid, 0, graph)
        nodes = {item["id"]: item for item in first["nodes"]}
        assert nodes["prefix"]["config"] == {
            "provider": "mock",
            "sample_rate": 16000,
            "channels": 1,
            "preserve_original": True,
        }
        assert nodes["chain-a"]["config"] == nodes["prefix"]["config"]
        assert first["workflow_revision"] == 1

        graph["nodes"] = list(reversed(graph["nodes"]))
        second = await save(client, pid, first["revision"], graph)
        assert second["revision"] == 2
        assert second["workflow_revision"] == 1

        for config in (
            {"provider": "shell", "command": "id"},
            {"sample_rate": 7999},
            {"channels": 3},
            {"unexpected": True},
        ):
            invalid = mixed_graph()
            invalid["nodes"][1]["config"] = config
            response = await client.post(
                f"/api/v1/projects/{pid}/workflow/validate",
                json={"nodes": invalid["nodes"], "edges": invalid["edges"]},
            )
            assert response.status_code == 200
            assert response.json()["valid"] is False
            assert any(
                item["code"] == "INVALID_NODE_CONFIG" and item["node_id"] == "chain-b"
                for item in response.json()["errors"]
            )


@pytest.mark.asyncio
async def test_topology_mixed_branches_and_node_downloads(tmp_path):
    payload = wav(b"unique bytes")
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client, payload)
        document = await save(client, pid, 0, mixed_graph())
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        assert detail["status"] == "completed"
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert all(item["status"] == "completed" for item in records.values())
        for node_id in ("chain-a", "chain-b", "prefix", "output-chain", "output-prefix"):
            assert records[node_id]["artifact"]["size_bytes"] == len(payload)
            assert records[node_id]["artifact"]["sha256"] == hashlib.sha256(payload).hexdigest()
        metadata = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/chain-a"
        )
        assert metadata.status_code == 200
        assert metadata.json()["download_url"].endswith("/outputs/chain-a/download")
        downloaded = await client.get(metadata.json()["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.content == payload
        assert downloaded.headers["content-type"].startswith("audio/wav")
        assert records["chain-b"]["artifact"]["filename"] == "preprocessed.wav"
        assert records["output-chain"]["artifact"]["filename"] == "chain.wav"


@pytest.mark.asyncio
async def test_invalid_dag_directions_multi_input_and_unreachable(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, _ = await project(client)

        async def validate(nodes, edges):
            response = await client.post(
                f"/api/v1/projects/{pid}/workflow/validate",
                json={"nodes": nodes, "edges": edges},
            )
            assert response.status_code == 200, response.text
            return response.json()

        good = mixed_graph()
        reverse = await validate(
            good["nodes"],
            [*good["edges"], edge("bad", "output-chain", "chain-b")],
        )
        assert any(item["code"] == "INVALID_EDGE_DIRECTION" for item in reverse["errors"])
        multi = await validate(
            good["nodes"],
            [*good["edges"], edge("multi", "prefix", "chain-b")],
        )
        assert any(item["code"] == "INVALID_NODE_INPUT_COUNT" for item in multi["errors"])
        isolated_nodes = [
            *good["nodes"],
            node("orphan", "preprocess"),
            node("orphan-out", "audio_output", filename_stem="orphan"),
        ]
        isolated_edges = [
            *good["edges"],
            edge("orphan-edge", "orphan", "orphan-out"),
        ]
        isolated = await validate(isolated_nodes, isolated_edges)
        assert any(
            item["code"] == "UNREACHABLE_NODE" and item["node_id"] == "orphan"
            for item in isolated["errors"]
        )
        dead_end_nodes = [*good["nodes"], node("dead", "preprocess")]
        dead_end_edges = [*good["edges"], edge("dead-edge", "input", "dead")]
        dead_end = await validate(dead_end_nodes, dead_end_edges)
        assert any(
            item["code"] == "NODE_HAS_NO_OUTPUT" and item["node_id"] == "dead"
            for item in dead_end["errors"]
        )


@pytest.mark.asyncio
async def test_failed_preprocess_blocks_only_its_branch(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("bad", "preprocess", provider="ffmpeg"),
                node("blocked", "audio_output", filename_stem="blocked"),
                node("good", "preprocess", provider="mock"),
                node("good-out", "audio_output", filename_stem="good"),
            ],
            "edges": [
                edge("bad-edge", "input", "bad"),
                edge("blocked-edge", "bad", "blocked"),
                edge("good-edge", "input", "good"),
                edge("good-out-edge", "good", "good-out"),
            ],
        }
        document = await save(client, pid, 0, graph)
        app.state.jobs.ffmpeg_path = "/definitely/missing/ffmpeg"
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert detail["status"] == "partial"
        assert records["bad"]["status"] == "failed"
        assert records["bad"]["error"]["type"] == "PREPROCESS_FFMPEG_NOT_FOUND"
        assert records["blocked"]["status"] == "blocked"
        assert records["good"]["status"] == records["good-out"]["status"] == "completed"
        assert not any("missing/ffmpeg" in str(item) for item in detail.values())


@pytest.mark.asyncio
async def test_ffmpeg_success_and_failure_with_leftover_temp(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    calls = []

    async def select_runner(command):
        calls.append(command)
        Path(command[-1]).write_bytes(wav(b"normalized", channels=2, sample_rate=48000))
        return subprocess.CompletedProcess(command, 0, b"", b"secret stderr C:\\private")

    import app.application.workflow_runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_make_ffmpeg_runner",
        lambda cancel_event: lambda command: select_runner(command),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("ff", "preprocess", provider="ffmpeg", sample_rate=48000, channels=2),
                node("out", "audio_output", filename_stem="out"),
            ],
            "edges": [
                edge("e1", "input", "ff"),
                edge("e2", "ff", "out"),
            ],
        }
        document = await save(client, pid, 0, graph)
        app.state.jobs.ffmpeg_path = "/server/ffmpeg"
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert detail["status"] == "completed"
        assert records["ff"]["artifact"]["filename"] == "preprocessed.wav"
        assert records["ff"]["artifact"]["media_type"] == "audio/wav"
        assert records["out"]["artifact"]["filename"] == "out.wav"
        downloaded = await client.get(records["ff"]["artifact"]["download_url"])
        assert downloaded.content == wav(b"normalized", channels=2, sample_rate=48000)
        assert calls[0][0] == "/server/ffmpeg"
        assert calls[0][-6:] == ["-ac", "2", "-ar", "48000", "-vn", calls[0][-1]]

        graph["nodes"][1]["config"]["channels"] = 1
        graph["nodes"][1]["config"]["sample_rate"] = 16000
        second = await save(client, pid, document["revision"], graph)

        async def failing(command):
            Path(command[-1]).write_bytes(wav(b"partial", channels=2, sample_rate=48000))
            return subprocess.CompletedProcess(command, 1, b"", b"/root/private/error")

        monkeypatch.setattr(
            runner_module,
            "_make_ffmpeg_runner",
            lambda cancel_event: lambda command: failing(command),
        )
        rid = await run(client, app, pid, aid, second["workflow_revision"])
        failed = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        failed_records = {item["node_id"]: item for item in failed["nodes"]}
        assert failed_records["ff"]["error"]["type"] == "PREPROCESS_FFMPEG_FAILED"
        assert failed_records["out"]["status"] == "blocked"
        assert "/root" not in str(failed)
        node_dir = app.state.store.workflow_run_dir(pid, aid, rid) / "nodes" / "ff"
        assert not list(node_dir.glob("*.tmp*"))


@pytest.mark.asyncio
async def test_ffmpeg_cancel_timeout_and_shutdown_terminate_and_reap(tmp_path, monkeypatch):
    real_event = asyncio.Event

    class Process:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.killed = False
            self.waited = False
            self.wait_started = asyncio.Event()
            self.release_wait = asyncio.Event()
            self.ignore_terminate = False

        async def wait(self):
            self.wait_started.set()
            await self.release_wait.wait()
            self.waited = True
            return self.returncode

        def terminate(self):
            self.terminated = True
            if not self.ignore_terminate:
                self.returncode = -15
                self.release_wait.set()

        def kill(self):
            self.killed = True
            self.returncode = -9
            self.release_wait.set()

    async def scenario():
        import app.application.workflow_runner as runner_module

        processes = []

        async def create(*args, **kwargs):
            process = Process()
            processes.append(process)
            return process

        monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", create)
        monkeypatch.setattr(runner_module, "FFMPEG_TERMINATE_SECONDS", 0.01)
        assert processes == []
        cancel = real_event()
        task = asyncio.create_task(_ffmpeg_process_runner(["ffmpeg"], cancel))
        await asyncio.sleep(0.01)
        cancel.set()
        with pytest.raises(WorkflowRunCancelled):
            await task
        assert processes[0].terminated and processes[0].waited

        async def create_slow(*args, **kwargs):
            process = Process()
            process.ignore_terminate = True
            processes.append(process)
            return process

        monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", create_slow)
        task = asyncio.create_task(_ffmpeg_process_runner(["ffmpeg"], real_event()))
        while len(processes) < 2:
            await asyncio.sleep(0)
        await asyncio.wait_for(processes[1].wait_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[1].terminated and processes[1].killed and processes[1].waited

        spawn_entered = asyncio.Event()
        spawn_release = asyncio.Event()
        spawned_process = Process()

        async def swallow_cancel_create(*args, **kwargs):
            spawn_entered.set()
            try:
                await spawn_release.wait()
            except asyncio.CancelledError:
                # A real spawn can finish concurrently with cancellation. Model
                # that race so the runner's returned-process reaping is checked.
                return spawned_process
            return spawned_process

        monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", swallow_cancel_create)
        monkeypatch.setattr(runner_module, "FFMPEG_TIMEOUT_SECONDS", 300)
        task = asyncio.create_task(_ffmpeg_process_runner(["ffmpeg"], real_event()))
        await asyncio.wait_for(spawn_entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert spawned_process.terminated and spawned_process.waited

        monkeypatch.setattr(runner_module, "FFMPEG_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", create)
        with pytest.raises(PreprocessError) as error:
            await _ffmpeg_process_runner(["ffmpeg"], real_event())
        assert error.value.code == "PREPROCESS_FFMPEG_TIMEOUT"
        assert processes[2].terminated and processes[2].waited

        task = asyncio.create_task(_ffmpeg_process_runner(["ffmpeg"], real_event()))
        while len(processes) < 4:
            await asyncio.sleep(0)
        await asyncio.wait_for(processes[3].wait_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[3].terminated and processes[3].waited

    await scenario()


@pytest.mark.asyncio
async def test_truncated_wav_is_rejected_and_temp_is_removed(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    complete = wav(b"normalized", channels=2, sample_rate=48000)

    async def truncated_runner(command):
        Path(command[-1]).write_bytes(complete[:-8])
        return subprocess.CompletedProcess(command, 0, b"", b"")

    import app.application.workflow_runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "_make_ffmpeg_runner",
        lambda cancel_event: truncated_runner,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("ff", "preprocess", provider="ffmpeg", sample_rate=48000, channels=2),
                node("out", "audio_output"),
            ],
            "edges": [edge("e1", "input", "ff"), edge("e2", "ff", "out")],
        }
        document = await save(client, pid, 0, graph)
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert records["ff"]["status"] == "failed"
        assert records["ff"]["error"]["type"] == "PREPROCESS_OUTPUT_INVALID"
        assert records["out"]["status"] == "blocked"
        node_dir = app.state.store.workflow_run_dir(pid, aid, rid) / "nodes" / "ff"
        assert not list(node_dir.iterdir())


@pytest.mark.asyncio
async def test_shutdown_during_subprocess_spawn_reaps_child_idempotently(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    import app.application.workflow_runner as runner_module

    class Process:
        def __init__(self):
            self.returncode = None
            self.terminated = False
            self.waited = False

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            self.waited = True
            return self.returncode

    process = Process()
    spawn_started = asyncio.Event()

    async def gated_spawn(*_args, **_kwargs):
        spawn_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # The native spawn can complete while shutdown is cancelling its
            # awaiting task. Return the child so the runner must reap it.
            return process
        return process

    monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", gated_spawn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("ff", "preprocess", provider="ffmpeg"),
                node("out", "audio_output"),
            ],
            "edges": [edge("e1", "input", "ff"), edge("e2", "ff", "out")],
        }
        document = await save(client, pid, 0, graph)
        app.state.jobs.ffmpeg_path = "/usr/bin/ffmpeg"
        response = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert response.status_code == 202
        task = app.state.jobs.tasks[(pid, aid)]
        await asyncio.wait_for(spawn_started.wait(), timeout=1)
        shutdown = asyncio.create_task(app.state.jobs.shutdown())
        await asyncio.sleep(0.05)
        await asyncio.wait_for(shutdown, timeout=2)
        assert task.done()
    assert process.terminated
    assert process.waited
    run = app.state.store.load_workflow_run(pid, aid, response.json()["id"])
    assert run.status == "interrupted"
    run_dir = app.state.store.workflow_run_dir(pid, aid, run.id)
    assert not list(run_dir.rglob("*.wav"))
    assert not list(run_dir.rglob("*.tmp*"))


@pytest.mark.asyncio
async def test_real_ffmpeg_mixed_branch_mp3_to_wav_to_mock(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("真实 FFmpeg 测试要求环境中安装 ffmpeg")
    mp3 = tmp_path / "source.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=0.2",
            "-ac",
            "2",
            "-ar",
            "8000",
            "-codec:a",
            "libmp3lame",
            "-y",
            str(mp3),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects",
            files={"file": ("source.mp3", mp3.read_bytes(), "audio/mpeg")},
        )
        assert created.status_code == 201, created.text
        value = created.json()["project"]
        pid, aid = value["id"], value["audios"][0]["id"]
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("ff", "preprocess", provider="ffmpeg", sample_rate=16000, channels=1),
                node("mock", "preprocess", provider="mock"),
                node("direct", "audio_output", filename_stem="direct"),
                node("converted", "audio_output", filename_stem="converted"),
                node("mock-out", "audio_output", filename_stem="mock"),
            ],
            "edges": [
                edge("e-input-ff", "input", "ff"),
                edge("e-ff-mock", "ff", "mock"),
                edge("e-input-direct", "input", "direct"),
                edge("e-ff-out", "ff", "converted"),
                edge("e-mock-out", "mock", "mock-out"),
            ],
        }
        document = await save(client, pid, 0, graph)
        app.state.jobs.ffmpeg_path = ffmpeg
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        detail = (
            await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")
        ).json()
        records = {item["node_id"]: item for item in detail["nodes"]}
        assert detail["status"] == "completed", detail
        assert records["direct"]["artifact"]["media_type"] == "audio/mpeg"
        assert records["direct"]["artifact"]["filename"].endswith(".mp3")
        assert records["ff"]["artifact"]["filename"] == "preprocessed.wav"
        assert records["ff"]["artifact"]["media_type"] == "audio/wav"
        assert records["mock"]["artifact"]["filename"] == "preprocessed.wav"
        assert records["mock"]["artifact"]["media_type"] == "audio/wav"
        assert records["converted"]["artifact"]["filename"] == "converted.wav"
        assert records["mock-out"]["artifact"]["filename"] == "mock.wav"
        assert records["ff"]["artifact"]["sha256"] == records["mock"]["artifact"]["sha256"]
        assert records["ff"]["artifact"]["sha256"] == records["converted"]["artifact"]["sha256"]
        ffmpeg_wav = await client.get(records["ff"]["artifact"]["download_url"])
        assert ffmpeg_wav.status_code == 200
        wav_path = app.state.store.workflow_artifact_path(pid, aid, rid, "ff", "preprocessed.wav")
        with wave.open(str(wav_path), "rb") as stream:
            assert stream.getframerate() == 16000
            assert stream.getnchannels() == 1
            assert stream.getnframes() > 0
        mock_download = await client.get(records["mock"]["artifact"]["download_url"])
        assert mock_download.content == ffmpeg_wav.content
        mock_path = app.state.store.workflow_artifact_path(
            pid, aid, rid, "mock", "preprocessed.wav"
        )
        source_path = app.state.store.scope(pid, aid).source_path(
            pid, app.state.store.audio(pid, aid).source.path
        )
        assert source_path.read_bytes() == mp3.read_bytes()
        assert mock_path.read_bytes() == wav_path.read_bytes()


@pytest.mark.asyncio
async def test_artifact_api_rejects_escape_and_symlink(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await project(client)
        graph = {
            "nodes": [
                node("input", "audio_input"),
                node("pre", "preprocess"),
                node("out", "audio_output"),
            ],
            "edges": [edge("e1", "input", "pre"), edge("e2", "pre", "out")],
        }
        document = await save(client, pid, 0, graph)
        rid = await run(client, app, pid, aid, document["workflow_revision"])
        run_dir = app.state.store.workflow_run_dir(pid, aid, rid)
        artifact = run_dir / "nodes" / "pre" / "preprocessed.wav"
        artifact.unlink()
        artifact.symlink_to(tmp_path / "outside.wav")
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/pre/download"
        )
        assert response.status_code == 404
        response = await client.get(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/..%2Fpre"
        )
        assert response.status_code == 404
