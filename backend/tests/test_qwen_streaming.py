"""Real local WebSocket protocol tests; no paid API, microphone, or account."""

import asyncio
import copy
import json
import logging
import struct
import threading
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.adapters import qwen_asr_streaming as qwen
from app.adapters import qwen_streaming_protocol as protocol
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

SECRET = "sk-private-streaming-test"
UNSAFE = "https://signed.example.test/?Signature=DO-NOT-LOG"
FINAL = {
    "sentence_id": 1,
    "channel_id": 0,
    "sentence_end": True,
    "begin_time": 20,
    "end_time": 120,
    "text": "最终文本。",
    "speaker_id": 2,
    "words": [
        {"begin_time": 20, "end_time": 120, "text": "最终", "punctuation": "。", "fixed": True}
    ],
}


def audio(tmp_path, *, rate=16000, duration=0.005, channels=1, width=2):
    path = tmp_path / "uploaded-without-extension"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\x00" * max(1, int(rate * duration)) * channels * width)
    return path


class Wire:
    def __init__(self):
        self.runs = []
        self.chunks = []
        self.times = []
        self.finishes = 0
        self.handshakes = []
        self.sentences = [copy.deepcopy(FINAL), copy.deepcopy(FINAL)]
        self.bad = None
        self.block = None
        self.reached = asyncio.Event()
        self.closed = asyncio.Event()

    async def pause(self, ws, stage):
        if self.block == stage:
            self.reached.set()
            await ws.wait_closed()

    async def event(self, ws, task_id, event, payload=None):
        await ws.send(
            json.dumps({"header": {"task_id": task_id, "event": event}, "payload": payload or {}})
        )

    async def handler(self, ws):
        try:
            self.handshakes.append(dict(ws.request.headers))
            run = json.loads(await ws.recv())
            self.runs.append(run)
            task_id = run["header"]["task_id"]
            await self.pause(ws, "start")
            if self.bad == "wrong_id":
                await self.event(ws, "wrong-task", "task-started")
                return
            if self.bad == "json":
                await ws.send("not JSON " + SECRET + UNSAFE)
                return
            if self.bad == "oversize":
                await ws.send("x" * (protocol.MAX_EVENT_BYTES + 1))
                return
            await self.event(ws, task_id, "task-started")
            if self.bad == "early_finish":
                await self.event(ws, task_id, "task-finished")
                return
            async for message in ws:
                if isinstance(message, bytes):
                    self.chunks.append(message)
                    self.times.append(asyncio.get_running_loop().time())
                    await self.pause(ws, "send")
                    continue
                finish = json.loads(message)
                assert finish["header"] == {
                    "task_id": task_id,
                    "action": "finish-task",
                    "streaming": "duplex",
                }
                assert finish["payload"] == {"input": {}}
                self.finishes += 1
                await self.pause(ws, "finish")
                if self.bad == "remote":
                    await self.event(ws, task_id, "task-failed", {"error_message": SECRET + UNSAFE})
                    return
                if self.bad == "disconnect":
                    return
                if self.bad == "binary":
                    await ws.send(b"not-json")
                    return
                if self.bad == "unknown":
                    await self.event(ws, task_id, "session.updated")
                    return
                await self.event(
                    ws,
                    task_id,
                    "result-generated",
                    {"output": {"sentence": {"heartbeat": True, "sentence_id": 0}}},
                )
                interim = {**FINAL, "sentence_end": False, "text": "中间内容，不得拼接"}
                if self.bad != "empty":
                    await self.event(
                        ws, task_id, "result-generated", {"output": {"sentence": interim}}
                    )
                for sentence in self.sentences:
                    await self.event(
                        ws,
                        task_id,
                        "result-generated",
                        {
                            "output": {
                                "sentence": sentence,
                                "debug_secret": SECRET,
                                "signed_url": UNSAFE,
                            }
                        },
                    )
                await self.pause(ws, "result")
                await self.event(ws, task_id, "task-finished")
                await ws.wait_closed()
                return
        except ConnectionClosed:
            pass
        finally:
            self.closed.set()


