import asyncio
import hashlib
import shutil
import threading
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest
import pytest_asyncio
from app.application.workflow_asr import TranscriptData
from app.application.workflow_checkpoints import (
    _copy_file,
    _hash_file,
    capture_source_identity,
    copy_checkpoint,
    create_checkpoint,
    load_checkpoint,
)
from app.application.workflow_media import (
    AudioData,
    WorkflowRunCancelled,
    _source_audio,
    write_audio_artifact,
)
from app.domain.checkpoint import CheckpointCodec, CheckpointUnavailable
from app.domain.node_contracts import NodeDefinition
from app.domain.node_execution import NodeExecutionContext, NodeResult
from app.domain.node_registry import NODE_DEFINITIONS, PORT_DEFINITIONS, TYPE_DEFINITIONS
from app.domain.transcript import Segment, Transcript
from app.domain.workflow import (
    WorkflowArtifact,
    WorkflowNode,
    WorkflowNodeRecord,
    WorkflowRun,
    workflow_snapshot,
)
from app.main import create_app
from app.nodes.checkpoint_codecs import AUDIO_CODEC
from httpx import ASGITransport, AsyncClient

from test_workflow import create_project


@pytest_asyncio.fixture
async def checkpoint_setup(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pid, aid = await create_project(client)
    store = app.state.store

    def new_run():
        document = store.load_workflow(pid)
        run = WorkflowRun(
            id=f"wfr_{uuid4().hex}",
            project_id=pid,
            audio_id=aid,
            workflow_revision=document.revision,
            workflow=workflow_snapshot(document),
        )
        store.create_workflow_run(run)
        return run

    return store, new_run, asyncio.Event()


@pytest.mark.asyncio
async def test_audio_copy_is_independent_preserves_suffix_and_digest(checkpoint_setup):
    store, new_run, cancel = checkpoint_setup
    source, target = new_run(), new_run()
    node = WorkflowNode(id="input", type="audio_input")
    identity = await capture_source_identity(store, source, cancel)
    assert len(identity["sha256"]) == 64
    audio = store.audio(source.project_id, source.audio_id)
    value = _source_audio(store, source, audio)
    result = NodeResult()
    result.add("audio", value)
    descriptor = await create_checkpoint(store, source, node, result, "node-fingerprint", cancel)
    assert descriptor
    copied, restored = await copy_checkpoint(store, source, target, node, descriptor, cancel)
    assert copied.output_digest == descriptor.output_digest
    output = restored.outputs["audio"].value
    assert output.path.suffix == output.suffix == ".wav"
    assert output.path.is_relative_to(
        store.workflow_run_dir(target.project_id, target.audio_id, target.id)
    )
    assert output.path.stat().st_nlink == 1
    expected = output.path.read_bytes()
    shutil.rmtree(store.workflow_run_dir(source.project_id, source.audio_id, source.id))
    value.path.write_bytes(b"x" * value.size_bytes)
    assert output.path.read_bytes() == expected
    assert (await capture_source_identity(store, target, cancel))["sha256"] != identity["sha256"]
    assert (await load_checkpoint(store, target, node, copied, cancel)).outputs[
        "audio"
    ].value.path.read_bytes() == expected


@pytest.mark.asyncio
async def test_source_info_preserves_all_named_outputs(checkpoint_setup):
    store, new_run, cancel = checkpoint_setup
    run = new_run()
    node = WorkflowNode(id="info", type="source_info")
    context = NodeExecutionContext(store, run, node, {}, store.load_project(run.project_id), cancel)
    result = await NODE_DEFINITIONS[node.type].execute(context)
    descriptor = await create_checkpoint(store, run, node, result, "info-fingerprint", cancel)
    restored = await load_checkpoint(store, run, node, descriptor, cancel)
    assert {port: data.value for port, data in restored.outputs.items()} == {
        port: data.value for port, data in result.outputs.items()
    }
    assert len(restored.outputs) == 5


@pytest.mark.asyncio
async def test_transcript_and_artifact_are_copied_without_old_urls(checkpoint_setup):
    store, new_run, cancel = checkpoint_setup
    source, target = new_run(), new_run()
    node = WorkflowNode(id="asr", type="asr")
    transcript = Transcript(
        project_id=source.project_id,
        stage="raw",
        language="ja",
        segments=[Segment(id="s1", source_text="hello")],
    )
    path = store.workflow_artifact_path(
        source.project_id, source.audio_id, source.id, node.id, "transcript.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(transcript.model_dump_json(), encoding="utf-8")
    result = NodeResult()
    result.add("transcript", TranscriptData(path=path, transcript=transcript))
    descriptor = await create_checkpoint(store, source, node, result, "asr-fingerprint", cancel)
    copied, restored = await copy_checkpoint(store, source, target, node, descriptor, cancel)
    assert restored.outputs["transcript"].value.transcript == transcript
    assert restored.outputs["transcript"].value.path != path
    output = WorkflowNode(id="output", type="audio_output")
    audio = _source_audio(store, source, store.audio(source.project_id, source.audio_id))
    artifact = await write_audio_artifact(store, source, output.id, audio, cancel, "export.wav")
    original = NodeResult(artifact=artifact)
    artifact_descriptor = await create_checkpoint(store, source, output, original, "out", cancel)
    _, cloned = await copy_checkpoint(store, source, target, output, artifact_descriptor, cancel)
    assert cloned.artifact.artifact_id != artifact.artifact_id
    assert (
        target.id in cloned.artifact.download_url and source.id not in cloned.artifact.download_url
    )
    assert (
        store.workflow_artifact_path(
            target.project_id, target.audio_id, target.id, output.id, "export.wav"
        ).read_bytes()
        == audio.path.read_bytes()
    )
    assert copied.output_digest == descriptor.output_digest


@pytest.mark.asyncio
async def test_explicit_audio_subtype_codec_roundtrip_without_runner_changes(
    checkpoint_setup, monkeypatch
):
    store, new_run, cancel = checkpoint_setup

    @dataclass(frozen=True)
    class AnnotatedAudio(AudioData):
        marker: str = "semantic-extra"

    async def encode(value, context):
        return {"audio": await AUDIO_CODEC.encode(value, context), "marker": value.marker}

    async def decode(payload, context):
        base = await AUDIO_CODEC.decode(payload["audio"], context)
        return AnnotatedAudio(**vars(base), marker=payload["marker"])

    definition = replace(
        TYPE_DEFINITIONS["audio"],
        type_id="annotated_audio",
        parent_type_id="audio",
        is_valid=lambda value: isinstance(value, AnnotatedAudio),
        checkpoint_codec=CheckpointCodec("annotated_audio", "1", encode, decode),
    )
    monkeypatch.setitem(TYPE_DEFINITIONS, definition.type_id, definition)
    run, target = new_run(), new_run()
    node = WorkflowNode(id="input", type="audio_input")
    audio = _source_audio(store, run, store.audio(run.project_id, run.audio_id))
    result = NodeResult()
    result.add("audio", AnnotatedAudio(**vars(audio)), type_id=definition.type_id)
    descriptor = await create_checkpoint(store, run, node, result, "extension", cancel)
    _, restored = await copy_checkpoint(store, run, target, node, descriptor, cancel)
    assert restored.outputs["audio"].type_id == "annotated_audio"
    assert restored.outputs["audio"].value.marker == "semantic-extra"
    monkeypatch.setitem(
        TYPE_DEFINITIONS, definition.type_id, replace(definition, checkpoint_codec=None)
    )
    assert await create_checkpoint(store, run, node, result, "extension", cancel) is None
    with pytest.raises(CheckpointUnavailable, match="快照格式"):
        await load_checkpoint(store, run, node, descriptor, cancel)


@pytest.mark.asyncio
async def test_corrupt_dependency_sidecar_and_external_path_rejected(checkpoint_setup, tmp_path):
    store, new_run, cancel = checkpoint_setup
    run = new_run()
    node = WorkflowNode(id="input", type="audio_input")
    result = NodeResult()
    audio = _source_audio(store, run, store.audio(run.project_id, run.audio_id))
    result.add("audio", audio)
    descriptor = await create_checkpoint(store, run, node, result, "fingerprint", cancel)
    restored = await load_checkpoint(store, run, node, descriptor, cancel)
    restored.outputs["audio"].value.path.write_bytes(b"corrupted")
    with pytest.raises(CheckpointUnavailable):
        await load_checkpoint(store, run, node, descriptor, cancel)
    descriptor = await create_checkpoint(store, run, node, result, "fingerprint", cancel)
    sidecar = (
        store.workflow_run_dir(run.project_id, run.audio_id, run.id)
        / ".checkpoints"
        / node.id
        / "checkpoint.json"
    )
    sidecar.write_bytes(b"{}")
    with pytest.raises(CheckpointUnavailable):
        await load_checkpoint(store, run, node, descriptor, cancel)
    external = tmp_path / "outside.wav"
    external.write_bytes(b"private")
    result = NodeResult()
    result.add("audio", replace(audio, path=external))
    with pytest.raises(CheckpointUnavailable):
        await create_checkpoint(store, run, node, result, "fingerprint", cancel)
    cancel.set()
    with pytest.raises(WorkflowRunCancelled):
        await capture_source_identity(store, run, cancel)


@pytest.mark.asyncio
async def test_hash_cancellation_joins_worker():
    entered, closed = threading.Event(), threading.Event()

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.set()

        def read(self, size):
            assert size <= 1024 * 1024
            entered.set()
            threading.Event().wait(0.03)
            return b"x"

    class Source:
        def open(self, mode):
            return Reader()

    task = asyncio.create_task(_hash_file(Source(), asyncio.Event()))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_all_primitive_types_roundtrip(checkpoint_setup, monkeypatch):
    store, new_run, cancel = checkpoint_setup
    ports = {"string": "Unicode 字符串", "number": 1.25, "integer": 42, "boolean": True}
    definition = NodeDefinition(
        type="primitive_test",
        label="Primitives",
        input_ports=(),
        output_ports=tuple(ports),
        terminal=False,
        config_kind="none",
    )
    monkeypatch.setitem(NODE_DEFINITIONS, definition.type, definition)
    run = new_run()
    node = WorkflowNode(id="primitives", type=definition.type)
    result = NodeResult()
    for port, value in ports.items():
        result.add(port, value)
    descriptor = await create_checkpoint(store, run, node, result, "primitive", cancel)
    restored = await load_checkpoint(store, run, node, descriptor, cancel)
    assert {port: data.value for port, data in restored.outputs.items()} == ports
    assert all(restored.outputs[port].type_id == PORT_DEFINITIONS[port].type_id for port in ports)


@pytest.mark.asyncio
async def test_sensitive_transcript_metadata_is_never_checkpointed(checkpoint_setup):
    store, new_run, cancel = checkpoint_setup
    run = new_run()
    node = WorkflowNode(id="asr", type="asr")
    transcript = Transcript(
        project_id=run.project_id,
        stage="raw",
        language="ja",
        segments=[Segment(id="s", source_text="safe text")],
        metadata={"api_key": "do-not-store"},
    )
    result = NodeResult()
    result.add(
        "transcript",
        TranscriptData(
            path=store.project_dir(run.project_id) / "unused.json", transcript=transcript
        ),
    )
    with pytest.raises(CheckpointUnavailable, match="认证"):
        await create_checkpoint(store, run, node, result, "unsafe", cancel)
    directory = store.workflow_run_dir(run.project_id, run.audio_id, run.id)
    assert not (directory / ".checkpoints").exists()


@pytest.mark.asyncio
async def test_transcript_usage_counts_and_recognized_urls_are_preserved(checkpoint_setup):
    store, new_run, cancel = checkpoint_setup
    run = new_run()
    node = WorkflowNode(id="asr", type="asr")
    transcript = Transcript(
        project_id=run.project_id,
        stage="raw",
        language="en",
        metadata={
            "usage": {"output_tokens": 3, "word_tokens": 2},
            "words": [
                {
                    "word": "https://example.com",
                    "text": "https://example.com",
                    "source_text": "https://example.com",
                }
            ],
        },
        segments=[
            Segment(
                id="s",
                source_text="https://example.com",
                metadata={"words": ["https://example.com"], "output_tokens": 1},
            )
        ],
    )
    path = store.workflow_artifact_path(
        run.project_id, run.audio_id, run.id, node.id, "transcript.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(transcript.model_dump_json(), encoding="utf-8")
    result = NodeResult()
    result.add("transcript", TranscriptData(path=path, transcript=transcript))
    descriptor = await create_checkpoint(store, run, node, result, "safe-metadata", cancel)
    restored = await load_checkpoint(store, run, node, descriptor, cancel)
    assert restored.outputs["transcript"].value.transcript == transcript


@pytest.mark.asyncio
async def test_checkpoint_symlink_rejected(checkpoint_setup, tmp_path):
    store, new_run, cancel = checkpoint_setup
    run = new_run()
    node = WorkflowNode(id="input", type="audio_input")
    root = store.workflow_run_dir(run.project_id, run.audio_id, run.id)
    external = tmp_path / "external-checkpoints"
    external.mkdir()
    try:
        (root / ".checkpoints").symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    result = NodeResult()
    result.add("audio", _source_audio(store, run, store.audio(run.project_id, run.audio_id)))
    with pytest.raises(CheckpointUnavailable):
        await create_checkpoint(store, run, node, result, "unsafe", cancel)
    assert list(external.iterdir()) == []


@pytest.mark.asyncio
async def test_copy_cancellation_waits_for_cleanup_even_after_second_cancel(monkeypatch):
    entered, finishing, release, cleaned = (asyncio.Event() for _ in range(4))
    cancel = asyncio.Event()

    async def copy(source, target, event, *, expected_size):
        entered.set()
        await event.wait()
        finishing.set()
        await release.wait()
        cleaned.set()
        raise WorkflowRunCancelled()

    monkeypatch.setattr("app.application.workflow_checkpoints._copy_audio", copy)
    task = asyncio.create_task(_copy_file(None, None, cancel, expected_size=0))
    await entered.wait()
    task.cancel()
    await finishing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_text_output_and_record_restore_one_artifact_identity(checkpoint_setup, monkeypatch):
    store, new_run, cancel = checkpoint_setup
    for node_type in ("text_producer", "text_consumer"):
        monkeypatch.setitem(
            NODE_DEFINITIONS,
            node_type,
            NodeDefinition(
                type=node_type,
                label=node_type,
                input_ports=(),
                output_ports=("text",),
                terminal=False,
                config_kind="none",
            ),
        )
    source, target = new_run(), new_run()
    producer = WorkflowNode(id="producer", type="text_producer")
    path = store.workflow_artifact_path(
        source.project_id, source.audio_id, source.id, producer.id, "result.txt"
    )
    path.parent.mkdir(parents=True)
    payload = b"reusable text"
    path.write_bytes(payload)
    artifact = WorkflowArtifact(
        artifact_id=f"art_{uuid4().hex}",
        port="text",
        filename="result.txt",
        media_type="text/plain",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    result = NodeResult(artifact=artifact)
    result.add("text", artifact)
    descriptor = await create_checkpoint(store, source, producer, result, "producer", cancel)
    _, restored = await copy_checkpoint(store, source, target, producer, descriptor, cancel)
    assert restored.outputs["text"].value is restored.artifact
    assert restored.artifact.artifact_id != artifact.artifact_id
    target.nodes.append(
        WorkflowNodeRecord(node_id=producer.id, node_type=producer.type, artifact=restored.artifact)
    )
    consumer = WorkflowNode(id="consumer", type="text_consumer")
    forwarded = NodeResult()
    forwarded.add("text", restored.outputs["text"].value)
    saved = await create_checkpoint(store, target, consumer, forwarded, "consumer", cancel)
    assert saved is not None
    assert (await load_checkpoint(store, target, consumer, saved, cancel)).outputs[
        "text"
    ].value.sha256 == artifact.sha256
