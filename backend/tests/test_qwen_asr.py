"""Fake HTTP protocol tests. No credentials, paid requests, or SDK are required."""

import asyncio
import json
import logging
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from app.adapters import qwen_asr as qwen
from app.domain.inline_connection import InlineConnection
from app.domain.node_execution import NodeData, NodeExecutionContext
from app.nodes import qwen_asr as node
from app.nodes.model_configuration import normalize_service
from app.storage.secret_store import SecretStore

SECRET = "sk-test-secret-do-not-persist"
SIGNED = "https://result.example.test/result.json?Signature=SIGNED-SECRET"
POLICY = {
    "upload_host": "https://oss.example.test",
    "upload_dir": "dashscope-instant/test/run",
    "policy": "POLICY-SECRET",
    "signature": "SIGNATURE-SECRET",
    "oss_access_key_id": "OSS-SECRET",
    "x_oss_object_acl": "private",
    "x_oss_forbid_overwrite": "true",
    "expire_in_seconds": 300,
    "max_file_size_mb": 100,
}
RESULT = {
    "properties": {"original_duration_in_milliseconds": 3834},
    "transcripts": [
        {
            "channel_id": 0,
            "sentences": [
                {
                    "begin_time": 100,
                    "end_time": 3820,
                    "text": "Hello world.",
                    "speaker_id": 1,
                    "words": [
                        {"begin_time": 100, "end_time": 596, "text": "Hello", "punctuation": " "}
                    ],
                    "credential": SECRET,
                }
            ],
        }
    ],
    "file_url": "oss://SHOULD-NOT-PERSIST",
    "signed_url": SIGNED,
}


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, wire, payload):
        self.wire, self.payload = wire, payload
        self.closed = False

    async def __aiter__(self):
        await self.wire.block("result")
        yield json.dumps(self.payload).encode()

    async def aclose(self):
        self.closed = True