@asynccontextmanager
async def platform(wire, process_request=None):
    async with serve(
        wire.handler,
        "127.0.0.1",
        0,
        process_request=process_request,
        logger=qwen._PRIVATE_LOGGER,
        close_timeout=0.1,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        yield SimpleNamespace(
            base_url=f"ws://127.0.0.1:{port}/api-ws/v1/inference", default_timeout=3
        )


async def transcribe(path, connection, **kwargs):
    defaults = dict(
        audio_path=path,
        connection=connection,
        project_id="project-test",
        language="zh",
        model="new-compatible-model-not-in-any-list",
        additional_parameters={},
        cancel_event=asyncio.Event(),
        api_key=SECRET,
    )
    defaults.update(kwargs)
    return await qwen.QwenStreamingASRAdapter().transcribe(**defaults)


def track_reads(monkeypatch, path):
    opened, reads = [], []
    original = Path.open

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, key):
            return getattr(self.stream, key)

        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 64 * 1024
            return self.stream.read(size)

    def open_file(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        if self == path:
            opened.append(stream)
            return Tracked(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_file)
    return opened, reads


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [8000, 16000, 22050, 44100, 4000000])
async def test_real_ws_protocol_actual_rate_bounded_stream_and_final_only(
    tmp_path, monkeypatch, caplog, rate
):
    path = audio(tmp_path, rate=rate, duration=0.04)
    expected = path.read_bytes()
    opened, reads = track_reads(monkeypatch, path)
    wire = Wire()
    caplog.set_level(logging.DEBUG)
    async with platform(wire) as connection:
        result = await transcribe(
            path, connection, additional_parameters={"vocabulary": {"Authorization": "普通热词"}}
        )
    assert len(wire.runs) == wire.finishes == 1
    run = wire.runs[0]
    assert run["payload"]["model"] == "new-compatible-model-not-in-any-list"
    assert run["payload"]["input"] == {}
    assert run["payload"]["parameters"] == {
        "format": "wav",
        "sample_rate": rate,
        "language_hints": ["zh"],
        "vocabulary": {"Authorization": "普通热词"},
    }
    assert wire.handshakes[0]["authorization"] == "Bearer " + SECRET
    assert "cookie" not in wire.handshakes[0]
    assert b"".join(wire.chunks) == expected
    assert max(map(len, wire.chunks)) <= 64 * 1024
    assert max(reads) <= 64 * 1024
    assert all(stream.closed for stream in opened)
    assert len(result.segments) == 1
    assert result.stage == "raw"
    segment = result.segments[0]
    assert (segment.start, segment.end, segment.speaker) == (0.02, 0.12, "2")
    assert segment.source_text == "最终文本。"
    assert segment.metadata["words"][0]["start"] == 0.02
    assert result.duration == pytest.approx(0.04)
    assert SECRET not in result.model_dump_json() + caplog.text
    assert UNSAFE not in result.model_dump_json() + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad,code",
    [
        ("wrong_id", "PROTOCOL_INVALID"),
        ("json", "PROTOCOL_INVALID"),
        ("oversize", "RESULT_TOO_LARGE"),
        ("early_finish", "PROTOCOL_INVALID"),
        ("remote", "REMOTE_FAILED"),
        ("disconnect", "CONNECTION_FAILED"),
        ("binary", "PROTOCOL_INVALID"),
        ("unknown", "PROTOCOL_INVALID"),
        ("empty", "RESULT_EMPTY"),
        ("interim_only", "PROTOCOL_INVALID"),
    ],
)
async def test_bad_remote_events_are_safe_and_never_replayed(tmp_path, caplog, bad, code):
    wire = Wire()
    wire.bad = bad
    if bad in {"empty", "interim_only"}:
        wire.sentences = []
    caplog.set_level(logging.DEBUG)
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_" + code
    assert len(wire.runs) == 1
    assert SECRET not in str(caught.value) + repr(caught.value) + caplog.text
    assert UNSAFE not in str(caught.value) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"text": "conflicting final"},
        {"begin_time": -1},
        {"end_time": None},
        {"begin_time": True},
        {"end_time": 1},
        {"words": "bad"},
        {"speaker_id": UNSAFE},
        {"channel_id": True},
        {"sentence_id": 0},
    ],
)
async def test_conflicting_or_invalid_final_fails(tmp_path, changes):
    wire = Wire()
    wire.sentences[1].update(changes)
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_PROTOCOL_INVALID"


