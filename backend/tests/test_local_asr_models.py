import asyncio
import json
import os
import struct
import threading
from pathlib import Path

import pytest
import yaml
from app.application import local_asr_models as models_module
from app.application.local_asr_models import scan_models
from app.main import create_app
from fastapi.testclient import TestClient

URL = "/api/v1/settings/local-asr/models"


def string(value: str | bytes) -> bytes:
    encoded = value.encode() if isinstance(value, str) else value
    return struct.pack("<Q", len(encoded)) + encoded


def gguf(architecture="qwen3asr", extra=()):
    entries = [] if architecture is None else [("general.architecture", 8, string(architecture))]
    entries.extend(extra)
    data = b"GGUF" + struct.pack("<IQQ", 3, 1, len(entries))
    for key, kind, value in entries:
        data += string(key) + struct.pack("<I", kind) + value
    data += string("test.weight") + struct.pack("<IQIQ", 1, 4, 0, 0)
    data += b"\x00" * (-len(data) % 32)
    return data + b"W" * 16, len(data)


def fake_cli(path: Path, *, version="CrispASR v0.8.38", backend_command=None):
    if backend_command is None:
        compiled = json.dumps(
            {"backends": [{"name": name} for name in ("qwen3", "whisper", "not-supported")]}
        )
        backend_command = f"printf '%s' '{compiled}'"
    path.write_text(
        '#!/bin/sh\n[ "$#" -eq 1 ] || exit 9\ncase "$1" in\n'
        f"--version) printf '%s\\n' '{version}';;\n"
        f"--list-backends-json) {backend_command};;\n"
        "*) exit 9;;\nesac\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for variable in ("VOICETRANSL_CRISPASR_EXECUTABLE", "VOICETRANSL_CRISPASR_MODEL_DIR"):
        monkeypatch.delenv(variable, raising=False)
    root = tmp_path / "crispasr"
    root.mkdir()
    binary = root / "bin" / "crispasr"
    binary.parent.mkdir()
    fake_cli(binary)
    models = root / "models"
    models.mkdir()
    config = tmp_path / "config" / "app.yaml"
    config.parent.mkdir()
    config.write_text(
        yaml.safe_dump(
            {"app": {"crispasr_executable": str(binary), "crispasr_model_dir": str(models)}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOICETRANSL_CONFIG", str(config))
    app = create_app(tmp_path / "data")
    with TestClient(app, base_url="http://127.0.0.1:5173", client=("127.0.0.1", 1234)) as client:
        yield {
            "app": app,
            "client": client,
            "models": models,
            "binary": binary,
            "config": config,
            "root": root,
            "tmp_path": tmp_path,
        }


def assert_private(response, runtime):
    text = json.dumps(response, ensure_ascii=False)
    assert str(runtime["tmp_path"]) not in text
    assert "/home/" not in text
    assert "private-user" not in text


def test_catalog_contract_and_fixed_cli_commands_without_inference(runtime):
    data, _ = gguf()
    (runtime["models"] / "model.gguf").write_bytes(data)
    before = runtime["config"].read_bytes()
    response = runtime["client"].get(URL)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "provider",
        "executable",
        "model_dir",
        "restart_required",
        "backends",
        "items",
        "truncated",
        "warnings",
    }
    assert body["provider"] == "crispasr"
    assert body["executable"]["ok"] and body["executable"]["version"] == "0.8.38"
    assert body["model_dir"] == "./crispasr/models"
    assert not body["restart_required"] and not body["truncated"]
    assert body["backends"] == ["qwen3", "whisper"]
    assert body["items"] == [
        {
            "model": "model.gguf",
            "backend": "qwen3",
            "size_bytes": len(data),
            "architecture": "qwen3asr",
            "selectable": True,
            "reason": None,
        }
    ]
    assert_private(body, runtime)
    assert runtime["config"].read_bytes() == before
    assert not runtime["app"].state.jobs.tasks


@pytest.mark.parametrize(
    "architecture",
    ["qwen3", "whisper", "llama", None, "/home/private-user/model", "qwen3asr\n", "x" * 65],
)
def test_unknown_or_unsafe_metadata_never_guessed_from_filename(runtime, architecture):
    extra = (("general.name", 8, string("unknown")),) if architecture is None else ()
    data, _ = gguf(architecture, extra)
    (runtime["models"] / "qwen3-asr-0.6b.gguf").write_bytes(data)
    response = runtime["client"].get(URL)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert not item["selectable"] and item["backend"] is None and item["reason"]
    if architecture and models_module.SAFE_ARCHITECTURE.fullmatch(architecture):
        assert item["architecture"] == architecture
    else:
        assert item["architecture"] is None
    assert_private(response.json(), runtime)


@pytest.mark.parametrize(
    "value",
    [b"not-gguf", b"GGUF" + struct.pack("<IQQ", 99, 1, 1), b"GGUF" + struct.pack("<IQQ", 3, 0, 1)],
)
def test_damaged_files_remain_visible_disabled(runtime, value):
    (runtime["models"] / "bad.gguf").write_bytes(value)
    body = runtime["client"].get(URL).json()
    assert body["items"][0]["model"] == "bad.gguf"
    assert body["items"][0]["reason"] and not body["items"][0]["selectable"]


def test_truncated_tensor_data_and_duplicate_architecture_are_disabled(runtime):
    data, start = gguf()
    (runtime["models"] / "truncated.gguf").write_bytes(data[:start])
    (runtime["models"] / "partial-tensor.gguf").write_bytes(data[: start + 1])
    duplicate, _ = gguf(extra=(("general.architecture", 8, string("qwen3asr")),))
    (runtime["models"] / "duplicate.gguf").write_bytes(duplicate)
    items = runtime["client"].get(URL).json()["items"]
    assert len(items) == 3 and all(item["reason"] and not item["selectable"] for item in items)


@pytest.mark.parametrize("tensors,pairs", [(8193, 1), (1, 1025)])
def test_declared_tensor_and_kv_counts_have_limits(runtime, tensors, pairs):
    (runtime["models"] / "count-limit.gguf").write_bytes(
        b"GGUF" + struct.pack("<IQQ", 3, tensors, pairs)
    )
    body = runtime["client"].get(URL).json()
    assert body["truncated"]
    assert not body["items"][0]["selectable"] and body["items"][0]["reason"]


def test_shallow_scan_excludes_all_links_fifo_invalid_names_and_directories(runtime):
    data, _ = gguf()
    (runtime["models"] / "valid.gguf").write_bytes(data)
    (runtime["models"] / "inside-link.gguf").symlink_to(runtime["models"] / "valid.gguf")
    (runtime["models"] / "outside-link.gguf").symlink_to(runtime["binary"])
    (runtime["models"] / "bad name.gguf").write_bytes(data)
    (runtime["models"] / "子模型.gguf").write_bytes(data)
    os.mkfifo(runtime["models"] / "fifo.gguf")
    child = runtime["models"] / "nested.gguf"
    child.mkdir()
    (child / "nested-model.gguf").write_bytes(data)
    body = runtime["client"].get(URL).json()
    assert [item["model"] for item in body["items"]] == ["valid.gguf"]
    assert body["warnings"]


def test_scan_open_toctou_cannot_follow_replaced_entry(runtime, monkeypatch):
    data, _ = gguf()
    victim = runtime["models"] / "victim.gguf"
    victim.write_bytes(data)
    original = os.open
    intercepted = []

    def replace_before_open(path, flags, *args, **kwargs):
        if path == "victim.gguf":
            intercepted.append(True)
            victim.unlink()
            victim.symlink_to(runtime["binary"])
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(models_module.os, "open", replace_before_open)
    body = runtime["client"].get(URL).json()
    assert intercepted and body["items"] == []


@pytest.mark.parametrize("ancestor", [False, True])
def test_active_root_or_ancestor_symlink_escape_denied(runtime, ancestor):
    outside = runtime["tmp_path"] / "outside"
    outside.mkdir()
    target = outside / "models"
    target.mkdir()
    (target / "secret.gguf").write_bytes(gguf()[0])
    if ancestor:
        runtime["root"].rename(runtime["tmp_path"] / "previous-root")
        runtime["root"].symlink_to(outside)
    else:
        runtime["models"].rmdir()
        runtime["models"].symlink_to(target)
    response = runtime["client"].get(URL)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LOCAL_ASR_MODEL_DIR_UNAVAILABLE"
    assert_private(response.json(), runtime)


@pytest.mark.parametrize("mode", ["missing", "unreadable"])
def test_unavailable_active_directory_returns_stable_503(runtime, mode):
    if mode == "missing":
        runtime["models"].rmdir()
    else:
        runtime["models"].chmod(0o000)
    try:
        response = runtime["client"].get(URL)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LOCAL_ASR_MODEL_DIR_UNAVAILABLE"
        assert_private(response.json(), runtime)
    finally:
        if runtime["models"].exists():
            runtime["models"].chmod(0o755)


def test_missing_executable_keeps_catalog_disabled_and_private(runtime):
    (runtime["models"] / "model.gguf").write_bytes(gguf()[0])
    runtime["binary"].unlink()
    response = runtime["client"].get(URL)
    assert response.status_code == 200
    body = response.json()
    assert not body["executable"]["ok"] and body["executable"]["version"] is None
    assert not body["items"][0]["selectable"] and body["items"][0]["reason"]
    assert body["backends"] == []
    assert_private(body, runtime)


@pytest.mark.parametrize(
    "command",
    [
        "printf 'bad-json'",
        "printf '%s' '{}'",
        "printf '/home/private-user/raw-cli-output' >&2; exit 1",
        "head -c 300000 /dev/zero",
        "sleep 5",
    ],
)
def test_failed_backend_diagnostics_disable_items_without_raw_output(runtime, command):
    (runtime["models"] / "model.gguf").write_bytes(gguf()[0])
    fake_cli(runtime["binary"], backend_command=command)
    runtime["app"].state.local_asr_settings.check_timeout = 0.2
    body = runtime["client"].get(URL).json()
    assert not body["executable"]["ok"] and not body["items"][0]["selectable"]
    assert body["executable"]["version"] == "0.8.38"
    assert_private(body, runtime)


def test_valid_compiled_backends_intersection_does_not_enable_missing_qwen3(runtime):
    (runtime["models"] / "model.gguf").write_bytes(gguf()[0])
    fake_cli(
        runtime["binary"],
        backend_command='printf \'%s\' \'{"backends":[{"name":"whisper"},{"name":"not-supported"}]}\'',
    )
    body = runtime["client"].get(URL).json()
    assert body["executable"]["ok"] and body["backends"] == ["whisper"]
    assert not body["items"][0]["selectable"] and "未编译" in body["items"][0]["reason"]


def test_saved_directory_and_executable_never_replace_active_scan_until_restart(runtime):
    (runtime["models"] / "active.gguf").write_bytes(gguf()[0])
    saved_models = runtime["root"] / "saved-models"
    saved_models.mkdir()
    (saved_models / "saved.gguf").write_bytes(gguf()[0])
    saved_binary = runtime["root"] / "saved-bin" / "crispasr"
    saved_binary.parent.mkdir()
    fake_cli(saved_binary, version="CrispASR v0.9.0")
    old = runtime["client"].get("/api/v1/settings/local-asr").json()
    response = runtime["client"].put(
        "/api/v1/settings/local-asr",
        json={
            "crispasr_executable": "./crispasr/saved-bin/crispasr",
            "crispasr_model_dir": "./crispasr/saved-models",
            "revision": old["revision"],
        },
    )
    assert response.status_code == 200
    body = runtime["client"].get(URL).json()
    assert body["restart_required"] and body["model_dir"] == "./crispasr/models"
    assert body["executable"]["version"] == "0.8.38"
    assert [item["model"] for item in body["items"]] == ["active.gguf"]
    restarted = create_app(runtime["tmp_path"] / "restarted-data")
    with TestClient(restarted, base_url="http://localhost", client=("127.0.0.1", 42)) as client:
        body = client.get(URL).json()
        assert not body["restart_required"] and body["model_dir"] == "./crispasr/saved-models"
        assert body["executable"]["version"] == "0.9.0"
        assert [item["model"] for item in body["items"]] == ["saved.gguf"]


def test_external_environment_root_uses_private_alias(runtime, monkeypatch):
    outside = runtime["tmp_path"].parent / (runtime["tmp_path"].name + "-private-user")
    outside.mkdir()
    (outside / "model.gguf").write_bytes(gguf()[0])
    monkeypatch.setenv("VOICETRANSL_CRISPASR_MODEL_DIR", str(outside))
    app = create_app(runtime["tmp_path"] / "external-data")
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1)) as client:
        body = client.get(URL).json()
        assert body["model_dir"] == "@root/1"
        assert body["items"][0]["selectable"]
        assert_private(body, runtime)


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "foreign.example"},
        {"origin": "http://foreign.example"},
        {"x-forwarded-for": "127.0.0.1"},
        {"forwarded": "for=127.0.0.1"},
    ],
)
def test_catalog_reuses_local_only_guard(runtime, headers):
    assert runtime["client"].get(URL, headers=headers).status_code == 403


