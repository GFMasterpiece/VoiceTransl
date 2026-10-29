"""Contained child process execution with a Windows Job Object handshake.

A small trampoline is spawned with stdin as a control channel. On Windows the
parent creates a kill-on-close Job Object, assigns the already-existing
trampoline, then sends GO. The trampoline only starts the real executable
after receiving that byte, so descendants cannot be created before containment.
POSIX uses a new session and process-group termination instead.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import shutil
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .platform_io import (
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    PlatformIOError,
    process_spawn_kwargs,
)

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
TRAMPOLINE = Path(__file__).with_name("process_trampoline.py")
FAKE_TERMINATE_SECONDS = 0.02


@dataclass
class ContainedProcess:
    process: asyncio.subprocess.Process
    job: object | None = None

    @property
    def returncode(self):
        return self.process.returncode

    @property
    def pid(self):
        return getattr(self.process, "pid", None)

    @property
    def stdout(self):
        return self.process.stdout

    @property
    def stderr(self):
        return self.process.stderr

    @property
    def stdin(self):
        return self.process.stdin

    async def wait(self):
        wait = getattr(self.process, "wait", None)
        if wait is None:
            return self.returncode
        return await wait()

    async def communicate(self):
        return await self.process.communicate()

    def terminate(self):
        self.close_job()
        if self.process.returncode is None:
            self.process.terminate()

    def kill(self):
        self.close_job()
        if self.process.returncode is None:
            self.process.kill()

    def close_job(self) -> None:
        if self.job is not None:
            handle = self.job
            self.job = None
            _kernel32.CloseHandle(handle)


if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_job(pid: int):
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise PlatformIOError("unable to create job object")
    limits = _ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        job,
        JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        _kernel32.CloseHandle(job)
        raise PlatformIOError("unable to configure job object")
    handle = _kernel32.OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, False, pid)
    if not handle:
        _kernel32.CloseHandle(job)
        raise PlatformIOError("unable to open trampoline process")
    try:
        if not _kernel32.AssignProcessToJobObject(job, handle):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(job)
            raise PlatformIOError(error, "unable to assign trampoline to job object")
    finally:
        _kernel32.CloseHandle(handle)
    return job


async def spawn_contained(
    command: Sequence[str],
    *,
    stdin=asyncio.subprocess.DEVNULL,
    stdout=asyncio.subprocess.DEVNULL,
    stderr=asyncio.subprocess.DEVNULL,
) -> ContainedProcess:
    """Spawn a command whose descendants stay in a contained process tree."""
    if not command:
        raise ValueError("command must not be empty")
    if not IS_WINDOWS:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            **process_spawn_kwargs(),
        )
        return ContainedProcess(process)

    # The trampoline reports child launch failures through its exit status, so
    # catch a missing executable before crossing that status boundary.
    if shutil.which(command[0]) is None:
        raise FileNotFoundError(command[0])
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(TRAMPOLINE),
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        creationflags=CREATE_NO_WINDOW,
    )
    job = None
    try:
        job = _create_job(process.pid)
        assert process.stdin is not None
        process.stdin.write(b"GO" + bytes([10]))
        await process.stdin.drain()
        process.stdin.close()
        return ContainedProcess(process, job)
    except BaseException:
        # Closing the Job kills the trampoline and every descendant assigned to
        # it. Always reap the real Windows parent with its native pid; never use
        # the finite fake-process fallback here.
        try:
            if job is not None:
                _kernel32.CloseHandle(job)
                job = None
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await reap_contained(ContainedProcess(process))
        finally:
            if job is not None:
                _kernel32.CloseHandle(job)
        raise


async def terminate_contained(process: ContainedProcess) -> None:
    """Terminate the full contained tree and reap the parent without abandoning it."""
    if IS_WINDOWS:
        # Closing the Job kills descendants even if the trampoline exited.
        process.close_job()
        if process.returncode is None:
            try:
                process.process.kill()
            except ProcessLookupError:
                pass
        # A real Windows PID must be reaped even if its exit is already visible.
        await reap_contained(process)
        return
    elif process.pid is not None:
        # A dead leader does not prove its process group is empty.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await reap_contained(process)
        return
    # Injected fake/test processes have no native pid. Never enter the
    # non-abandonable native reap loop for them: terminate/kill, then await a
    # finite contract if provided, or return once no wait exists.
    if process.returncode is None:
        try:
            process.process.terminate()
        except (AttributeError, ProcessLookupError):
            pass
    wait = getattr(process.process, "wait", None)
    wait_task = asyncio.create_task(wait()) if callable(wait) else None
    if wait_task is not None:
        done, _ = await asyncio.wait({wait_task}, timeout=FAKE_TERMINATE_SECONDS)
        if not done:
            try:
                process.process.kill()
            except (AttributeError, ProcessLookupError):
                pass
            done, _ = await asyncio.wait({wait_task}, timeout=FAKE_TERMINATE_SECONDS)
        if not done:
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        else:
            try:
                await wait_task
            except (ProcessLookupError, ChildProcessError):
                pass


async def reap_contained(process: ContainedProcess) -> None:
    """Reap without abandoning the wait when cancellation repeats."""
    wait = asyncio.create_task(process.wait())
    try:
        while not wait.done():
            try:
                await asyncio.shield(wait)
            except asyncio.CancelledError:
                continue
        await wait
    except (ProcessLookupError, ChildProcessError):
        pass
    finally:
        process.close_job()


__all__ = [
    "ContainedProcess",
    "reap_contained",
    "spawn_contained",
    "terminate_contained",
]