@pytest.mark.asyncio
async def test_channel_sentence_ids_missing_timestamps_and_whitelist(tmp_path):
    wire = Wire()
    wire.sentences = [
        {**FINAL, "sentence_id": 2, "begin_time": None, "end_time": None},
        {**FINAL, "channel_id": 1},
        copy.deepcopy(FINAL),
    ]
    async with platform(wire) as connection:
        result = await transcribe(audio(tmp_path), connection)
    assert [segment.id for segment in result.segments] == ["stream_0_1", "stream_0_2", "stream_1_1"]
    assert result.segments[1].start is result.segments[1].end is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["start", "send", "finish", "result"])
@pytest.mark.parametrize("outer_cancel", [False, True])
async def test_cancel_each_ws_stage_joins_tasks_closes_file_and_socket(
    tmp_path, monkeypatch, stage, outer_cancel
):
    path = audio(tmp_path, duration=2 if stage == "send" else 0.005)
    opened, _ = track_reads(monkeypatch, path)
    wire = Wire()
    wire.block = stage
    cancel = asyncio.Event()
    async with platform(wire) as connection:
        task = asyncio.create_task(transcribe(path, connection, cancel_event=cancel))
        await asyncio.wait_for(wire.reached.wait(), 2)
        if outer_cancel:
            task.cancel()
        else:
            cancel.set()
        with pytest.raises(qwen.QwenStreamingCancelled) as caught:
            await asyncio.wait_for(task, 2)
        assert caught.value.code == "QWEN_STREAM_CANCELLED"
        await asyncio.wait_for(wire.closed.wait(), 1)
        assert len(wire.runs) == 1
        assert all(stream.closed for stream in opened)
        assert not [
            task
            for task in asyncio.all_tasks()
            if not task.done() and "QwenStreamingASRAdapter" in task.get_coro().__qualname__
        ]


@pytest.mark.asyncio
async def test_cancel_connect_and_before_start(tmp_path):
    reached, release = asyncio.Event(), asyncio.Event()

    async def handshake(ws, request):
        reached.set()
        await release.wait()

    wire = Wire()
    async with platform(wire, handshake) as connection:
        cancel = asyncio.Event()
        task = asyncio.create_task(transcribe(audio(tmp_path), connection, cancel_event=cancel))
        await asyncio.wait_for(reached.wait(), 1)
        cancel.set()
        try:
            with pytest.raises(qwen.QwenStreamingCancelled):
                await asyncio.wait_for(task, 2)
        finally:
            release.set()
        assert not wire.runs
        with pytest.raises(qwen.QwenStreamingCancelled):
            await transcribe(audio(tmp_path), connection, cancel_event=cancel)


@pytest.mark.asyncio
async def test_cancel_joins_active_disk_worker_before_handle_close(tmp_path, monkeypatch):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = qwen._inspect_wav

    def slow_read(stream):
        entered.set()
        release.wait(2)
        assert not stream.closed
        try:
            return original(stream)
        finally:
            exited.set()

    monkeypatch.setattr(qwen, "_inspect_wav", slow_read)
    cancel = asyncio.Event()
    wire = Wire()
    path = audio(tmp_path)
    opened, _ = track_reads(monkeypatch, path)
    async with platform(wire) as connection:
        task = asyncio.create_task(transcribe(path, connection, cancel_event=cancel))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        cancel.set()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(qwen.QwenStreamingCancelled):
            await task
    assert exited.is_set()
    assert not wire.runs
    assert all(stream.closed for stream in opened)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["start", "send", "finish", "result"])
