import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml
from app.application.local_asr_settings import LocalASRPaths, parse_version
from app.config import load_settings
from app.main import create_app
from fastapi.testclient import TestClient

ENDPOINT = "/api/v1/settings/local-asr"
OFFICIAL_BUILD_INFO = """=== build info ===
  version       : 0.8.38
  git sha       : 5592ede8
  git date      : 2026-09-28T10:55:30Z
  git subject   : release-_bump_VERSION_to_0.8.38
  build date    : 2026-09-28T12:07:01Z
  build type    : Release
  compiler      : gcc 11.4.0
  os            : linux
  arch          : x86_64
  ggml backends : cpu,cuda
  cuda toolkit  : 13.0.88 (build/link target)
  cuda runtime ABI: 13 (matching major-version runtime libraries required)
  cuda archs    : 75-real,86-real,89-real,120a-real,120-virtual
"""


@pytest.mark.parametrize(
    "output",
    [
        "version: 0.8.38",
        "OtherProgram version 0.8.38",
        "=== build info ===\nversion: 0.8.38\n",
        OFFICIAL_BUILD_INFO.replace("ggml backends", "other backends"),
        OFFICIAL_BUILD_INFO.replace("5592ede8", "unknown"),
        OFFICIAL_BUILD_INFO.replace("version       : 0.8.38", "version       : arbitrary"),
    ],
)
def test_generic_version_or_incomplete_build_reports_rejected(output):
    assert parse_version(output) is None


