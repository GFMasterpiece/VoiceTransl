"""Private, authenticated, immutable credential versions; never part of a graph.

Fernet protects exported workflow/backup material that omits this directory.
Keeping its master key beside ciphertext does not protect a compromised data root.
Windows inherits the data root's ACL; POSIX creation modes also restrict access.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import stat
from pathlib import Path
from threading import Lock, RLock
from uuid import NAMESPACE_URL, uuid5

from cryptography.fernet import Fernet, InvalidToken

_REF = re.compile(r"cred_[0-9a-f]{32}")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_REQUEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")
_MAX_SECRET_BYTES = 8192
_MAX_RECORD_BYTES = 32768
_STORE_CHECK = b"VoiceTransl credential store v1"
_LOCKS: dict[str, RLock] = {}
_LOCKS_GUARD = Lock()


class SecretStoreError(ValueError):
    """Only fixed, non-secret messages may cross the API/logging boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _error(code, message):
    raise SecretStoreError(code, message) from None


class SecretStore:
    def __init__(self, data_dir: str | Path):
        self._data_dir = Path(data_dir).resolve()
        self._private = self._data_dir / ".credentials"
        self._versions = self._private / "versions"
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(os.path.normcase(str(self._data_dir)), RLock())

    @staticmethod
    def _owner(project_id, node_id, provider_type):
        values = (project_id, node_id, provider_type)
        if any(not isinstance(item, str) or not _OWNER.fullmatch(item) for item in values):
            _error("CREDENTIAL_INVALID", "凭据归属信息无效")
        return values

    @staticmethod
    def _secret(api_key):
        if (
            not isinstance(api_key, str)
            or not api_key
            or len(api_key) > _MAX_SECRET_BYTES
            or any(ord(char) < 33 or ord(char) > 126 for char in api_key)
        ):
            _error("CREDENTIAL_INVALID", "API Key必须为有效的非空文本，不能包含空白或控制字符")
        return api_key

    @staticmethod
    def _entry(path: Path, parent: Path, *, directory=False):
        """Check the raw entry before resolve(), including Windows reparse points."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or path.resolve().parent != parent.resolve()
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
            or (os.name != "nt" and info.st_mode & 0o077)
        ):
            _error("CREDENTIAL_STORAGE_UNSAFE", "凭据存储路径或访问权限不安全")
        return True

    @staticmethod
    def _exclusive_write(path, payload):
        # O_EXCL is essential: atomic_write_bytes uses replace(), which would
        # overwrite another master/version. No reference is published before fsync.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # This call created the entry and has not returned its reference.
            path.unlink(missing_ok=True)
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _master(self):
        path = self._private / "master.key"
        if not self._entry(path, self._private):
            _error(
                "CREDENTIAL_MASTER_KEY_MISSING", "凭据主密钥缺失；请恢复原密钥，系统不会自动重建"
            )
        with path.open("rb") as stream:
            key = stream.read(65)
        try:
            if len(key) != 44:
                raise ValueError
            cipher = Fernet(key)
            check = self._private / "store.check"
            if not self._entry(check, self._private):
                raise ValueError
            with check.open("rb") as stream:
                token = stream.read(1025)
            if len(token) > 1024 or not hmac.compare_digest(cipher.decrypt(token), _STORE_CHECK):
                raise ValueError
            return cipher
        except (ValueError, TypeError, InvalidToken):
            _error(
                "CREDENTIAL_MASTER_KEY_INVALID",
                "凭据主密钥或校验文件无效；请恢复原存储，系统不会自动替换",
            )

    def _prepare(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fresh = False
        if not self._entry(self._private, self._data_dir, directory=True):
            try:
                self._private.mkdir(mode=0o700)
                fresh = True
            except FileExistsError:
                pass
        self._entry(self._private, self._data_dir, directory=True)
        if fresh:
            # Generate only for a genuinely new private store. Even an existing
            # empty store without its master is an explicit recovery error.
            key = Fernet.generate_key()
            self._exclusive_write(self._private / "master.key", key)
            self._exclusive_write(self._private / "store.check", Fernet(key).encrypt(_STORE_CHECK))
        cipher = self._master()
        if not self._entry(self._versions, self._private, directory=True):
            self._versions.mkdir(mode=0o700, exist_ok=True)
        self._entry(self._versions, self._private, directory=True)
        return cipher

    def _read(self, owner, credential_ref):
        if not isinstance(credential_ref, str) or not _REF.fullmatch(credential_ref):
            _error("CREDENTIAL_REF_INVALID", "凭据引用无效")
        if not self._entry(self._private, self._data_dir, directory=True) or not self._entry(
            self._versions, self._private, directory=True
        ):
            _error("CREDENTIAL_NOT_FOUND", "保存的凭据不存在，请重新设置API Key")
        path = self._versions / f"{credential_ref}.json"
        if not self._entry(path, self._versions):
            _error("CREDENTIAL_NOT_FOUND", "保存的凭据不存在，请重新设置API Key")
        cipher = self._master()
        try:
            with path.open("rb") as stream:
                encoded = stream.read(_MAX_RECORD_BYTES + 1)
            if len(encoded) > _MAX_RECORD_BYTES:
                raise ValueError
            envelope = json.loads(encoded)
            if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "token"}:
                raise ValueError
            if envelope["schema_version"] != "1.0" or not isinstance(envelope["token"], str):
                raise ValueError
            # No TTL: immutable queued/retry credentials must not age out.
            value = json.loads(cipher.decrypt(envelope["token"].encode("ascii")))
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema_version",
                    "credential_ref",
                    "project_id",
                    "node_id",
                    "provider_type",
                    "api_key",
                }
                or value["schema_version"] != "1.0"
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError, UnicodeError, InvalidToken, RecursionError):
            _error("CREDENTIAL_DECRYPT_FAILED", "保存的凭据无法验证或解密；请检查原主密钥和密文")
        if (
            value["credential_ref"] != credential_ref
            or tuple(value[key] for key in ("project_id", "node_id", "provider_type")) != owner
        ):
            _error("CREDENTIAL_OWNER_MISMATCH", "凭据不属于当前项目、节点或服务类型")
        return self._secret(value["api_key"])

    def create(self, project_id, node_id, provider_type, api_key, *, request_id: str) -> str:
        owner = self._owner(project_id, node_id, provider_type)
        self._secret(api_key)
        if not isinstance(request_id, str) or not _REQUEST.fullmatch(request_id):
            _error("CREDENTIAL_INVALID", "凭据保存请求标识无效")
        material = json.dumps([*owner, request_id], ensure_ascii=True, separators=(",", ":"))
        credential_ref = "cred_" + uuid5(NAMESPACE_URL, "voicetransl:credential:v1:" + material).hex
        try:
            with self._lock:
                cipher = self._prepare()
                path = self._versions / f"{credential_ref}.json"
                if self._entry(path, self._versions):
                    existing = self._read(owner, credential_ref)
                    if not hmac.compare_digest(existing.encode(), api_key.encode()):
                        _error("CREDENTIAL_REQUEST_CONFLICT", "同一保存请求不能替换为不同API Key")
                    return credential_ref
                payload = json.dumps(
                    {
                        "schema_version": "1.0",
                        "credential_ref": credential_ref,
                        "project_id": owner[0],
                        "node_id": owner[1],
                        "provider_type": owner[2],
                        "api_key": api_key,
                    },
                    separators=(",", ":"),
                ).encode()
                envelope = {
                    "schema_version": "1.0",
                    "token": cipher.encrypt(payload).decode("ascii"),
                }
                self._exclusive_write(path, json.dumps(envelope, separators=(",", ":")).encode())
                return credential_ref
        except SecretStoreError:
            raise
        except (OSError, ValueError, TypeError):
            _error("CREDENTIAL_STORAGE_ERROR", "凭据存储操作失败，请检查服务端存储状态和权限")

    def read(self, project_id, node_id, provider_type, credential_ref) -> str:
        owner = self._owner(project_id, node_id, provider_type)
        try:
            with self._lock:
                return self._read(owner, credential_ref)
        except SecretStoreError:
            raise
        except (OSError, ValueError, TypeError):
            _error("CREDENTIAL_STORAGE_ERROR", "凭据存储操作失败，请检查服务端存储状态和权限")

    def validate_ref(self, project_id, node_id, provider_type, credential_ref) -> None:
        self.read(project_id, node_id, provider_type, credential_ref)

    def _defaults(self):
        store = SecretStore(self._data_dir)
        store._private = self._data_dir / ".default_credentials"
        store._versions = store._private / "versions"
        return store

    def create_default(self, node_type, provider_type, api_key, *, request_id: str) -> str:
        ref = self._defaults().create(
            "global-defaults", node_type, provider_type, api_key, request_id=request_id
        )
        return "d" + ref

    def read_default(self, node_type, provider_type, credential_version) -> str:
        if not isinstance(credential_version, str) or not re.fullmatch(
            r"dcred_[0-9a-f]{32}", credential_version
        ):
            _error("CREDENTIAL_INVALID", "默认凭据版本无效")
        return self._defaults().read(
            "global-defaults", node_type, provider_type, credential_version[1:]
        )