class Wire:
    def __init__(self):
        self.requests, self.clients = [], []
        self.chunks, self.upload_prefix = [], b""
        self.submissions, self.cancel_calls, self.active = 0, 0, 0
        self.block_stage = None
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        self.policy = dict(POLICY)
        self.result = json.loads(json.dumps(RESULT))
        self.result_streams = []
        self.overrides = {}
        self.cancel_status = 200
        self.poll_states = []

    async def block(self, stage):
        if stage == self.block_stage:
            self.reached.set()
            await self.release.wait()

    async def handle(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/uploads"):
            await self.block("policy")
            return self.overrides.get("policy") or httpx.Response(
                200, json={"data": self.policy}, headers={"set-cookie": "control=SECRET; Path=/"}
            )
        if request.url.host == "oss.example.test" or request.url.path == "/oss":
            async for part in request.stream:
                self.chunks.append(len(part))
                if not self.upload_prefix:
                    self.upload_prefix = part
                elif len(part) > 100:
                    await self.block("upload")
            return self.overrides.get("upload") or httpx.Response(
                200, headers={"set-cookie": "oss=SECRET; Path=/"}
            )
        if request.url.path.endswith("/asr/transcription"):
            self.submissions += 1
            self.submitted = json.loads(await request.aread())
            await self.block("submit")
            return self.overrides.get("submit") or httpx.Response(
                200,
                json={"output": {"task_id": "task-1", "task_status": "PENDING"}},
                headers={"set-cookie": "control2=SECRET; Path=/"},
            )
        if request.url.path.endswith("/cancel"):
            self.cancel_calls += 1
            await self.block("cancel")
            return httpx.Response(self.cancel_status, json={"request_id": "safe"})
        if request.url.path.endswith("/tasks/task-1"):
            await self.block("poll")
            state = self.poll_states.pop(0) if self.poll_states else "SUCCEEDED"
            return self.overrides.get("poll") or httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": state,
                        "results": [
                            {
                                "subtask_status": "SUCCEEDED",
                                "transcription_url": SIGNED,
                            }
                        ],
                    }
                },
            )
        if request.url.host == "result.example.test" or request.url.path == "/result":
            if "result" in self.overrides:
                return self.overrides["result"]
            stream = ResponseStream(self, self.result)
            self.result_streams.append(stream)
            return httpx.Response(200, stream=stream)
        raise AssertionError(f"Unexpected fake request {request.url.path}")


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, wire):
        self.wire = wire

    async def handle_async_request(self, request):
        self.wire.active += 1
        try:
            return await self.wire.handle(request)
        finally:
            self.wire.active -= 1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    wire = Wire()
    path = tmp_path / "source.wav"
    path.write_bytes(b"a" * (qwen.CHUNK_SIZE * 4 + 11))
    connection = InlineConnection(
        provider_type="qwen_asr",
        base_url="https://control.example.test/api/v1",
        default_timeout=30,
    )
    monkeypatch.setattr(qwen, "POLL_INTERVAL", 0)

    def client(_self):
        value = httpx.AsyncClient(
            transport=Transport(wire), follow_redirects=False, trust_env=False
        )
        wire.clients.append(value)
        return value

    monkeypatch.setattr(qwen._Session, "client", client)
    args = dict(
        audio_path=path,
        project_id="p1",
        language="ja",
        connection=connection,
        api_key=SECRET,
        model=qwen.DEFAULT_MODEL,
        additional_parameters={},
        cancel_event=asyncio.Event(),
    )
    return wire, args


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [qwen.DEFAULT_MODEL, "future-filetrans-same-protocol-v99"])
async def test_protocol_streaming_auth_isolation_and_real_transcript(setup, model, caplog):
    wire, args = setup
    args["model"] = model
    args["additional_parameters"] = {"keep_dialect": True, "vocabulary": {"Authorization": 5}}
    wire.poll_states = ["PENDING", "RUNNING", "SUCCEEDED"]
    with caplog.at_level(logging.DEBUG):
        transcript = await qwen.transcribe(**args)
    assert transcript.language == "ja"
    assert transcript.duration == 3.834
    segment = transcript.segments[0]
    assert (segment.start, segment.end, segment.speaker, segment.channel) == (0.1, 3.82, "1", "0")
    assert segment.metadata["words"][0]["end"] == 0.596
    assert wire.submissions == 1
    assert wire.submitted["model"] == model
    assert list(wire.submitted["input"]) == ["file_urls"]
    assert len(wire.submitted["input"]["file_urls"]) == 1
    assert wire.submitted["input"]["file_urls"][0].startswith("oss://dashscope-instant/test/run/")
    assert wire.submitted["parameters"] == {
        "keep_dialect": True,
        "vocabulary": {"Authorization": 5},
        "language_hints": ["ja"],
    }
    assert wire.requests[0].url.params["model"] == model
    assert b'name="file"' in wire.upload_prefix
    assert wire.upload_prefix.rfind(b'name="file"') > wire.upload_prefix.rfind(b'name="key"')
    assert len(wire.chunks) >= 7
    assert max(wire.chunks[1:]) <= qwen.CHUNK_SIZE
    assert args["additional_parameters"] == {
        "keep_dialect": True,
        "vocabulary": {"Authorization": 5},
    }
    for request in wire.requests:
        control = request.url.host == "control.example.test"
        assert request.headers.get("Authorization") == (f"Bearer {SECRET}" if control else None)
        assert "cookie" not in request.headers
        if request.url.path.endswith("/asr/transcription"):
            assert request.headers["X-DashScope-Async"] == "enable"
            assert request.headers["X-DashScope-OssResourceResolve"] == "enable"
    persisted = transcript.model_dump_json() + caplog.text
    for private in (
        SECRET,
        SIGNED,
        "SIGNATURE-SECRET",
        "POLICY-SECRET",
        "OSS-SECRET",
        "SHOULD-NOT-PERSIST",
    ):
        assert private not in persisted
    assert all(client.is_closed for client in wire.clients)
    assert all(stream.closed for stream in wire.result_streams)