async def test_total_timeout_cleans_every_stage(tmp_path, monkeypatch, stage):
    path = audio(tmp_path, duration=2 if stage == "send" else 0.005)
    opened, _ = track_reads(monkeypatch, path)
    wire = Wire()
    wire.block = stage
    async with platform(wire) as connection:
        connection.default_timeout = 0.05
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(path, connection)
        assert caught.value.code == "QWEN_STREAM_TIMEOUT"
    assert all(stream.closed for stream in opened)
    assert len(wire.runs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["text", "stereo", "pcm8", "float", "truncated", "size", "odd", "bad_rate"]
)
async def test_unsupported_and_invalid_files_fail_before_network(tmp_path, kind):
    path = audio(tmp_path, channels=2 if kind == "stereo" else 1, width=1 if kind == "pcm8" else 2)
    data = bytearray(path.read_bytes())
    if kind == "text":
        data = bytearray(b"ID3 an mp3 pretending to be WAV")
    elif kind == "float":
        struct.pack_into("<H", data, 20, 3)
    elif kind == "truncated":
        data = data[:-1]
    elif kind == "size":
        struct.pack_into("<I", data, 40, len(data) + 100)
    elif kind == "odd":
        struct.pack_into("<I", data, 40, 159)
    elif kind == "bad_rate":
        struct.pack_into("<I", data, 24, 0)
    path.write_bytes(data)
    wire = Wire()
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(path, connection)
    assert caught.value.code in {"QWEN_STREAM_AUDIO_INVALID", "QWEN_STREAM_AUDIO_UNSUPPORTED"}
    assert not wire.runs and not wire.handshakes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [(401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (500, "HTTP_FAILED"), (302, "HTTP_FAILED")],
)
async def test_handshake_failure_and_redirect_never_leak_key(tmp_path, caplog, status, code):
    wire, target = Wire(), Wire()
    async with platform(target) as other:

        def reject(ws, request):
            headers = Headers({"Location": other.base_url})
            return Response(status, "Error", headers, (SECRET + UNSAFE).encode())

        async with platform(wire, reject) as connection:
            caplog.set_level(logging.DEBUG)
            with pytest.raises(qwen.QwenStreamingError) as caught:
                await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_" + code
    assert not target.handshakes and not target.runs
    assert SECRET not in caplog.text + str(caught.value)
    assert UNSAFE not in caplog.text + str(caught.value)


@pytest.mark.parametrize(
    "key",
    [
        "model",
        "input",
        "format",
        "sample_rate",
        "language_hints",
        "Authorization",
        "api_key",
        "task_id",
        "header",
    ],
)
def test_parameters_cannot_override_protocol_credentials_or_audio(key):
    with pytest.raises(ValueError):
        protocol.validate_additional_parameters({key: "not-allowed"})


@pytest.mark.parametrize(
    "value", [[], {"x": float("nan")}, {"x": "x" * 17000}, {"x": [[[[[[[[[{}]]]]]]]]]}]
)
def test_parameters_are_bounded_json(value):
    with pytest.raises(ValueError):
        protocol.validate_additional_parameters(value)


