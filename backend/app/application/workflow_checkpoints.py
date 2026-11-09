"""Durable typed outputs with run-owned, verified files and explicit codecs."""

import asyncio
import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from uuid import uuid4

from ..domain.checkpoint import CheckpointDescriptor, CheckpointUnavailable
from ..domain.node_execution import NodeData, NodeResult
from ..domain.node_registry import (
    NODE_DEFINITIONS,
    PORT_DEFINITIONS,
    TYPE_DEFINITIONS,
    validate_node_result,
)
from .workflow_asr import _write_bytes
from .workflow_media import WorkflowRunCancelled, _copy_audio

MAX_SIDECAR_BYTES = 16 * 1024 * 1024
_FILE = re.compile(r"file_[0-9]+(?:\.[A-Za-z0-9]{1,16})?")


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _safe_path(path, root):
    path, root = Path(path), Path(root)
    if not path.is_absolute():
        raise CheckpointUnavailable()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise CheckpointUnavailable() from None
    cursor = root
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise CheckpointUnavailable()
        cursor = cursor / part
        if cursor.is_symlink():
            raise CheckpointUnavailable()
    if root.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise CheckpointUnavailable()
    return path


def _directory(store, run, node):
    root = store.workflow_run_dir(run.project_id, run.audio_id, run.id)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", node.id):
        raise CheckpointUnavailable()
    return _safe_path(root / ".checkpoints" / node.id, root)


async def _hash_file(path, cancel_event):
    stop = threading.Event()

    def work():
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno()) if hasattr(stream, "fileno") else None
            if before is not None and not stat.S_ISREG(before.st_mode):
                raise CheckpointUnavailable()
            while chunk := stream.read(1024 * 1024):
                if stop.is_set():
                    raise WorkflowRunCancelled()
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno()) if before is not None else None
            if before is not None and (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise CheckpointUnavailable()
        return size, digest.hexdigest()

    if cancel_event.is_set():
        raise WorkflowRunCancelled()
    task = asyncio.create_task(asyncio.to_thread(work))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.05)
            if cancel_event.is_set():
                raise WorkflowRunCancelled()
        return await task
    except BaseException:
        stop.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


async def capture_source_identity(store, run, cancel_event):
    audio = store.audio(run.project_id, run.audio_id)
    source = store.scope(run.project_id, run.audio_id).source_path(
        run.project_id, audio.source.path
    )
    _safe_path(source, store.project_dir(run.project_id))
    size, digest = await _hash_file(source, cancel_event)
    if size != audio.source.size_bytes:
        raise CheckpointUnavailable("SOURCE_CHANGED", "源音频已变化，请重新上传")
    return {
        "audio_id": run.audio_id,
        "source": audio.source.model_dump(mode="json"),
        "size_bytes": size,
        "sha256": digest,
    }


