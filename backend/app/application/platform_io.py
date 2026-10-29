"""Portable filesystem, locking and child-process primitives.

Security-sensitive callers use these helpers instead of branching on
os.name at each call site. POSIX operations remain descriptor-relative;
Windows directory access holds handles without FILE_SHARE_DELETE and rejects
reparse points so an ancestor cannot be swapped during a scan.
"""

from __future__ import annotations

import ctypes
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

IS_WINDOWS = os.name == "nt"
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
CREATE_NO_WINDOW = 0x08000000


class PlatformIOError(OSError):
    pass


def _win_error(message: str) -> PlatformIOError:
    return PlatformIOError(getattr(ctypes, "get_last_error", lambda: 0)(), message)


if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.ReadFile.restype = wintypes.BOOL
    _kernel32.SetFilePointerEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.DWORD,
    ]
    _kernel32.SetFilePointerEx.restype = wintypes.BOOL
    _kernel32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD


class _WinFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTimeLow", ctypes.c_uint32),
        ("ftCreationTimeHigh", ctypes.c_uint32),
        ("ftLastAccessTimeLow", ctypes.c_uint32),
        ("ftLastAccessTimeHigh", ctypes.c_uint32),
        ("ftLastWriteTimeLow", ctypes.c_uint32),
        ("ftLastWriteTimeHigh", ctypes.c_uint32),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


def _windows_read(descriptor: int, count: int) -> bytes:
    import msvcrt

    handle = msvcrt.get_osfhandle(descriptor)
    buffer = ctypes.create_string_buffer(count)
    read = wintypes.DWORD()
    if not _kernel32.ReadFile(handle, buffer, count, ctypes.byref(read), None):
        raise _win_error("unable to read model metadata")
    return buffer.raw[: read.value]


class WindowsDirectory:
    """A directory chain opened without FILE_SHARE_DELETE."""

    def __init__(self, root: Path):
        if not IS_WINDOWS:
            raise PlatformIOError("Windows directory handles are unavailable")
        if not root.is_absolute() or not root.anchor:
            raise PlatformIOError("Windows directory must be absolute")
        # The secure directory handle walk does not model UNC/device roots.
        # Reject them rather than claiming containment we have not tested.
        if str(root).startswith((chr(92) * 2, "//")):
            raise PlatformIOError("Windows UNC model roots are not supported")
        self._handles: list[int] = []
        current = Path(root.anchor)
        try:
            self._handles.append(self._open(str(current), directory=True))
            for part in root.parts[1:]:
                current = current / part
                self._handles.append(self._open(str(current), directory=True))
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _open(path: str, *, directory: bool) -> int:
        flags = FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= FILE_FLAG_BACKUP_SEMANTICS
        handle = _kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            flags,
            None,
        )
        if handle == INVALID_HANDLE_VALUE:
            raise _win_error("unable to open path")
        info = _WinFileInfo()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            _kernel32.CloseHandle(handle)
            raise _win_error("unable to inspect path")
        if info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            _kernel32.CloseHandle(handle)
            raise PlatformIOError("reparse point is not allowed")
        if directory and not info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY:
            _kernel32.CloseHandle(handle)
            raise PlatformIOError("expected a directory")
        return handle

    def _final_path(self) -> str:
        size = _kernel32.GetFinalPathNameByHandleW(self._handles[-1], None, 0, 0)
        if not size:
            raise _win_error("unable to resolve directory")
        buffer = ctypes.create_unicode_buffer(size + 1)
        if not _kernel32.GetFinalPathNameByHandleW(self._handles[-1], buffer, len(buffer), 0):
            raise _win_error("unable to resolve directory")
        value = buffer.value
        if value.startswith(chr(92) * 2 + chr(63) + chr(92)):
            value = value[4:]
        return value

    def scan_entries(self) -> Iterator[str]:
        directory = self._final_path()
        with os.scandir(directory) as entries:
            for entry in entries:
                yield entry.name

    def open_file(self, name: str) -> SecureModelFile:
        if not name or name in {".", ".."} or "/" in name or chr(92) in name:
            raise PlatformIOError("invalid directory entry")
        path = str(Path(self._final_path()) / name)
        handle = self._open(path, directory=False)
        info = _WinFileInfo()
        if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            _kernel32.CloseHandle(handle)
            raise _win_error("unable to inspect file")
        if info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY):
            _kernel32.CloseHandle(handle)
            raise PlatformIOError("expected a regular file")
        import msvcrt

        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        return SecureModelFile(os.fdopen(descriptor, "rb", buffering=0))

    def close(self) -> None:
        while self._handles:
            _kernel32.CloseHandle(self._handles.pop())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class SecureModelFile:
    """A Windows model handle with one scanner-owned read cursor."""

    def __init__(self, file: BinaryIO):
        self.file = file

    def fileno(self) -> int:
        return self.file.fileno()

    def read_at(self, count: int, position: int) -> bytes:
        offset = os.lseek(self.fileno(), position, os.SEEK_SET)
        if offset != position:
            raise OSError("unable to seek secure model handle")
        return _windows_read(self.fileno(), count)

    def close(self) -> None:
        self.file.close()


class FileLock:
    """Open a lock file and acquire one non-blocking exclusive lock."""

    def __init__(self, path: Path):
        self.path = path
        self.file: BinaryIO | None = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        try:
            if IS_WINDOWS:
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            self.file.close()
            self.file = None
            raise BlockingIOError("lock is already held")
        return self.file

    def __exit__(self, *_args):
        if self.file is not None:
            try:
                if IS_WINDOWS:
                    import msvcrt

                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.file, fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


def set_mode_from(path: Path, file: BinaryIO) -> None:
    """Copy the config mode when POSIX supports it; Windows has ACLs instead."""
    if IS_WINDOWS:
        return
    os.fchmod(file.fileno(), stat.S_IMODE(path.stat().st_mode))


def process_spawn_kwargs() -> dict[str, object]:
    """Portable flags for a child that must be independently stoppable."""
    if IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    return {"start_new_session": True}


def pread(descriptor: int, count: int, position: int) -> bytes:
    if hasattr(os, "pread"):
        return os.pread(descriptor, count, position)
    offset = os.lseek(descriptor, position, os.SEEK_SET)
    if offset != position:
        raise OSError("unable to seek secure file handle")
    return os.read(descriptor, count)


@contextmanager
def open_model_root(root: Path) -> Iterator[object]:
    """Yield a directory entry API that rejects links and reparse points."""
    if IS_WINDOWS:
        with WindowsDirectory(root) as directory:
            yield directory
        return
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_DIRECTORY
    descriptor = os.open(root.anchor, flags)
    try:
        for part in root.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)