def test_foreign_peer_denied_and_all_client_arguments_rejected(runtime):
    with TestClient(
        runtime["app"], base_url="http://localhost", client=("192.168.0.1", 3)
    ) as client:
        assert client.get(URL).status_code == 403
    for query in ("?path=/home/private-user", "?model_dir=./crispasr/models", "?refresh=true"):
        response = runtime["client"].get(URL + query)
        assert response.status_code == 422
        assert_private(response.json(), runtime)
    assert (
        runtime["client"].request("GET", URL, json={"path": "/home/private-user"}).status_code
        == 422
    )


@pytest.mark.parametrize(
    "limit,value", [("MAX_MODELS", 1), ("MAX_ENTRIES", 1), ("SCAN_SECONDS", 0)]
)
def test_scan_count_and_total_time_limits_are_explicit(runtime, monkeypatch, limit, value):
    for name in ("a.gguf", "b.gguf", "c.gguf"):
        (runtime["models"] / name).write_bytes(gguf()[0])
    monkeypatch.setattr(models_module, limit, value)
    body = runtime["client"].get(URL).json()
    assert body["truncated"] and body["warnings"] and len(body["items"]) <= 1


@pytest.mark.parametrize(
    "key,kind,value",
    [
        ("large", 8, struct.pack("<Q", 65537)),
        ("array", 9, struct.pack("<IQ", 8, 1_000_001)),
        ("depth", 9, struct.pack("<IQ", 9, 1) * 6 + struct.pack("<IQ", 8, 0)),
        ("type", 99, b""),
    ],
)
def test_malformed_string_array_depth_or_type_is_bounded(runtime, key, kind, value):
    data, _ = gguf(extra=((key, kind, value),))
    (runtime["models"] / "bad.gguf").write_bytes(data)
    body = runtime["client"].get(URL).json()
    assert body["items"][0]["reason"] and not body["items"][0]["selectable"]