@pytest.mark.asyncio
async def test_explicit_secret_never_enters_connection_or_metadata(setup, caplog, monkeypatch):
    wire, args = setup
    explicit = "sk-test-only-explicit-secret"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "different-environment-secret")
    args["api_key"] = explicit
    with caplog.at_level(logging.DEBUG):
        transcript = await qwen.transcribe(**args)
    for request in wire.requests:
        assert request.headers.get("Authorization") == (
            f"Bearer {explicit}" if request.url.host == "control.example.test" else None
        )
    assert explicit not in args["connection"].model_dump_json()
    assert explicit not in transcript.model_dump_json() + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [None, "", "bad key", "key\n", {}, 1])
async def test_explicit_invalid_secret_never_reads_environment(setup, explicit, monkeypatch):
    wire, args = setup
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("QWEN_TEST_KEY", SECRET)
    args["api_key"] = explicit
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_CREDENTIALS_MISSING"
    assert not wire.requests


@pytest.mark.asyncio
async def test_omitted_secret_is_required_even_when_environment_has_key(setup, monkeypatch):
    wire, args = setup
    monkeypatch.setenv("DASHSCOPE_API_KEY", SECRET)
    monkeypatch.setenv("QWEN_TEST_KEY", SECRET)
    args.pop("api_key")
    with pytest.raises(TypeError, match="api_key"):
        await qwen.transcribe(**args)
    assert not wire.requests and not wire.clients


@pytest.mark.asyncio
async def test_previous_profile_shaped_object_is_not_an_independent_connection(setup, monkeypatch):
    wire, args = setup
    monkeypatch.setenv("QWEN_TEST_KEY", SECRET)
    args["connection"] = SimpleNamespace(
        base_url="https://control.example.test/api/v1",
        api_key_env="QWEN_TEST_KEY",
        default_timeout=30,
    )
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_CONNECTION_INVALID"
    assert SECRET not in str(caught.value)
    assert not wire.requests and not wire.clients


@pytest.mark.asyncio
async def test_profile_keyword_is_no_longer_an_adapter_contract(setup):
    wire, args = setup
    args["profile"] = args.pop("connection")
    with pytest.raises(TypeError, match="profile"):
        await qwen.transcribe(**args)
    assert not wire.requests and not wire.clients


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "wss://control.example.test/api/v1",
        "https://user:password@control.example.test/api/v1",
        f"https://control.example.test/api/v1?api_key={SECRET}",
        "https://control.example.test/api/v1#fragment",
        "https://control.example.test:0/api/v1",
        "https://control.example.test:invalid/api/v1",
        "https://control.example.test/api/with space",
    ],
)
async def test_filetrans_connection_validation_shared_with_node(setup, base_url):
    wire, args = setup
    args["connection"] = args["connection"].model_copy(update={"base_url": base_url})
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_CONNECTION_INVALID"
    with pytest.raises(ValueError) as node_error:
        normalize_service({"protocol": "qwen_filetrans", "endpoint": base_url})
    assert SECRET not in str(caught.value) + str(node_error.value)
    assert not wire.requests and not wire.clients


@pytest.mark.parametrize(
    "parameters",
    [
        [],
        None,
        {"language_hints": ["en"]},
        {"model": "override"},
        {"input": {}},
        {"Authorization": SECRET},
        {"api-key": SECRET},
        {"password": SECRET},
        {"url": "https://x"},
        {"x": float("nan")},
        {"x": "x" * 17000},
        {"x": [[[[[[[[[1]]]]]]]]]},
        {"x": [1] * 2050},
    ],
)
def test_invalid_additional_parameters(parameters):
    with pytest.raises(ValueError):
        qwen.validate_additional_parameters(parameters)
    with pytest.raises(ValueError) as error:
        node.normalize({"additional_parameters": parameters})
    assert SECRET not in str(error.value)


