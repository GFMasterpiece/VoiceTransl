from copy import deepcopy
from dataclasses import replace

import pytest
from app.main import create_app
from app.storage.secret_store import SecretStore, SecretStoreError
from fastapi.testclient import TestClient


def save_body(settings, **changes):
    settings = deepcopy(settings)
    body = {
        "schema_version": "1.0",
        "expected_revision": settings["revision"],
        "request_id": "settings-test",
        "global": settings["global"],
        "node_defaults": {key: value["config"] for key, value in settings["node_defaults"].items()},
    }
    body.update(changes)
    return body


def test_settings_roundtrip_revision_restart_and_runtime(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    initial = client.get("/api/v1/settings").json()
    assert initial["revision"] == 0
    assert set(initial["node_defaults"]) == {
        "asr",
        "qwen_asr",
        "qwen_asr_streaming",
        "llm_translate",
    }
    assert [field["key"] for field in initial["node_defaults"]["asr"]["fields"]] == ["language"]
    body = save_body(initial, **{"global": {"max_concurrent": 4, "upload_concurrency": 7}})
    body["node_defaults"]["qwen_asr"]["model"] = "future-compatible-model"
    saved = client.put("/api/v1/settings", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert app.state.jobs.max_concurrent == 4
    assert client.get("/api/v1/upload-policy").json()["upload_concurrency"] == 7
    assert client.put("/api/v1/settings", json=body).json() == saved.json()
    body["global"]["max_concurrent"] = 2
    assert client.put("/api/v1/settings", json=body).status_code == 409
    restarted = TestClient(create_app(tmp_path)).get("/api/v1/settings").json()
    assert restarted == saved.json()


def test_default_secret_versions_are_private_immutable_and_owned(tmp_path):
    client = TestClient(create_app(tmp_path))
    initial = client.get("/api/v1/settings").json()
    body = save_body(
        initial,
        credential_updates=[
            {"node_type": "qwen_asr", "operation": "set", "api_key": "default-key-ONE"}
        ],
    )
    saved = client.put("/api/v1/settings", json=body)
    assert saved.status_code == 200, saved.text
    assert "default-key-ONE" not in saved.text
    token = saved.json()["node_defaults"]["qwen_asr"]["credential_version"]
    secrets = SecretStore(tmp_path)
    assert secrets.read_default("qwen_asr", "qwen_asr", token) == "default-key-ONE"
    with pytest.raises(SecretStoreError):
        secrets.read_default("qwen_asr_streaming", "qwen_asr_streaming", token)
    assert client.put("/api/v1/settings", json=body).status_code == 200
    body["credential_updates"][0]["api_key"] = "DIFFERENT"
    assert client.put("/api/v1/settings", json=body).status_code == 409
    clear = save_body(
        saved.json(),
        request_id="clear-key",
        credential_updates=[{"node_type": "qwen_asr", "operation": "clear"}],
    )
    assert not client.put("/api/v1/settings", json=clear).json()["node_defaults"]["qwen_asr"][
        "credential_configured"
    ]
    assert secrets.read_default("qwen_asr", "qwen_asr", token) == "default-key-ONE"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"default-key-ONE" not in path.read_bytes()


def test_settings_failed_write_and_invalid_values_do_not_publish(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    initial = client.get("/api/v1/settings").json()
    body = save_body(initial)
    body["global"]["upload_concurrency"] = 33
    assert client.put("/api/v1/settings", json=body).status_code == 422
    body = save_body(initial)
    body["node_defaults"]["qwen_asr"]["credential_ref"] = "illegal"
    assert client.put("/api/v1/settings", json=body).status_code == 422

    def fail(*args):
        raise OSError("write unavailable")

    monkeypatch.setattr("app.storage.settings_store.atomic_write_json", fail)
    assert client.put("/api/v1/settings", json=save_body(initial)).status_code == 503
    assert client.get("/api/v1/settings").json() == initial
    assert not (tmp_path / "settings.json").exists()


@pytest.mark.parametrize(
    "content", ['{"schema_version":"future"}', "{broken", "[]", "null", "3", '"scalar"']
)
def test_existing_invalid_settings_never_fall_back(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        create_app(tmp_path)
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("content", ["[]", "null", "3", '"scalar"'])
def test_bad_settings_structure_returns_safe_read_error(tmp_path, content):
    client = TestClient(create_app(tmp_path))
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    response = client.get("/api/v1/settings")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SETTINGS_READ_FAILED"
    assert path.read_bytes() == content.encode()


@pytest.mark.parametrize("broken", [False, True])
def test_settings_symlink_is_rejected_including_broken_links(tmp_path, broken):
    client = TestClient(create_app(tmp_path))
    initial = client.get("/api/v1/settings").json()
    target = tmp_path / "target.json"
    if not broken:
        target.write_bytes(b"original target")
    path = tmp_path / "settings.json"
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    assert client.get("/api/v1/settings").status_code == 503
    assert client.put("/api/v1/settings", json=save_body(initial)).status_code == 422
    with pytest.raises(ValueError, match="symlink"):
        create_app(tmp_path)
    assert path.is_symlink()
    if broken:
        assert not target.exists()
    else:
        assert target.read_bytes() == b"original target"


def test_default_master_missing_is_not_regenerated(tmp_path):
    secrets = SecretStore(tmp_path)
    token = secrets.create_default("qwen_asr", "qwen_asr", "key-value", request_id="one")
    (tmp_path / ".default_credentials" / "master.key").unlink()
    with pytest.raises(SecretStoreError) as error:
        secrets.read_default("qwen_asr", "qwen_asr", token)
    assert error.value.code == "CREDENTIAL_MASTER_KEY_MISSING"
    assert not (tmp_path / ".default_credentials" / "master.key").exists()


def test_existing_settings_additive_registry_extension_is_read_only(tmp_path, monkeypatch):
    from app.domain.node_registry import NODE_DEFINITIONS

    client = TestClient(create_app(tmp_path))
    saved = client.put("/api/v1/settings", json=save_body(client.get("/api/v1/settings").json()))
    assert saved.status_code == 200
    path = tmp_path / "settings.json"
    original = path.read_bytes()
    definition = NODE_DEFINITIONS["asr"]
    monkeypatch.setitem(
        NODE_DEFINITIONS,
        "asr",
        replace(definition, settings_default_fields=("language", "provider")),
    )
    monkeypatch.setitem(
        NODE_DEFINITIONS, "extension_asr", replace(definition, type="extension_asr")
    )
    loaded = client.get("/api/v1/settings")
    assert loaded.status_code == 200, loaded.text
    defaults = loaded.json()["node_defaults"]
    assert defaults["asr"]["config"]["provider"] == "mock"
    assert defaults["extension_asr"]["config"] == {"language": "ja"}
    assert path.read_bytes() == original
    assert TestClient(create_app(tmp_path)).get("/api/v1/settings").status_code == 200
    assert path.read_bytes() == original


def test_stale_replay_after_further_update_cannot_create_or_publish_keys(tmp_path):
    client = TestClient(create_app(tmp_path))
    first = save_body(client.get("/api/v1/settings").json())
    second = client.put("/api/v1/settings", json=first).json()
    latest = client.put("/api/v1/settings", json=save_body(second, request_id="second")).json()
    original = (tmp_path / "settings.json").read_bytes()
    first["credential_updates"] = [
        {"node_type": "qwen_asr", "operation": "set", "api_key": "stale-key"}
    ]
    assert client.put("/api/v1/settings", json=first).status_code == 409
    assert client.get("/api/v1/settings").json() == latest
    assert (tmp_path / "settings.json").read_bytes() == original
    assert not (tmp_path / ".default_credentials").exists()