async def _copy_file(source, target, cancel_event, *, expected_size):
    task = asyncio.create_task(
        _copy_audio(source, target, cancel_event, expected_size=expected_size)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel_event.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise


class _Context:
    def __init__(self, store, run, node, cancel_event, files=None):
        self.store, self.run, self.node = store, run, node
        self.cancel_event = cancel_event
        self.directory = _directory(store, run, node)
        self.files = files if files is not None else {}
        self.artifacts = {}
        self.source_keys = {}
        self.restored_artifacts = {}

    def file(self, key):
        if not isinstance(key, str) or not _FILE.fullmatch(key) or key not in self.files:
            raise CheckpointUnavailable()
        return _safe_path(self.directory / key, self.directory.parent.parent)

    async def add_file(self, source):
        source = Path(source)
        if source in self.source_keys:
            return self.source_keys[source]
        root = self.store.workflow_run_dir(self.run.project_id, self.run.audio_id, self.run.id)
        if source.is_relative_to(root):
            _safe_path(source, root)
        else:
            audio = self.store.audio(self.run.project_id, self.run.audio_id)
            original = self.store.scope(self.run.project_id, self.run.audio_id).source_path(
                self.run.project_id, audio.source.path
            )
            if source != original:
                raise CheckpointUnavailable()
            _safe_path(source, self.store.project_dir(self.run.project_id))
        suffix = source.suffix
        if suffix and not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", suffix):
            raise CheckpointUnavailable()
        key = f"file_{len(self.files)}{suffix}"
        target = _safe_path(self.directory / key, root)
        if not stat.S_ISREG(source.stat().st_mode):
            raise CheckpointUnavailable()
        size, digest = await _copy_file(
            source, target, self.cancel_event, expected_size=source.stat().st_size
        )
        self.files[key] = {"size_bytes": size, "sha256": digest}
        self.source_keys[source] = key
        if not source.is_relative_to(root):
            identity = getattr(self.run, "source_identity", None)
            if identity and (identity["sha256"] != digest or identity["size_bytes"] != size):
                raise CheckpointUnavailable("SOURCE_CHANGED", "源音频已变化，请重新上传")
        return key

    def artifact_payload(self, artifact):
        return artifact.model_dump(mode="json", exclude={"artifact_id", "download_url"})

    async def add_artifact(self, artifact):
        owner = next(
            (
                record.node_id
                for record in self.run.nodes
                if record.artifact and record.artifact.artifact_id == artifact.artifact_id
            ),
            self.node.id,
        )
        source = self.store.workflow_artifact_path(
            self.run.project_id, self.run.audio_id, self.run.id, owner, artifact.filename
        )
        key = await self.add_file(source)
        if self.files[key] != {"size_bytes": artifact.size_bytes, "sha256": artifact.sha256}:
            raise CheckpointUnavailable()
        value = self.artifact_payload(artifact)
        if artifact.filename in self.artifacts and self.artifacts[artifact.filename] != {
            "file": key,
            "value": value,
        }:
            raise CheckpointUnavailable()
        self.artifacts[artifact.filename] = {"file": key, "value": value}
        return key

    def restore_artifact(self, payload):
        from ..domain.workflow import WorkflowArtifact

        key = _json(payload)
        if key in self.restored_artifacts:
            return self.restored_artifacts[key]
        artifact = WorkflowArtifact(
            **payload,
            artifact_id=f"art_{uuid4().hex}",
            download_url=f"/api/v1/projects/{self.run.project_id}/audios/{self.run.audio_id}/workflow-runs/{self.run.id}/outputs/{self.node.id}/download",
        )
        self.restored_artifacts[key] = artifact
        return artifact


def _codec(type_id):
    definition = TYPE_DEFINITIONS.get(type_id)
    if definition is None or definition.checkpoint_codec is None:
        raise CheckpointUnavailable(
            "CHECKPOINT_CODEC_UNAVAILABLE", "节点输出类型没有可用的结果快照格式，请重新运行上游节点"
        )
    return definition.checkpoint_codec


async def create_checkpoint(store, run, node, result, fingerprint, cancel_event):
    definition = NODE_DEFINITIONS[node.type]
    validate_node_result(definition, result)
    types = {
        port: data.type_id or PORT_DEFINITIONS[port].type_id
        for port, data in result.outputs.items()
    }
    try:
        codecs = {port: _codec(type_id) for port, type_id in types.items()}
    except CheckpointUnavailable:
        return None
    context = _Context(store, run, node, cancel_event)
    document = {
        "version": "1",
        "outputs": {},
        "files": context.files,
        "artifacts": context.artifacts,
        "artifact": None,
        "preview": None,
    }
    for port in sorted(result.outputs):
        codec = codecs[port]
        document["outputs"][port] = {
            "type_id": types[port],
            "codec_id": codec.id,
            "codec_version": codec.version,
            "payload": await codec.encode(result.outputs[port].value, context),
        }
    if result.artifact:
        document["artifact"] = {
            "file": await context.add_artifact(result.artifact),
            "value": context.artifact_payload(result.artifact),
        }
    if result.preview:
        document["preview"] = result.preview.model_dump(mode="json", exclude={"content_url"})
    payload = _json(document)
    if len(payload) > MAX_SIDECAR_BYTES:
        raise CheckpointUnavailable("CHECKPOINT_TOO_LARGE", "节点结果快照超出支持大小")
    target = _safe_path(context.directory / "checkpoint.json", context.directory.parent.parent)
    size, digest = await _write_bytes(target, payload, cancel_event)
    return CheckpointDescriptor(
        fingerprint=fingerprint, sha256=digest, size_bytes=size, output_digest=digest
    )


async def _read_document(store, run, node, descriptor, cancel_event):
    descriptor = CheckpointDescriptor.model_validate(descriptor)
    if descriptor.version != "1" or descriptor.size_bytes > MAX_SIDECAR_BYTES:
        raise CheckpointUnavailable()
    context = _Context(store, run, node, cancel_event)
    path = _safe_path(context.directory / "checkpoint.json", context.directory.parent.parent)
    with path.open("rb") as stream:
        raw = stream.read(MAX_SIDECAR_BYTES + 1)
    if len(raw) != descriptor.size_bytes or hashlib.sha256(raw).hexdigest() != descriptor.sha256:
        raise CheckpointUnavailable()
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or document.get("version") != "1"
        or hashlib.sha256(_json(document)).hexdigest() != descriptor.output_digest
    ):
        raise CheckpointUnavailable()
    context.files = document["files"]
    if not isinstance(context.files, dict):
        raise CheckpointUnavailable()
    for key, metadata in context.files.items():
        size, digest = await _hash_file(context.file(key), cancel_event)
        if metadata != {"size_bytes": size, "sha256": digest}:
            raise CheckpointUnavailable()
    return context, document