def test_configuration_defaults_and_ports():
    config = node.normalize({})
    assert config == {}
    assert "provider_profile_id" not in config and "api_key_env" not in config
    definition = node.definition()
    assert definition.fields == ()
    assert definition.required_ports == ("audio", "model")
    assert definition.binding_inputs == {"model": "asr_file"}
    assert set(definition.input_ports) == {"audio", "model", "asr_context", "asr_options"}
    from app.domain.node_registry import NODE_DEFINITIONS

    context_fields = {field.key: field for field in NODE_DEFINITIONS["asr_context"].fields}
    model_fields = {field.key: field for field in NODE_DEFINITIONS["model_config"].fields}
    connection_fields = {
        field.key: field for field in NODE_DEFINITIONS["service_connection"].fields
    }
    assert context_fields["language"].input_port == "language"
    assert connection_fields["credential_ref"].input_port is None
    assert model_fields["model_name"].options == []
    assert model_fields["additional_parameters"].type == "json"
    assert model_fields["additional_parameters"].input_type_id == "json_object"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["policy", "upload", "submit", "poll", "result"])
@pytest.mark.parametrize("external_cancel", [False, True])
async def test_each_stage_cancel_joins_requests_and_closes_files(
    setup, monkeypatch, stage, external_cancel
):
    wire, args = setup
    wire.block_stage = stage
    opened = []
    original_open = Path.open

    def tracked_open(path, *positional, **kwargs):
        stream = original_open(path, *positional, **kwargs)
        if path == args["audio_path"] and positional and positional[0] == "rb":
            opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    task = asyncio.create_task(qwen.transcribe(**args))
    await asyncio.wait_for(wire.reached.wait(), 3)
    if external_cancel:
        task.cancel()
        expected = asyncio.CancelledError
    else:
        args["cancel_event"].set()
        expected = qwen.QwenASRCancelled
    with pytest.raises(expected) as caught:
        await asyncio.wait_for(task, 3)
    if not external_cancel:
        assert caught.value.code == "QWEN_ASR_CANCELLED"
        assert caught.value.message and SECRET not in caught.value.message
    assert wire.active == 0
    assert wire.submissions <= 1
    assert wire.cancel_calls == (1 if stage in ("poll", "result") else 0)
    assert all(stream.closed for stream in opened)
    assert all(stream.closed for stream in wire.result_streams)
    assert all(client.is_closed for client in wire.clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_status", [200, 400, 500])
async def test_remote_cancel_is_once_and_never_claims_remote_stopped(setup, cancel_status):
    wire, args = setup
    wire.block_stage, wire.cancel_status = "poll", cancel_status
    task = asyncio.create_task(qwen.transcribe(**args))
    await asyncio.wait_for(wire.reached.wait(), 3)
    args["cancel_event"].set()
    with pytest.raises(qwen.QwenASRCancelled) as caught:
        await task
    assert wire.cancel_calls == 1
    assert "可能继续" in caught.value.message
    assert "48小时" in caught.value.message
    assert (
        caught.value.remote_cancel_outcome
        == {
            200: "pending_cancel_requested",
            400: "not_cancelable",
            500: "unknown",
        }[cancel_status]
    )


@pytest.mark.asyncio
async def test_total_timeout_includes_multiple_stages_and_cleanup_is_bounded(setup, monkeypatch):
    wire, args = setup
    args["connection"] = args["connection"].model_copy(update={"default_timeout": 0.04})
    original = wire.handle

    async def delayed(request):
        await asyncio.sleep(0.015)
        return await original(request)

    wire.handle = delayed
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_TIMEOUT"
    assert wire.active == 0 and wire.submissions <= 1
    assert all(client.is_closed for client in wire.clients)


@pytest.mark.asyncio
async def test_slow_remote_cancel_bounded_after_total_timeout(setup, monkeypatch):
    wire, args = setup
    args["connection"] = args["connection"].model_copy(update={"default_timeout": 0.3})
    wire.block_stage = "poll"
    original = wire.handle

    async def slow_cancel(request):
        if request.url.path.endswith("/cancel"):
            wire.cancel_calls += 1
            await asyncio.Event().wait()
        return await original(request)

    wire.handle = slow_cancel
    monkeypatch.setattr(qwen, "CANCEL_TIMEOUT", 0.02)
    with pytest.raises(qwen.QwenASRError) as caught:
        await asyncio.wait_for(qwen.transcribe(**args), 1)
    assert caught.value.code == "QWEN_TIMEOUT"
    assert wire.cancel_calls == 1 and wire.active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["policy", "submit", "poll", "result"])