@pytest.mark.asyncio
async def test_aggregate_result_limit_and_audio_mutation(tmp_path, monkeypatch):
    wire = Wire()
    monkeypatch.setattr(qwen, "MAX_RESULT_BYTES", 10)
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_RESULT_TOO_LARGE"

    monkeypatch.setattr(qwen, "MAX_RESULT_BYTES", protocol.MAX_RESULT_BYTES)
    wire = Wire()
    path = audio(tmp_path, duration=0.12)
    original = qwen._inspect_wav

    def inspect_then_mutate(stream):
        result = original(stream)
        with open(path, "ab") as changed:
            changed.write(b"\x00\x00")
        return result

    monkeypatch.setattr(qwen, "_inspect_wav", inspect_then_mutate)
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(path, connection)
    assert caught.value.code == "QWEN_STREAM_AUDIO_INVALID"
    assert wire.finishes == 0


@pytest.mark.asyncio
async def test_real_time_pacing_does_not_push_whole_file_immediately(tmp_path):
    wire = Wire()
    async with platform(wire) as connection:
        await transcribe(audio(tmp_path, duration=0.22), connection)
    assert len(wire.times) >= 3
    assert wire.times[1] - wire.times[0] >= 0.07
    assert wire.times[2] - wire.times[0] >= 0.17


@pytest.mark.asyncio
async def test_connect_timeout_and_close_timeout_are_part_of_total_deadline(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def slow_handshake(ws, request):
        await release.wait()

    wire = Wire()
    async with platform(wire, slow_handshake) as connection:
        connection.default_timeout = 0.02
        try:
            with pytest.raises(qwen.QwenStreamingError) as caught:
                await transcribe(audio(tmp_path), connection)
            assert caught.value.code == "QWEN_STREAM_TIMEOUT"
        finally:
            release.set()
    assert not wire.runs

    close_completed = asyncio.Event()
    original = qwen._close

    async def slow_close(ws):
        try:
            await asyncio.sleep(0.06)
        finally:
            await original(ws)
            close_completed.set()

    monkeypatch.setattr(qwen, "_close", slow_close)
    async with platform(Wire()) as connection:
        connection.default_timeout = 0.03
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_TIMEOUT"
    assert close_completed.is_set()


@pytest.mark.asyncio
async def test_cancel_during_sending_read_joins_worker_and_preserves_handle(tmp_path, monkeypatch):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = qwen._read_audio_chunk

    def slow_read(stream, size, identity):
        entered.set()
        release.wait(2)
        assert not stream.closed
        try:
            return original(stream, size, identity)
        finally:
            exited.set()

    monkeypatch.setattr(qwen, "_read_audio_chunk", slow_read)
    path = audio(tmp_path)
    opened, _ = track_reads(monkeypatch, path)
    cancel = asyncio.Event()
    wire = Wire()
    async with platform(wire) as connection:
        task = asyncio.create_task(transcribe(path, connection, cancel_event=cancel))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        cancel.set()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(qwen.QwenStreamingCancelled):
            await task
    assert exited.is_set()
    assert all(stream.closed for stream in opened)
    assert len(wire.runs) == 1
    assert wire.finishes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://example.test/api-ws/v1/inference",
        "wss://user:password@example.test/ws",
        "wss://example.test/ws?api_key=private",
        "wss://example.test/ws#fragment",
        "https://example.test/ws",
        "wss://example.test:invalid/ws",
    ],
)
async def test_invalid_endpoint_fails_without_network(tmp_path, endpoint):
    connection = SimpleNamespace(base_url=endpoint, default_timeout=1)
    with pytest.raises(qwen.QwenStreamingError) as caught:
        await transcribe(audio(tmp_path), connection)
    assert caught.value.code == "QWEN_STREAM_CONNECTION_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "\n", "private\r\nAuthorization: other"])
async def test_secret_is_explicit_and_never_read_from_environment(tmp_path, monkeypatch, key):
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    wire = Wire()
    async with platform(wire) as connection:
        with pytest.raises(qwen.QwenStreamingError) as caught:
            await transcribe(audio(tmp_path), connection, api_key=key)
    assert caught.value.code == "QWEN_STREAM_API_KEY_REQUIRED"
    assert not wire.handshakes
