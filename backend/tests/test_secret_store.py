import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from app.storage.secret_store import SecretStore, SecretStoreError
from cryptography.fernet import Fernet

KEY_A = "sk-test-only-secret-one"
KEY_B = "sk-test-only-secret-two"
OWNER = ("prj_test", "node_asr", "qwen_asr")


def create(store, value=KEY_A, request="request:0", owner=OWNER):
    return store.create(*owner, value, request_id=request)


def files(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def assert_code(expected, callable):
    with pytest.raises(SecretStoreError) as caught:
        callable()
    assert caught.value.code == expected
    assert KEY_A not in repr(caught.value) and KEY_B not in str(caught.value)


def test_encrypted_restart_roundtrip_immutable_versions_and_no_cleartext(tmp_path):
    store = SecretStore(tmp_path)
    first = create(store)
    before = files(tmp_path)
    assert KEY_A not in repr(store)
    assert all(KEY_A.encode() not in content for content in before.values())
    record = json.loads(before[f".credentials/versions/{first}.json"])
    assert set(record) == {"schema_version", "token"}
    assert SecretStore(tmp_path).read(*OWNER, first) == KEY_A
    assert SecretStore(tmp_path).validate_ref(*OWNER, first) is None
    assert create(SecretStore(tmp_path)) == first
    assert files(tmp_path) == before
    second = create(store, KEY_B, "request:1")
    assert second != first
    assert store.read(*OWNER, first) == KEY_A and store.read(*OWNER, second) == KEY_B
    assert all(content == files(tmp_path)[path] for path, content in before.items())
    assert all(KEY_B.encode() not in content for content in files(tmp_path).values())


def test_same_request_changed_key_conflicts_without_overwrite(tmp_path):
    store = SecretStore(tmp_path)
    ref = create(store)
    before = files(tmp_path)
    assert_code("CREDENTIAL_REQUEST_CONFLICT", lambda: create(store, KEY_B))
    assert store.read(*OWNER, ref) == KEY_A
    assert files(tmp_path) == before


@pytest.mark.parametrize(
    "other",
    [
        ("prj_other", "node_asr", "qwen_asr"),
        ("prj_test", "node_other", "qwen_asr"),
        ("prj_test", "node_asr", "other_provider"),
    ],
)
def test_binding_rejects_cross_project_node_or_provider(tmp_path, other):
    store = SecretStore(tmp_path)
    ref = create(store)
    assert_code("CREDENTIAL_OWNER_MISMATCH", lambda: store.read(*other, ref))
    assert_code("CREDENTIAL_OWNER_MISMATCH", lambda: store.validate_ref(*other, ref))
    second = create(store, owner=other)
    assert second != ref and store.read(*other, second) == KEY_A


@pytest.mark.parametrize("operation", ["read", "validate", "create"])
@pytest.mark.parametrize(
    "damage", ["missing", "invalid", "wrong_valid_key", "missing_check", "invalid_check"]
)
def test_master_failure_never_generates_new_key_or_rewrites_tokens(
    tmp_path, monkeypatch, operation, damage
):
    store = SecretStore(tmp_path)
    ref = create(store)
    master = tmp_path / ".credentials/master.key"
    check = tmp_path / ".credentials/store.check"
    if damage == "missing":
        master.unlink()
    elif damage == "invalid":
        master.write_bytes(b"broken")
    elif damage == "wrong_valid_key":
        master.write_bytes(Fernet.generate_key())
    elif damage == "missing_check":
        check.unlink()
    else:
        check.write_bytes(b"broken")
    before = files(tmp_path)

    def forbidden():
        pytest.fail("A damaged/missing master must never generate a replacement")

    monkeypatch.setattr(Fernet, "generate_key", forbidden)
    code = (
        "CREDENTIAL_MASTER_KEY_MISSING" if damage == "missing" else "CREDENTIAL_MASTER_KEY_INVALID"
    )
    actions = {
        "read": lambda: store.read(*OWNER, ref),
        "validate": lambda: store.validate_ref(*OWNER, ref),
        "create": lambda: create(store, KEY_B, "fresh-request"),
    }
    assert_code(code, actions[operation])
    assert files(tmp_path) == before


def test_missing_or_empty_existing_store_never_initializes_on_read(tmp_path, monkeypatch):
    store = SecretStore(tmp_path)
    ref = "cred_" + uuid4().hex
    assert_code("CREDENTIAL_NOT_FOUND", lambda: store.read(*OWNER, ref))
    assert not (tmp_path / ".credentials").exists()
    (tmp_path / ".credentials").mkdir(mode=0o700)
    assert_code("CREDENTIAL_MASTER_KEY_MISSING", lambda: create(store))
    assert list((tmp_path / ".credentials").iterdir()) == []


def test_corrupt_truncated_or_swapped_tokens_fail_without_repair(tmp_path):
    store = SecretStore(tmp_path)
    first = create(store)
    second = create(store, KEY_B, "second")
    first_path = tmp_path / f".credentials/versions/{first}.json"
    original = first_path.read_bytes()
    first_path.write_bytes(original[: len(original) // 2])
    before = files(tmp_path)
    assert_code("CREDENTIAL_DECRYPT_FAILED", lambda: store.validate_ref(*OWNER, first))
    assert files(tmp_path) == before
    first_path.write_bytes((tmp_path / f".credentials/versions/{second}.json").read_bytes())
    assert_code("CREDENTIAL_OWNER_MISMATCH", lambda: store.read(*OWNER, first))


@pytest.mark.parametrize(
    "value", ["", " ", KEY_A + "\n", "Bearer " + KEY_A, "日文", None, {}, "x" * 8193]
)
def test_invalid_secret_not_in_errors_or_created_files(tmp_path, value):
    store = SecretStore(tmp_path)
    assert_code("CREDENTIAL_INVALID", lambda: create(store, value))
    assert not (tmp_path / ".credentials").exists()


@pytest.mark.parametrize(
    "ref", ["../master.key", "cred_../x", "C:\\x", "/x", "cred_" + "a" * 33, None]
)
def test_invalid_ref_never_becomes_path(tmp_path, ref):
    store = SecretStore(tmp_path)
    assert_code("CREDENTIAL_REF_INVALID", lambda: store.read(*OWNER, ref))
    assert not (tmp_path / ".credentials").exists()


def test_concurrent_multiple_instances_share_one_master_and_same_immutable_version(tmp_path):
    def same(_):
        return create(SecretStore(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as pool:
        references = list(pool.map(same, range(20)))
    assert len(set(references)) == 1
    assert len(list((tmp_path / ".credentials/versions").iterdir())) == 1
    assert SecretStore(tmp_path).read(*OWNER, references[0]) == KEY_A


def test_credential_versions_do_not_expire_while_queued_or_retried(tmp_path):
    store = SecretStore(tmp_path)
    ref = create(store)
    path = tmp_path / f".credentials/versions/{ref}.json"
    envelope = json.loads(path.read_bytes())
    cipher = Fernet((tmp_path / ".credentials/master.key").read_bytes())
    body = cipher.decrypt(envelope["token"].encode())
    envelope["token"] = cipher.encrypt_at_time(body, 1).decode()
    path.write_text(json.dumps(envelope))
    assert store.read(*OWNER, ref) == KEY_A


def test_hardlinked_master_or_version_is_unsafe(tmp_path):
    store = SecretStore(tmp_path)
    ref = create(store)
    record = tmp_path / f".credentials/versions/{ref}.json"
    record_link = tmp_path / "external-record-link"
    os.link(record, record_link)
    assert_code("CREDENTIAL_STORAGE_UNSAFE", lambda: store.read(*OWNER, ref))
    record_link.unlink()
    os.link(tmp_path / ".credentials/master.key", tmp_path / "external-master-link")
    assert_code("CREDENTIAL_STORAGE_UNSAFE", lambda: store.read(*OWNER, ref))


def test_storage_errors_are_fixed_and_never_expose_secret(tmp_path, monkeypatch):
    store = SecretStore(tmp_path)
    create(store)

    def failing_write(path, payload):
        raise OSError(KEY_A + " internal filesystem details")

    monkeypatch.setattr(store, "_exclusive_write", failing_write)
    assert_code("CREDENTIAL_STORAGE_ERROR", lambda: create(store, request="failed-request"))


def test_exclusive_creation_refuses_existing_path(tmp_path):
    path = tmp_path / "untouched"
    path.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        SecretStore._exclusive_write(path, b"replacement")
    assert path.read_bytes() == b"original"


def test_posix_private_permissions(tmp_path):
    if os.name == "nt":
        pytest.skip("Windows inherits the configured private data root ACL")
    create(SecretStore(tmp_path))
    for path in (tmp_path / ".credentials").rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_symlink_and_hardlink_entries_refused(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    other = tmp_path / "data"
    other.mkdir()
    link = other / ".credentials"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires host privileges")
    assert_code("CREDENTIAL_STORAGE_UNSAFE", lambda: create(SecretStore(other)))
    assert list(external.iterdir()) == []
    link.unlink()
    store = SecretStore(other)
    ref = create(store)
    record = other / f".credentials/versions/{ref}.json"
    os.link(record, external / "copy")
    assert_code("CREDENTIAL_STORAGE_UNSAFE", lambda: store.read(*OWNER, ref))


def test_windows_junction_refused_without_writing_outside_root(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction test")
    import subprocess

    external = tmp_path / "external"
    external.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    junction = data / ".credentials"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip("Host does not permit directory junction creation")
    try:
        assert_code("CREDENTIAL_STORAGE_UNSAFE", lambda: create(SecretStore(data)))
        assert list(external.iterdir()) == []
    finally:
        # rmdir removes this named junction itself; never recurse into its target.
        junction.rmdir()


def test_credential_store_outside_project_export_and_project_delete_boundary(tmp_path):
    from app.storage.project_store import FileSystemProjectStore

    secret_store = SecretStore(tmp_path)
    ref = create(secret_store)
    projects = FileSystemProjectStore(tmp_path)
    project = projects.project_dir(OWNER[0])
    project.mkdir()
    (project / "project.json").write_text("{}")
    projects.delete_project(OWNER[0])
    # No implicit GC: deleting a project cannot damage other refs or the master.
    assert secret_store.read(*OWNER, ref) == KEY_A
    assert not list(projects.projects_dir.iterdir())
    assert all(Path(name).parts[0] == ".credentials" for name in files(tmp_path))