async def test_bad_server_response_safe_and_no_submit_retry(setup, stage):
    wire, args = setup
    wire.overrides[stage] = httpx.Response(500, text=f"Bearer {SECRET} {SIGNED}")
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_HTTP_ERROR"
    assert SECRET not in str(caught.value) and SIGNED not in str(caught.value)
    assert wire.submissions <= 1


@pytest.mark.asyncio
async def test_lost_submit_response_never_submits_twice(setup):
    wire, args = setup
    original = wire.handle

    async def lost(request):
        if request.url.path.endswith("/asr/transcription"):
            wire.submissions += 1
            raise httpx.ReadError(f"{SECRET} {SIGNED}", request=request)
        return await original(request)

    wire.handle = lost
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_CONNECTION_FAILED" and wire.submissions == 1
    assert SECRET not in str(caught.value) and SIGNED not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["policy", "submit", "poll", "result"])
async def test_malformed_or_oversized_json_rejected(setup, stage):
    wire, args = setup
    wire.overrides[stage] = httpx.Response(200, text="not json")
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_RESPONSE_INVALID"
    wire.overrides[stage] = httpx.Response(200, content=b"x" * (1024 * 1024 + 1))
    if stage == "result":
        # The result limit is larger than control responses; test it separately.
        return
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_RESPONSE_INVALID" and "过大" in str(caught.value)


@pytest.mark.asyncio
async def test_result_response_size_is_bounded(setup, monkeypatch):
    wire, args = setup
    monkeypatch.setattr(qwen, "MAX_RESULT_BYTES", 64)
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_RESPONSE_INVALID" and "过大" in str(caught.value)
    assert wire.result_streams[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"subtask_status": "FAILED", "transcription_url": SIGNED}],
            }
        },
        {"output": {"task_status": "FAILED", "message": SECRET}},
        {"output": {"task_status": "ALIEN"}},
        {"output": None},
    ],
)
async def test_subtask_and_status_must_be_valid(setup, response):
    wire, args = setup
    wire.overrides["poll"] = httpx.Response(200, json=response)
    with pytest.raises(qwen.QwenASRError):
        await qwen.transcribe(**args)
    assert not wire.result_streams


@pytest.mark.asyncio
async def test_runtime_upload_limit_and_missing_credentials(setup):
    wire, args = setup
    wire.policy["max_file_size_mb"] = "0.001"
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_UPLOAD_TOO_LARGE" and wire.submissions == 0
    args["api_key"] = None
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_CREDENTIALS_MISSING" and len(wire.requests) == 1


@pytest.mark.parametrize(
    "url", ["http://evil.test", "https://user:pass@x.test/a", "file:///x", "https://x.test/#a"]
)
def test_remote_url_rejects_auth_and_insecure_downgrade(url):
    with pytest.raises(qwen.QwenASRError):
        qwen._remote_url(url, "https://control.example.test/api/v1")
    assert qwen._remote_url("http://127.0.0.1:8013/oss", "http://127.0.0.1:8013/api/v1")
    with pytest.raises(qwen.QwenASRError):
        qwen._remote_url("http://127.0.0.1:9999/oss", "http://127.0.0.1:8013/api/v1")


@pytest.mark.asyncio
async def test_redirect_never_followed(setup):
    wire, args = setup
    wire.overrides["result"] = httpx.Response(
        302, headers={"Location": f"https://evil.test/{SECRET}"}
    )
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_HTTP_ERROR"
    assert all(request.url.host != "evil.test" for request in wire.requests)