def test_metadata_bytes_and_per_file_time_budget(runtime, monkeypatch):
    (runtime["models"] / "model.gguf").write_bytes(gguf()[0])
    monkeypatch.setattr(models_module, "MAX_METADATA_BYTES", 25)
    body = runtime["client"].get(URL).json()
    assert body["truncated"] and not body["items"][0]["selectable"]
    monkeypatch.setattr(models_module, "MAX_METADATA_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(models_module, "FILE_SECONDS", 0)
    body = runtime["client"].get(URL).json()
    assert body["truncated"] and not body["items"][0]["selectable"]


def test_full_large_tokenizer_metadata_parsed_without_weight_reads(runtime, monkeypatch):
    tokens = struct.pack("<IQ", 8, 150000) + string("token") * 150000
    data, data_start = gguf(extra=(("tokenizer.ggml.tokens", 9, tokens),))
    model = runtime["models"] / "large-vocabulary.gguf"
    model.write_bytes(data)
    original = os.pread
    positions = []

    def metadata_only(descriptor, count, position):
        assert position + count <= data_start
        positions.append((position, count))
        return original(descriptor, count, position)

    monkeypatch.setattr(models_module.os, "pread", metadata_only)
    # No tight elapsed-time assertion: exercise the large tokenizer path with a
    # generous deterministic test budget rather than depending on CI scheduling.
    monkeypatch.setattr(models_module, "FILE_SECONDS", 10)
    monkeypatch.setattr(models_module, "SCAN_SECONDS", 10)
    scanned = scan_models(runtime["models"], threading.Event())
    assert scanned["items"][0]["architecture"] == "qwen3asr"
    assert scanned["items"][0]["reason"] is None and not scanned["truncated"]
    assert positions and max(position + count for position, count in positions) <= data_start


def test_short_read_during_file_truncation_disables_safely(runtime, monkeypatch):
    model = runtime["models"] / "truncated.gguf"
    model.write_bytes(gguf()[0])
    original = os.pread

    def short_read(descriptor, count, position):
        if position == 24:
            return b""
        return original(descriptor, count, position)

    monkeypatch.setattr(models_module.os, "pread", short_read)
    scanned = scan_models(runtime["models"], threading.Event())
    assert not scanned["items"][0]["selectable"] and scanned["items"][0]["reason"]


@pytest.mark.asyncio
async def test_cancelled_scan_finishes_thread_before_next_request_can_scan(runtime, monkeypatch):
    started = threading.Event()
    stopped = threading.Event()
    finish = threading.Event()
    calls = []
    active = 0
    max_active = 0

    def controlled_scan(_root, stop):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(True)
        try:
            if len(calls) == 1:
                started.set()
                assert stop.wait(2)
                stopped.set()
                assert finish.wait(2)
            return {"items": [], "truncated": False, "warnings": []}
        finally:
            active -= 1

    monkeypatch.setattr(models_module, "scan_models", controlled_scan)
    service = runtime["app"].state.local_asr_models
    first = asyncio.create_task(service.get())
    assert await asyncio.to_thread(started.wait, 2)
    second = asyncio.create_task(service.get())
    first.cancel()
    assert await asyncio.to_thread(stopped.wait, 2)
    await asyncio.sleep(0)
    assert len(calls) == 1 and not second.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert max_active == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_never_starts_a_scan(runtime, monkeypatch):
    service = runtime["app"].state.local_asr_models
    calls = []
    monkeypatch.setattr(models_module, "scan_models", lambda *_args: calls.append(True))
    await service.lock.acquire()
    try:
        pending = asyncio.create_task(service.get())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert calls == []
    finally:
        service.lock.release()


@pytest.mark.asyncio
async def test_cancelled_backend_probe_kills_process_and_releases_lock(runtime):
    fake_cli(runtime["binary"], backend_command="sleep 30")
    service = runtime["app"].state.local_asr_models
    factory = service.process_factory
    started = asyncio.Event()
    processes = []

    async def tracked(executable):
        process = await factory(executable)
        processes.append(process)
        started.set()
        return process

    service.process_factory = tracked
    pending = asyncio.create_task(service.get())
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert processes[0].returncode is not None and not service.lock.locked()
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)
