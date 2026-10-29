import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from app.application.platform_io import IS_WINDOWS, FileLock, open_model_root
from app.application.platform_process import spawn_contained, terminate_contained


def _exists(pid: int) -> bool:
    if IS_WINDOWS:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _alive_non_zombie(pid: int) -> bool:
    if IS_WINDOWS:
        return False
    try:
        status = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return status.split()[2] != "Z"


@pytest.mark.asyncio
async def test_contained_process_preserves_spaced_unicode_arguments_and_exit_code():
    code = "import sys; sys.exit(17 if sys.argv[1] == '带 空格' else 9)"
    process = await spawn_contained([sys.executable, "-c", code, "带 空格"])
    assert await process.wait() == 17


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX process-group semantics")
@pytest.mark.asyncio
async def test_terminate_contained_kills_descendants_after_parent_exits(tmp_path):
    marker = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"open(r'{marker}','w').write(str(p.pid)); "
        "sys.exit(0)"
    )
    process = await spawn_contained([sys.executable, "-c", code])
    assert await process.wait() == 0
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    child_pid = int(marker.read_text())
    assert _exists(child_pid)
    await terminate_contained(process)
    deadline = time.monotonic() + 2
    while _alive_non_zombie(child_pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert not _alive_non_zombie(child_pid)


def test_file_lock_conflict_and_release(tmp_path):
    path = tmp_path / "settings.lock"
    with FileLock(path):
        with pytest.raises(BlockingIOError):
            with FileLock(path):
                pass
    with FileLock(path):
        pass


@pytest.mark.skipif(IS_WINDOWS, reason="symlink creation is native Windows-only below")
def test_posix_model_root_rejects_symlinked_ancestor(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        with open_model_root(linked):
            pass


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows directory handle semantics")
def test_windows_model_root_rejects_unc_and_device_paths():
    for value in (
        chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "models",
        "//server/share/models",
        chr(92) * 2 + "?" + chr(92) + "C:" + chr(92) + "models",
    ):
        with pytest.raises(OSError):
            with open_model_root(Path(value)):
                pass


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows directory handle semantics")
def test_windows_model_root_rejects_directory_junction(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linked), str(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("this Windows account/filesystem cannot create a junction")
    try:
        with pytest.raises(OSError):
            with open_model_root(linked):
                pass
    finally:
        assert linked.resolve().parent == tmp_path.resolve()
        assert linked.is_junction()
        linked.rmdir()
    assert target.is_dir()


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows directory handle semantics")
def test_windows_held_directory_blocks_junction_replacement(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linked), str(target)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("this Windows account/filesystem cannot create a junction")
    try:
        # open_model_root rejects the junction itself. Separately hold the
        # canonical target directory chain and prove Windows refuses to rename
        # that target while a no-FILE_SHARE_DELETE handle remains open.
        with pytest.raises(OSError):
            with open_model_root(linked):
                pass
        with open_model_root(target):
            with pytest.raises(OSError):
                target.rename(tmp_path / "replacement")
    finally:
        assert linked.resolve().parent == tmp_path.resolve()
        assert linked.is_junction()
        linked.rmdir()
    assert target.is_dir()


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows job-object semantics")
@pytest.mark.asyncio
async def test_windows_job_assignment_failure_reaps_trampoline(monkeypatch):
    from app.application import platform_process

    created = []
    original_create = platform_process.asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        process = await original_create(*args, **kwargs)
        created.append(process)
        return process

    captured_handle = []
    captured_pid = []

    def fail_create_job(pid):
        # Bind the wait handle to the actual trampoline before injecting the
        # failure, so the assertion cannot accidentally observe a reused PID.
        captured_pid.append(pid)
        captured_handle.append(_open_synchronize_handle(pid))
        raise OSError("injected assignment failure")

    monkeypatch.setattr(platform_process.asyncio, "create_subprocess_exec", capture_process)
    monkeypatch.setattr(platform_process, "_create_job", fail_create_job)
    try:
        with pytest.raises(OSError, match="injected assignment failure"):
            await platform_process.spawn_contained([sys.executable, "-c", "raise SystemExit(91)"])
        assert len(created) == len(captured_pid) == len(captured_handle) == 1
        tested = created[0]
        assert captured_pid[0] == tested.pid
        assert tested.returncode is not None
        assert _wait_handle(captured_handle[0]) == 0
        assert await tested.wait() == tested.returncode
    finally:
        for handle in captured_handle:
            _close_handle(handle)


def _open_synchronize_handle(pid: int) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    assert handle
    return handle


def _wait_handle(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(handle, 2000)


def _close_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle(handle)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows job-object semantics")
@pytest.mark.asyncio
async def test_windows_job_closes_after_parent_exit(tmp_path):
    marker = tmp_path / "win-child.pid"
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        f"open(r'{marker}','w').write(str(p.pid)); "
        "sys.exit(0)"
    )
    process = await spawn_contained([sys.executable, "-c", code])
    assert await process.wait() == 0
    deadline = time.monotonic() + 2
    while not marker.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert marker.exists()
    child_pid = int(marker.read_text())
    # Open the process handle before closing the Job. A handle is immune to
    # PID reuse and WaitForSingleObject does not depend on localized tasklist.
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, child_pid)
    assert handle
    try:
        await terminate_contained(process)
        assert kernel32.WaitForSingleObject(handle, 2000) == 0
    finally:
        kernel32.CloseHandle(handle)