@pytest.mark.parametrize(
    "sentences",
    [
        [{"text": "hello", "begin_time": -1, "end_time": 10}],
        [{"text": "hello", "begin_time": 20, "end_time": 10}],
        [{"text": "hello", "begin_time": 0}],
        [{"text": "hello", "begin_time": True, "end_time": 10}],
        [{"text": "hello", "begin_time": float("inf"), "end_time": 10}],
        [{"text": ""}],
    ],
)
def test_invalid_or_empty_results_not_fabricated(sentences):
    with pytest.raises(qwen.QwenASRError) as caught:
        qwen.parse_transcript(
            {"transcripts": [{"sentences": sentences}]}, project_id="p", language="ja"
        )
    assert caught.value.code == "QWEN_RESULT_INVALID"


def test_full_text_preserves_null_timestamps_and_exports_txt_only():
    from app.application.workflow_asr import _render_text

    transcript = qwen.parse_transcript(
        {"transcripts": [{"text": "actual text"}]}, project_id="p", language="en"
    )
    assert transcript.segments[0].start is transcript.segments[0].end is None
    assert _render_text(transcript, "txt") == "actual text\n"
    with pytest.raises(ValueError):
        _render_text(transcript, "srt")


@pytest.mark.asyncio
async def test_node_uses_own_frozen_connection_and_publishes_real_transcript(setup, tmp_path):
    wire, args = setup
    from app.application.configuration_evaluator import evaluate_configuration_graph
    from app.domain.workflow import WorkflowSnapshot

    ref = SecretStore(tmp_path).create(
        "p1", "connection", "service_connection", SECRET, request_id="node-test"
    )
    connection_config = {
        "protocol": "qwen_filetrans",
        "endpoint": args["connection"].base_url,
        "credential_ref": ref,
    }
    snapshot = WorkflowSnapshot(
        revision=1,
        workflow_revision=1,
        nodes=[
            {"id": "connection", "type": "service_connection", "config": connection_config},
            {"id": "model", "type": "model_config", "config": {"model_name": args["model"]}},
            {"id": "context", "type": "asr_context", "config": {}},
            {"id": "options", "type": "asr_options", "config": {"timeout_seconds": 30}},
            {"id": "asr1", "type": "qwen_asr", "config": {}},
        ],
        edges=[
            {
                "id": "cm",
                "source": "connection",
                "source_port": "connection",
                "target": "model",
                "target_port": "connection",
            },
            {
                "id": "ma",
                "source": "model",
                "source_port": "model",
                "target": "asr1",
                "target_port": "model",
            },
            {
                "id": "ca",
                "source": "context",
                "source_port": "asr_context",
                "target": "asr1",
                "target_port": "asr_context",
            },
            {
                "id": "oa",
                "source": "options",
                "source_port": "asr_options",
                "target": "asr1",
                "target_port": "asr_options",
            },
        ],
    )
    target = tmp_path / "output" / "transcript.json"
    calls = []

    def artifact_path(*parts):
        calls.append(parts)
        return target

    store = SimpleNamespace(workflow_artifact_path=artifact_path, data_dir=tmp_path)
    run = SimpleNamespace(workflow=snapshot, project_id="p1", audio_id="a1", id="r1")
    project = SimpleNamespace(
        audios=[SimpleNamespace(id="a1", source=SimpleNamespace(language="en"))]
    )
    packets = await evaluate_configuration_graph(store, run, project, args["cancel_event"])
    context = NodeExecutionContext(
        store=store,
        run=run,
        node=snapshot.nodes[-1],
        inputs={
            "audio": NodeData("audio", SimpleNamespace(path=args["audio_path"])),
            "model": packets["model"].outputs["model"],
            "asr_context": packets["context"].outputs["asr_context"],
            "asr_options": packets["options"].outputs["asr_options"],
        },
        project=project,
        cancel_event=args["cancel_event"],
    )
    result = await node.execute(context)
    assert result.artifact.filename == "transcript.json"
    data = json.loads(target.read_bytes())
    assert data["language"] == "en" and data["segments"][0]["source_text"] == "Hello world."
    assert SECRET not in target.read_text() and SIGNED not in target.read_text()
    assert wire.requests[0].url.host == "control.example.test"
    assert calls == [("p1", "a1", "r1", "asr1", "transcript.json")]
    assert wire.submitted["parameters"]["language_hints"] == ["en"]
    assert result.outputs["transcript"].value.transcript.language == "en"
    assert context.node.config == {}
    assert snapshot.nodes[0].config == connection_config