def test_official_build_info_version_check(setup):
    setup["alternative"].write_text(
        "#!/bin/sh\ncat <<'BUILD_INFO'\n" + OFFICIAL_BUILD_INFO + "BUILD_INFO\n",
        encoding="utf-8",
    )
    values = payload(setup)
    values.pop("revision")
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 200
    assert response.json()["executable"]["ok"] is True
    assert response.json()["executable"]["version"] == "0.8.38"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for variable in ("VOICETRANSL_CRISPASR_EXECUTABLE", "VOICETRANSL_CRISPASR_MODEL_DIR"):
        monkeypatch.delenv(variable, raising=False)
    config = tmp_path / "config" / "app.yaml"
    config.parent.mkdir()
    root = tmp_path / "crispasr"
    root.mkdir()
    executable = root / "old" / "crispasr"
    executable.parent.mkdir()
    executable.write_text(
        '#!/bin/sh\n[ "$#" -eq 1 ] && [ "$1" = "--version" ] || exit 2\n'
        "printf 'CrispASR v0.8.38\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    alternative = root / "new" / "crispasr"
    alternative.parent.mkdir()
    alternative.write_bytes(executable.read_bytes())
    alternative.chmod(0o755)
    models = root / "models"
    models.mkdir()
    other_models = root / "other-models"
    other_models.mkdir()
    raw = {
        "app": {
            "crispasr_executable": "./crispasr/old/crispasr",
            "crispasr_model_dir": "./crispasr/models",
            "unknown": {"keep": ["exact", 42]},
        },
        "jobs": {"max_concurrent": 1},
        "unknown": {"secret": "not-exposed"},
    }
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setenv("VOICETRANSL_CONFIG", str(config))
    app = create_app(tmp_path / "data")
    with TestClient(app, base_url="http://127.0.0.1:5173", client=("127.0.0.1", 1234)) as client:
        yield {
            "app": app,
            "client": client,
            "config": config,
            "root": root,
            "executable": executable,
            "alternative": alternative,
            "models": models,
            "other_models": other_models,
            "raw": raw,
            "tmp_path": tmp_path,
        }


def payload(setup, *, revision=None):
    return {
        "crispasr_executable": str(setup["alternative"]),
        "crispasr_model_dir": str(setup["other_models"]),
        "revision": revision or setup["client"].get(ENDPOINT).json()["revision"],
    }


def test_get_check_save_preserves_config_and_runtime_restart(setup):
    client = setup["client"]
    before = client.get(ENDPOINT).json()
    assert before["editable"] and not before["restart_required"]
    assert "not-exposed" not in str(before)
    assert before["active"]["crispasr_executable"] == "./crispasr/old/crispasr"
    values = payload(setup)
    check = client.post(ENDPOINT + "/check", json={key: values[key] for key in before["active"]})
    assert check.status_code == 200
    assert check.json()["executable"] == {
        "ok": True,
        "message": "CrispASR 版本检查通过",
        "version": "0.8.38",
    }
    response = client.put(ENDPOINT, json=values)
    assert response.status_code == 200
    saved = response.json()
    assert saved["restart_required"] and saved["active"] == before["active"]
    assert saved["crispasr_executable"] == "./crispasr/new/crispasr"
    assert setup["app"].state.settings.crispasr_executable == setup["executable"]
    assert setup["app"].state.jobs.crispasr_executable == setup["executable"]
    assert setup["app"].state.jobs.crispasr_model_dir == setup["models"]
    raw = yaml.safe_load(setup["config"].read_bytes())
    expected = setup["raw"]
    expected["app"].update({key: values[key] for key in before["active"]})
    assert raw == expected
    restarted = create_app(setup["tmp_path"] / "restart-data")
    assert restarted.state.local_asr_settings.get()["restart_required"] is False
    assert restarted.state.jobs.crispasr_executable == setup["alternative"]


def test_noop_preserves_bytes_and_revision(setup):
    current = setup["client"].get(ENDPOINT).json()
    before = setup["config"].read_bytes()
    modified = setup["config"].stat().st_mtime_ns
    response = setup["client"].put(
        ENDPOINT, json={**current["active"], "revision": current["revision"]}
    )
    assert response.status_code == 200
    assert response.json()["revision"] == current["revision"]
    assert setup["config"].read_bytes() == before
    assert setup["config"].stat().st_mtime_ns == modified


def test_environment_managed_preserves_yaml_and_rejects_change(setup, monkeypatch):
    monkeypatch.setenv("VOICETRANSL_CRISPASR_EXECUTABLE", str(setup["alternative"]))
    monkeypatch.setenv("VOICETRANSL_CRISPASR_MODEL_DIR", str(setup["other_models"]))
    app = create_app(setup["tmp_path"] / "env-data")
    before = setup["config"].read_bytes()
    with TestClient(app, base_url="http://localhost", client=("::1", 1234)) as client:
        current = client.get(ENDPOINT).json()
        assert all(current["managed_by_environment"].values())
        assert current["crispasr_executable"] == "./crispasr/new/crispasr"
        assert not current["restart_required"]
        values = {**current["active"], "revision": current["revision"]}
        assert client.put(ENDPOINT, json=values).status_code == 200
        assert setup["config"].read_bytes() == before
        values["crispasr_executable"] = str(setup["executable"])
        response = client.put(ENDPOINT, json=values)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SETTINGS_ENVIRONMENT_MANAGED"
        assert setup["config"].read_bytes() == before


def test_environment_field_not_written_when_other_field_changes(setup, monkeypatch):
    monkeypatch.setenv("VOICETRANSL_CRISPASR_EXECUTABLE", str(setup["alternative"]))
    app = create_app(setup["tmp_path"] / "env-data")
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        current = client.get(ENDPOINT).json()
        response = client.put(
            ENDPOINT,
            json={
                **current["active"],
                "crispasr_model_dir": str(setup["other_models"]),
                "revision": current["revision"],
            },
        )
        assert response.status_code == 200
        raw = yaml.safe_load(setup["config"].read_bytes())
        assert raw["app"]["crispasr_executable"] == "./crispasr/old/crispasr"


@pytest.mark.parametrize("revision", ["stale", "0" * 64])
def test_revision_conflict(setup, revision):
    before = setup["config"].read_bytes()
    response = setup["client"].put(ENDPOINT, json=payload(setup, revision=revision))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETTINGS_REVISION_CONFLICT"
    assert setup["config"].read_bytes() == before


def test_external_edit_conflicts_and_new_revision_reads_saved_values(setup):
    values = payload(setup)
    raw = yaml.safe_load(setup["config"].read_bytes())
    raw["app"]["crispasr_model_dir"] = str(setup["other_models"])
    setup["config"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    current = setup["client"].get(ENDPOINT).json()
    assert current["revision"] != values["revision"]
    assert current["restart_required"]
    assert setup["client"].put(ENDPOINT, json=values).status_code == 409


def test_edit_during_version_check_is_rechecked_before_write(setup):
    service = setup["app"].state.local_asr_settings
    original_factory = service.process_factory
    values = payload(setup)
    changed = setup["config"].read_bytes() + b"# external edit during check\n"

    async def edit_during_check(executable):
        setup["config"].write_bytes(changed)
        return await original_factory(executable)

    service.process_factory = edit_during_check
    response = setup["client"].put(ENDPOINT, json=values)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SETTINGS_REVISION_CONFLICT"
    assert setup["config"].read_bytes() == changed


@pytest.mark.parametrize(
    "field,value",
    [
        ("crispasr_executable", "/bin/crispasr"),
        ("crispasr_executable", "./crispasr/../crispasr/old/crispasr"),
        ("crispasr_executable", "./crispasr/old/sh"),
        ("crispasr_executable", "./crispasr/old/crispasr\x00"),
        ("crispasr_model_dir", "./crispasr/models\n"),
        ("crispasr_model_dir", "./crispasr/models\x85"),
        ("crispasr_model_dir", "./crispasr/models\u202e"),
        ("crispasr_model_dir", "./crispasr\\models"),
        ("crispasr_model_dir", "/etc"),
    ],
)
def test_unsafe_paths_denied_before_execution(setup, field, value):
    values = {**setup["client"].get(ENDPOINT).json()["active"], field: value}
    before = setup["config"].read_bytes()
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PATH_NOT_ALLOWED"
    response = setup["client"].put(
        ENDPOINT, json={**values, "revision": payload(setup)["revision"]}
    )
    assert response.status_code == 422
    assert setup["config"].read_bytes() == before


@pytest.mark.parametrize("executable", [True, False])
def test_symlink_escape_denied(setup, executable):
    outside = setup["tmp_path"] / "outside"
    outside.mkdir()
    target = outside / "crispasr" if executable else outside
    if executable:
        target.write_bytes(setup["executable"].read_bytes())
        target.chmod(0o755)
    link = setup["root"] / ("link/crispasr" if executable else "link")
    if executable:
        link.parent.mkdir()
    link.symlink_to(target)
    values = setup["client"].get(ENDPOINT).json()["active"]
    values["crispasr_executable" if executable else "crispasr_model_dir"] = str(link)
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 422


def test_missing_paths_are_check_results_and_save_fails_without_mutation(setup):
    values = {
        "crispasr_executable": str(setup["root"] / "missing" / "crispasr"),
        "crispasr_model_dir": str(setup["root"] / "missing-models"),
    }
    before = setup["config"].read_bytes()
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 200
    assert not response.json()["executable"]["ok"]
    assert not response.json()["model_dir"]["ok"]
    response = setup["client"].put(
        ENDPOINT, json={**values, "revision": payload(setup)["revision"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SETTINGS_VALIDATION_FAILED"
    assert setup["config"].read_bytes() == before


@pytest.mark.parametrize("body", [{"unknown": "x"}, {"crispasr_executable": 123}])
def test_unknown_fields_and_non_strings_rejected(setup, body):
    values = payload(setup)
    values.update(body)
    assert setup["client"].put(ENDPOINT, json=values).status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "evil.example"},
        {"host": "192.168.0.1:8000"},
        {"host": "127.0.0.1.evil.example"},
        {"origin": "http://evil.example"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1:8000"},
        {"origin": "https://127.0.0.1:5173"},
        {"host": "evil.example", "x-forwarded-host": "localhost"},
        {"x-forwarded-for": "127.0.0.1"},
        {"x-forwarded-proto": "http"},
        {"forwarded": "for=127.0.0.1;host=localhost"},
    ],
)
def test_foreign_host_or_origin_denied_on_all_settings_endpoints(setup, headers):
    for method, suffix in (("GET", ""), ("PUT", ""), ("POST", "/check")):
        response = setup["client"].request(
            method, ENDPOINT + suffix, json=payload(setup), headers=headers
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SETTINGS_LOCAL_ONLY"


def test_foreign_peer_denied_and_existing_routes_unaffected(setup):
    with TestClient(
        setup["app"], base_url="http://127.0.0.1", client=("192.168.0.2", 123)
    ) as client:
        assert client.get(ENDPOINT, headers={"x-forwarded-for": "127.0.0.1"}).status_code == 403
        assert client.get("/api/v1/health").status_code == 200


def test_same_origin_vite_host_allowed(setup):
    headers = {"origin": "http://127.0.0.1:5173"}
    assert setup["client"].get(ENDPOINT, headers=headers).status_code == 200
    assert setup["client"].put(ENDPOINT, json=payload(setup), headers=headers).status_code == 200


@pytest.mark.parametrize("target", ["config", "parent"])
def test_unwritable_config_and_directory_are_explicit(setup, target):
    values = payload(setup)
    before = setup["config"].read_bytes()
    path = setup["config"] if target == "config" else setup["config"].parent
    original = path.stat().st_mode
    path.chmod(0o444 if target == "config" else 0o555)
    try:
        assert setup["client"].get(ENDPOINT).json()["editable"] is False
        response = setup["client"].put(ENDPOINT, json=values)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "SETTINGS_NOT_EDITABLE"
        assert setup["config"].read_bytes() == before
    finally:
        path.chmod(original)


def test_missing_config_returns_recoverable_error(setup):
    original = setup["config"].read_bytes()
    setup["config"].unlink()
    assert setup["client"].get(ENDPOINT).status_code == 503
    assert setup["client"].put(ENDPOINT, json=payload(setup, revision="old")).status_code == 503
    setup["config"].write_bytes(original)
    assert setup["client"].get(ENDPOINT).status_code == 200


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_atomic_write_failure_preserves_original_and_removes_temp(setup, monkeypatch, operation):
    before = setup["config"].read_bytes()

    def fail_replace(*_args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(f"app.application.local_asr_settings.os.{operation}", fail_replace)
    response = setup["client"].put(ENDPOINT, json=payload(setup))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SETTINGS_WRITE_FAILED"
    assert setup["config"].read_bytes() == before
    assert not list(setup["config"].parent.glob(".app-settings-*"))


@pytest.mark.parametrize("target", ["other_models", "alternative"])
def test_path_permission_failure_is_a_check_result(setup, monkeypatch, target):
    original_stat = Path.stat

    def denied_stat(path, *args, **kwargs):
        if path == setup[target]:
            raise PermissionError("denied")
        return original_stat(path, *args, **kwargs)

    values = payload(setup)
    values.pop("revision")
    monkeypatch.setattr(Path, "stat", denied_stat)
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 200
    assert not response.json()["model_dir" if target == "other_models" else "executable"]["ok"]


def test_relative_environment_paths_match_runtime_resolution(setup, monkeypatch):
    monkeypatch.setenv("VOICETRANSL_CRISPASR_EXECUTABLE", "./crispasr/new/crispasr")
    monkeypatch.setenv("VOICETRANSL_CRISPASR_MODEL_DIR", "./crispasr/other-models")
    app = create_app(setup["tmp_path"] / "relative-env-data")
    assert app.state.settings.crispasr_executable == setup["alternative"]
    assert app.state.jobs.crispasr_model_dir == setup["other_models"]
    assert app.state.local_asr_settings.get()["active"] == {
        "crispasr_executable": "./crispasr/new/crispasr",
        "crispasr_model_dir": "./crispasr/other-models",
    }


def test_explicit_relative_allow_roots_and_legacy_server_roots(setup):
    raw = setup["raw"]
    raw["app"]["crispasr_allowed_roots"] = ["./trusted"]
    setup["config"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    settings = load_settings(setup["config"])
    assert settings.crispasr_allowed_roots == [setup["tmp_path"] / "trusted"]
    app = create_app(setup["tmp_path"] / "roots-data")
    view = app.state.local_asr_settings.get()
    assert view["allowed_roots"] == [
        "./trusted",
        "./crispasr/old",
        "./crispasr/models",
    ]


def assert_private_response(body, setup):
    serialized = json.dumps(body, ensure_ascii=False)
    assert str(setup["tmp_path"]) not in serialized
    assert "personal-user" not in serialized
    assert "hidden-project" not in serialized
    assert "/home/" not in serialized


def test_relative_paths_roundtrip_check_save_and_private_json(setup):
    client = setup["client"]
    before = client.get(ENDPOINT).json()
    assert_private_response(before, setup)
    values = {
        "crispasr_executable": "./crispasr/new/crispasr",
        "crispasr_model_dir": "./crispasr/other-models",
    }
    checked = client.post(ENDPOINT + "/check", json=values)
    assert checked.json()["executable"]["ok"]
    assert_private_response(checked.json(), setup)
    saved = client.put(ENDPOINT, json={**values, "revision": before["revision"]}).json()
    assert saved["restart_required"]
    assert_private_response(saved, setup)
    assert {key: saved[key] for key in values} == values
    original = setup["config"].read_bytes()
    again = client.put(ENDPOINT, json={**values, "revision": saved["revision"]})
    assert again.status_code == 200
    assert setup["config"].read_bytes() == original
    stale = client.put(ENDPOINT, json={**values, "revision": before["revision"]})
    assert stale.status_code == 409
    assert_private_response(stale.json(), setup)


def external_app(setup, monkeypatch, *, environment=False):
    outside = setup["tmp_path"].parent / (setup["tmp_path"].name + "-external")
    outside = outside / "home" / "personal-user" / "hidden-project"
    executable = outside / "tools" / "crispasr"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(setup["executable"].read_bytes())
    executable.chmod(0o755)
    models = outside / "models"
    models.mkdir()
    new_executable = outside / "new" / "crispasr"
    new_executable.parent.mkdir()
    new_executable.write_bytes(executable.read_bytes())
    new_executable.chmod(0o755)
    new_models = outside / "new-models"
    new_models.mkdir()
    raw = setup["raw"]
    raw["app"]["crispasr_allowed_roots"] = ["./crispasr", str(outside)]
    if environment:
        monkeypatch.setenv("VOICETRANSL_CRISPASR_EXECUTABLE", str(executable))
        monkeypatch.setenv("VOICETRANSL_CRISPASR_MODEL_DIR", str(models))
    else:
        raw["app"]["crispasr_executable"] = str(executable)
        raw["app"]["crispasr_model_dir"] = str(models)
    setup["config"].write_text(yaml.safe_dump(raw), encoding="utf-8")
    return create_app(setup["tmp_path"] / "external-data"), new_executable, new_models


@pytest.mark.parametrize("environment", [False, True])
def test_external_aliases_private_check_noop_environment_and_real_path_comparison(
    setup, monkeypatch, environment
):
    app, _, _ = external_app(setup, monkeypatch, environment=environment)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        current = client.get(ENDPOINT).json()
        assert current["active"] == {
            "crispasr_executable": "@root/2/crispasr",
            "crispasr_model_dir": "@root/3",
        }
        assert current["allowed_roots"] == ["./crispasr", "@root/1", "@root/2", "@root/3"]
        assert_private_response(current, setup)
        assert not current["restart_required"]
        checked = client.post(ENDPOINT + "/check", json=current["active"])
        assert checked.status_code == 200
        assert all(result["ok"] for result in checked.json().values())
        before = setup["config"].read_bytes()
        for paths in (
            current["active"],
            {
                "crispasr_executable": "@root/1/tools/crispasr",
                "crispasr_model_dir": "@root/1/models",
            },
            app.state.local_asr_settings.active,
        ):
            saved = client.put(ENDPOINT, json={**paths, "revision": current["revision"]})
            assert saved.status_code == 200
            assert not saved.json()["restart_required"]
            assert_private_response(saved.json(), setup)
            assert setup["config"].read_bytes() == before
        if environment:
            rejected = client.put(
                ENDPOINT,
                json={
                    **current["active"],
                    "crispasr_model_dir": "@root/1/new-models",
                    "revision": current["revision"],
                },
            )
            assert rejected.status_code == 422
            assert rejected.json()["error"]["code"] == "SETTINGS_ENVIRONMENT_MANAGED"
            assert setup["config"].read_bytes() == before


def test_external_alias_save_restart_and_revision_conflict(setup, monkeypatch):
    app, new_executable, new_models = external_app(setup, monkeypatch)
    active = app.state.local_asr_settings.active.copy()
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        old = client.get(ENDPOINT).json()
        paths = {
            "crispasr_executable": "@root/1/new/crispasr",
            "crispasr_model_dir": "@root/1/new-models",
        }
        checked = client.post(ENDPOINT + "/check", json=paths)
        assert all(result["ok"] for result in checked.json().values())
        response = client.put(ENDPOINT, json={**paths, "revision": old["revision"]})
        assert response.status_code == 200
        saved = response.json()
        assert saved["restart_required"] and saved["active"] == old["active"]
        assert_private_response(saved, setup)
        assert app.state.local_asr_settings.active == active
        assert str(app.state.jobs.crispasr_executable) == active["crispasr_executable"]
        assert str(app.state.jobs.crispasr_model_dir) == active["crispasr_model_dir"]
        raw = yaml.safe_load(setup["config"].read_bytes())
        assert raw["app"]["crispasr_executable"] == str(new_executable)
        assert raw["app"]["crispasr_model_dir"] == str(new_models)
        assert client.put(ENDPOINT, json={**paths, "revision": old["revision"]}).status_code == 409
        before = setup["config"].read_bytes()
        assert (
            client.put(ENDPOINT, json={**paths, "revision": saved["revision"]}).status_code == 200
        )
        assert setup["config"].read_bytes() == before
    restarted = create_app(setup["tmp_path"] / "external-restart-data")
    view = restarted.state.local_asr_settings.get()
    assert not view["restart_required"]
    assert_private_response(view, setup)
    assert restarted.state.jobs.crispasr_executable == new_executable


@pytest.mark.parametrize(
    "label",
    [
        "@root/0",
        "@root/01",
        "@root/99",
        "@root/-1",
        "@root/1/../escape",
        "@root/1//etc",
        "@unavailable",
    ],
)
def test_invalid_or_escaping_aliases_do_not_grant_access(setup, monkeypatch, label):
    app, _, _ = external_app(setup, monkeypatch)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        old = client.get(ENDPOINT).json()
        paths = {**old["active"], "crispasr_model_dir": label}
        before = setup["config"].read_bytes()
        for endpoint, values in (
            (ENDPOINT + "/check", paths),
            (ENDPOINT, {**paths, "revision": old["revision"]}),
        ):
            response = (
                client.post(endpoint, json=values)
                if endpoint.endswith("/check")
                else client.put(endpoint, json=values)
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "PATH_NOT_ALLOWED"
            assert_private_response(response.json(), setup)
        assert setup["config"].read_bytes() == before


def test_alias_symlink_escape_and_unauthorized_external_admin_edit_stay_private(setup, monkeypatch):
    app, _, _ = external_app(setup, monkeypatch)
    service = app.state.local_asr_settings
    unknown = setup["tmp_path"].parent / (
        setup["tmp_path"].name + "-unauthorized-home-personal-user"
    )
    unknown.mkdir()
    (service.external_roots[0] / "escape").symlink_to(unknown)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        old = client.get(ENDPOINT).json()
        checked = client.post(
            ENDPOINT + "/check", json={**old["active"], "crispasr_model_dir": "@root/1/escape"}
        )
        assert checked.status_code == 422
        raw = yaml.safe_load(setup["config"].read_bytes())
        raw["app"]["crispasr_model_dir"] = str(unknown)
        setup["config"].write_text(yaml.safe_dump(raw), encoding="utf-8")
        current = client.get(ENDPOINT).json()
        assert current["crispasr_model_dir"] == "@unavailable"
        assert current["restart_required"] and current["revision"] != old["revision"]
        assert_private_response(current, setup)
        checked = client.post(
            ENDPOINT + "/check", json={key: current[key] for key in current["active"]}
        )
        assert checked.status_code == 422
        assert "重启" in checked.json()["error"]["message"]
        assert_private_response(checked.json(), setup)


@pytest.mark.parametrize(
    "script,expected",
    [
        ("printf 'OtherProgram 1.2.3\\n'", "未识别"),
        ("printf 'CrispASR v1.2.3\\n'; exit 1", "未识别"),
        ("sleep 5", "超时"),
        ("head -c 10000 /dev/zero", "超出限制"),
    ],
)
def test_controlled_version_check_failure_modes(setup, script, expected):
    setup["alternative"].write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
    setup["app"].state.local_asr_settings.check_timeout = 0.1
    values = payload(setup)
    values.pop("revision")
    response = setup["client"].post(ENDPOINT + "/check", json=values)
    assert response.status_code == 200
    assert not response.json()["executable"]["ok"]
    assert expected in response.json()["executable"]["message"]


@pytest.mark.asyncio
async def test_cancelled_version_check_cleans_up_child(setup):
    service = setup["app"].state.local_asr_settings
    original_factory = service.process_factory
    started = asyncio.Event()
    processes = []

    async def tracked_factory(executable):
        process = await original_factory(executable)
        processes.append(process)
        started.set()
        return process

    service.process_factory = tracked_factory
    setup["alternative"].write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    task = asyncio.create_task(
        service.check(LocalASRPaths(**{key: payload(setup)[key] for key in service.active}))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[0].returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(processes[0].pid, 0)


def test_invalid_yaml_does_not_overwrite_file(setup):
    setup["config"].write_text("app: [", encoding="utf-8")
    before = setup["config"].read_bytes()
    response = setup["client"].put(ENDPOINT, json=payload(setup, revision="stale"))
    assert response.status_code == 503
    assert setup["config"].read_bytes() == before