async def _decode(context, document):
    from ..domain.workflow import WorkflowPreview

    result = NodeResult()
    for port, saved in document["outputs"].items():
        codec = _codec(saved["type_id"])
        if saved["codec_id"] != codec.id or saved["codec_version"] != codec.version:
            raise CheckpointUnavailable(
                "CHECKPOINT_CODEC_UNAVAILABLE", "结果快照格式已变化，请重新运行上游节点"
            )
        result.outputs[port] = NodeData(
            port, await codec.decode(saved["payload"], context), saved["type_id"]
        )
    if document["artifact"]:
        result.artifact = context.restore_artifact(document["artifact"]["value"])
    if document["preview"]:
        if result.artifact is None:
            raise CheckpointUnavailable()
        result.preview = WorkflowPreview(
            **document["preview"],
            content_url=result.artifact.download_url.removesuffix("/download") + "/content",
        )
    validate_node_result(NODE_DEFINITIONS[context.node.type], result)
    return result


async def load_checkpoint(store, run, node, descriptor, cancel_event):
    try:
        context, document = await _read_document(store, run, node, descriptor, cancel_event)
        return await _decode(context, document)
    except (OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, CheckpointUnavailable):
            raise
        raise CheckpointUnavailable() from None


async def copy_checkpoint(store, source_run, target_run, node, descriptor, cancel_event):
    if (source_run.project_id, source_run.audio_id) != (target_run.project_id, target_run.audio_id):
        raise CheckpointUnavailable()
    source, document = await _read_document(store, source_run, node, descriptor, cancel_event)
    await _decode(source, document)
    target = _Context(store, target_run, node, cancel_event)
    for key, metadata in source.files.items():
        destination = _safe_path(target.directory / key, target.directory.parent.parent)
        size, digest = await _copy_file(
            source.file(key), destination, cancel_event, expected_size=metadata["size_bytes"]
        )
        if digest != metadata["sha256"]:
            raise CheckpointUnavailable()
        target.files[key] = {"size_bytes": size, "sha256": digest}
    for artifact in document["artifacts"].values():
        target_path = store.workflow_artifact_path(
            target_run.project_id,
            target_run.audio_id,
            target_run.id,
            node.id,
            artifact["value"]["filename"],
        )
        metadata = target.files[artifact["file"]]
        _, digest = await _copy_file(
            target.file(artifact["file"]),
            target_path,
            cancel_event,
            expected_size=metadata["size_bytes"],
        )
        if digest != metadata["sha256"]:
            raise CheckpointUnavailable()
    payload = _json(document)
    path = _safe_path(target.directory / "checkpoint.json", target.directory.parent.parent)
    await _write_bytes(path, payload, cancel_event)
    result = await _decode(target, document)
    return CheckpointDescriptor.model_validate(descriptor), result