@pytest.mark.asyncio
async def test_eight_megabyte_upload_never_buffers_whole_file(setup):
    wire, args = setup
    with args["audio_path"].open("wb") as output:
        for _ in range(128):
            output.write(b"x" * qwen.CHUNK_SIZE)
    await qwen.transcribe(**args)
    assert len(wire.chunks) == 130  # multipart prefix, 128 chunks, terminator
    assert max(wire.chunks) <= qwen.CHUNK_SIZE
    assert sum(wire.chunks[1:-1]) == 8 * 1024 * 1024


@pytest.mark.asyncio
async def test_cancel_during_disk_read_joins_worker_before_close(setup, monkeypatch):
    wire, args = setup
    entered, release = threading.Event(), threading.Event()
    opened = []
    original_open = Path.open

    class SlowRead:
        def __init__(self, stream):
            self.stream, self.reading = stream, False

        def read(self, size):
            self.reading = True
            entered.set()
            try:
                assert release.wait(3)
                return self.stream.read(size)
            finally:
                self.reading = False

        def close(self):
            assert not self.reading
            self.stream.close()

    def slow_open(path, *positional, **kwargs):
        stream = original_open(path, *positional, **kwargs)
        if path == args["audio_path"] and positional and positional[0] == "rb":
            stream = SlowRead(stream)
            opened.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", slow_open)
    task = asyncio.create_task(qwen.transcribe(**args))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        args["cancel_event"].set()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(qwen.QwenASRCancelled):
            await task
        assert all(item.stream.closed for item in opened)
        assert wire.active == 0 and wire.submissions == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_upload_uses_policy_expiry_without_resubmission(setup):
    wire, args = setup
    wire.policy["expire_in_seconds"] = "0.02"
    wire.block_stage = "upload"
    with pytest.raises(qwen.QwenASRError) as caught:
        await qwen.transcribe(**args)
    assert caught.value.code == "QWEN_UPLOAD_FAILED"
    assert wire.submissions == 0 and wire.active == 0
    assert all(client.is_closed for client in wire.clients)


@pytest.mark.asyncio
async def test_explicit_same_origin_local_http_service(setup):
    wire, args = setup
    args["connection"] = args["connection"].model_copy(
        update={"base_url": "http://127.0.0.1:8013/api/v1"}
    )
    wire.policy["upload_host"] = "http://127.0.0.1:8013/oss"
    wire.overrides["poll"] = httpx.Response(
        200,
        json={
            "output": {
                "task_status": "SUCCEEDED",
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "http://127.0.0.1:8013/result",
                    }
                ],
            }
        },
    )
    await qwen.transcribe(**args)
    for request in wire.requests:
        assert request.headers.get("Authorization") == (
            None if request.url.path in ("/oss", "/result") else f"Bearer {SECRET}"
        )
        assert "cookie" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["txt", "srt", "cancel"])
