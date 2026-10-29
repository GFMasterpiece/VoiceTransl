"""Cancellation tests against a real loopback HTTP server and HTTPX transport."""

import asyncio
import contextlib
import json

import pytest
from app.domain import Segment, TranslationConfig
from app.main import create_app  # noqa: F401 - initialize provider modules before protocols
from app.providers.protocols import TranslationContext
from app.providers.translation import HTTPTranslationProvider, TranslationProviderError


def make_config(base_url: str, timeout: int = 30) -> TranslationConfig:
    return TranslationConfig(
        provider="openai_chat",
        base_url=base_url,
        api_key_env="TEST_TRANSLATION_KEY",
        model="test-model",
        target_language="zh-CN",
        timeout_seconds=timeout,
    )


class LoopbackServer:
    def __init__(self):
        self.server: asyncio.Server | None = None
        self.body_read = asyncio.Event()
        self.finished = asyncio.Event()
        self.error: BaseException | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.peer_eof: asyncio.Event | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self.server.sockets or ()
        assert len(sockets) == 1
        self.port = sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.writer = writer
        eof = asyncio.Event()
        self.peer_eof = eof
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            headers = {}
            for line in header.decode("ascii").split("\r\n")[1:]:
                if not line:
                    continue
                name, value = line.split(":", 1)
                headers[name.casefold()] = value.strip()
            length = int(headers.get("content-length", "0"))
            body = await asyncio.wait_for(reader.readexactly(length), 5)
            assert json.loads(body)["model"] == "test-model"
            self.body_read.set()
            # Never answer. A successful cancellation closes the client
            # connection, which this real server observes as EOF.
            while not eof.is_set():
                chunk = await asyncio.wait_for(reader.read(4096), 5)
                if not chunk:
                    eof.set()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.error = exc
        finally:
            self.finished.set()
            if not writer.is_closing():
                writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 5)

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
        if self.writer is not None and not self.writer.is_closing():
            self.writer.close()
        if self.server is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 5)
        if self.writer is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.writer.wait_closed(), 5)

    async def wait_for_eof(self) -> None:
        assert self.peer_eof is not None
        await asyncio.wait_for(self.peer_eof.wait(), 8)


async def wait_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), 5)


async def run_cancel_scenario(scenario: str, monkeypatch) -> None:
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "test-only")
    server = LoopbackServer()
    await server.start()
    cancel_event = asyncio.Event()
    provider = HTTPTranslationProvider(
        make_config(
            f"http://127.0.0.1:{server.port}/v1",
            timeout=1 if scenario == "timeout" else 30,
        ),
        cancel_event=cancel_event if scenario != "task-cancel" else None,
    )
    task = asyncio.create_task(
        provider.translate([Segment(id="seg_0", source_text="原文")], TranslationContext())
    )
    try:
        await wait_event(server.body_read)
        if scenario == "event":
            cancel_event.set()
        elif scenario == "task-cancel":
            task.cancel()
        with pytest.raises((TranslationProviderError, asyncio.CancelledError)):
            await asyncio.wait_for(asyncio.shield(task), 8)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, 8)
        await server.wait_for_eof()
        await wait_event(server.finished)
        await server.stop()
    assert server.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["event", "timeout", "task-cancel"])
async def test_real_socket_cancellation_reaches_provider_eof(scenario, monkeypatch):
    await run_cancel_scenario(scenario, monkeypatch)


@pytest.mark.asyncio
async def test_repeated_task_cancellation_still_waits_for_socket_cleanup(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "test-only")
    server = LoopbackServer()
    await server.start()
    provider = HTTPTranslationProvider(
        make_config(f"http://127.0.0.1:{server.port}/v1"), cancel_event=None
    )
    task = asyncio.create_task(
        provider.translate([Segment(id="seg_0", source_text="原文")], TranslationContext())
    )
    try:
        await wait_event(server.body_read)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(task, 8)
        await server.wait_for_eof()
        await wait_event(server.finished)
        await server.stop()
    assert server.error is None