async def test_registered_node_real_run_parameter_line_history_and_cancel(setup, tmp_path, mode):
    from app.main import create_app

    from test_workflow import create_project, save_graph

    wire, args = setup
    app = create_app(tmp_path / "application-data")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://app"
    ) as client:
        pid, aid = await create_project(client)
        graph = {
            "nodes": [
                {"id": "input", "type": "audio_input", "config": {}},
                {"id": "lang", "type": "string_value", "config": {"value": "fr"}},
                {
                    "id": "qwen",
                    "type": "qwen_asr",
                    "config": {},
                },
                {
                    "id": "connection",
                    "type": "service_connection",
                    "config": {
                        "protocol": "qwen_filetrans",
                        "endpoint": args["connection"].base_url,
                    },
                },
                {
                    "id": "model",
                    "type": "model_config",
                    "config": {"model_name": "future-protocol-model"},
                },
                {"id": "context", "type": "asr_context", "config": {"language": "en"}},
                {"id": "options", "type": "asr_options", "config": {"timeout_seconds": 30}},
                {
                    "id": "output",
                    "type": "text_output",
                    "config": {
                        "format": mode if mode != "cancel" else "txt",
                        "filename_stem": "recognized",
                    },
                },
            ],
            "edges": [
                {
                    "id": "audio",
                    "source": "input",
                    "source_port": "audio",
                    "target": "qwen",
                    "target_port": "audio",
                },
                {
                    "id": "language",
                    "source": "lang",
                    "source_port": "string",
                    "target": "context",
                    "target_port": "language",
                },
                {
                    "id": "cm",
                    "source": "connection",
                    "source_port": "connection",
                    "target": "model",
                    "target_port": "connection",
                },
                {
                    "id": "mq",
                    "source": "model",
                    "source_port": "model",
                    "target": "qwen",
                    "target_port": "model",
                },
                {
                    "id": "cq",
                    "source": "context",
                    "source_port": "asr_context",
                    "target": "qwen",
                    "target_port": "asr_context",
                },
                {
                    "id": "oq",
                    "source": "options",
                    "source_port": "asr_options",
                    "target": "qwen",
                    "target_port": "asr_options",
                },
                {
                    "id": "text",
                    "source": "qwen",
                    "source_port": "transcript",
                    "target": "output",
                    "target_port": "transcript",
                },
            ],
            "request_id": "save-test-key",
            "credential_updates": [
                {"node_id": "connection", "operation": "set", "api_key": SECRET}
            ],
        }
        document = await save_graph(client, pid, 0, graph)
        if mode == "cancel":
            wire.block_stage = "poll"
        created = await client.post(
            f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs",
            json={"workflow_revision": document["workflow_revision"]},
        )
        assert created.status_code == 202, created.text
        rid = created.json()["id"]
        # Mutating the saved node after submission cannot redirect this run.
        changed = {
            "nodes": json.loads(json.dumps(document["nodes"])),
            "edges": document["edges"],
        }
        next(item for item in changed["nodes"] if item["id"] == "connection")["config"][
            "endpoint"
        ] = "https://changed.example.test/api/v1"
        await save_graph(client, pid, document["revision"], changed)
        if mode == "cancel":
            await asyncio.wait_for(wire.reached.wait(), 3)
            response = await client.post(
                f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel"
            )
            assert response.status_code in (200, 202), response.text
        await asyncio.wait_for(asyncio.gather(*list(app.state.jobs.tasks.values())), 5)
        run = (await client.get(f"/api/v1/projects/{pid}/audios/{aid}/workflow-runs/{rid}")).json()
        records = {record["node_id"]: record for record in run["nodes"]}
        assert wire.submitted["parameters"]["language_hints"] == ["fr"]
        assert all(request.url.host != "changed.example.test" for request in wire.requests)
        assert (
            next(item for item in run["workflow"]["nodes"] if item["id"] == "context")["config"][
                "language"
            ]
            == "en"
        )
        assert records["context"]["effective_config"]["language"] == "fr"
        assert records["context"]["parameter_sources"]["language"]["kind"] == "connection"
        assert records["qwen"]["effective_config"] == {}
        if mode == "cancel":
            assert run["status"] == "cancelled"
            assert records["qwen"]["error"]["type"] == "QWEN_ASR_CANCELLED"
            assert "可能继续" in records["qwen"]["error"]["message"]
            assert records["qwen"]["artifact"] is None and wire.cancel_calls == 1
        else:
            assert run["status"] == "completed", run
            artifact = records["output"]["artifact"]
            downloaded = await client.get(artifact["download_url"])
            assert downloaded.status_code == 200 and "Hello world." in downloaded.text
            if mode == "srt":
                assert "00:00:00,100 --> 00:00:03,820" in downloaded.text
        for path in (tmp_path / "application-data").rglob("*.json"):
            saved = path.read_text(encoding="utf-8")
            assert SECRET not in saved and SIGNED not in saved and "POLICY-SECRET" not in saved
        assert not app.state.jobs.reservations
