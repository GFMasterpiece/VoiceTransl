import asyncio
from pathlib import Path

import pytest
from app.domain import ASRConfig, Project, SourceAsset
from app.providers.crispasr import CrispASRError, CrispASRProvider, parse_json_transcript, parse_srt


def project() -> Project:
    return Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=1),
    )


def setup_files(tmp_path: Path):
    binary = tmp_path / "crispasr"
    binary.write_bytes(b"binary")
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.gguf").write_bytes(b"model")
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF")
    return binary, models, audio


class FakeProcess:
    def __init__(self, command, *, output=True, delay=0, returncode=0):
        self.command = command
        self.output = output
        self.delay = delay
        self.returncode = None
        self._result = returncode
        self.terminated = False

    async def communicate(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.output:
            base = Path(self.command[self.command.index("--output-file") + 1])
            base.with_suffix(".srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
            )
        self.returncode = self._result
        return b"", b""

    async def wait(self):
        self.returncode = self._result
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["qwen3-1.7b", "qwen3"])
async def test_provider_builds_safe_argv_and_parses_srt(tmp_path, backend):
    binary, models, audio = setup_files(tmp_path)
    seen = {}

    async def factory(*command):
        seen["command"] = command
        return FakeProcess(command)

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", backend=backend, model="model.gguf", language="ja"),
        process_factory=factory,
    )
    result = await provider.transcribe(audio, project())
    assert result.segments[0].source_text == "hello"
    command = list(seen["command"])
    assert command[command.index("--backend") + 1] == backend
    assert "--vad-max-speech-duration-s" in command
    assert "--shell" not in command


def test_parsers_reject_malformed_and_accept_json():
    assert parse_json_transcript('{"segments":[{"start":0,"end":1,"text":"ok"}]}')[0].end == 1
    with pytest.raises(ValueError):
        parse_srt("1\n00:00:02,000 --> 00:00:01,000\nno\n")
    with pytest.raises(ValueError):
        parse_json_transcript('{"segments":[{"start":0,"end":1,"text":""}]}')


def test_parsers_accept_zero_duration_ranges_without_changing_them():
    srt = parse_srt("1\n00:00:01,000 --> 00:00:01,000\nzero\n")
    assert (srt[0].start, srt[0].end) == (1.0, 1.0)
    parsed = parse_json_transcript('{"segments":[{"start":2.5,"end":2.5,"text":"zero"}]}')
    assert (parsed[0].start, parsed[0].end) == (2.5, 2.5)


@pytest.mark.asyncio
async def test_provider_reports_missing_model_and_output(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    missing = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="../escape"),
    )
    with pytest.raises(CrispASRError, match="模型") as error:
        await missing.transcribe(audio, project())
    assert error.value.code == "ASR_MODEL_INVALID"

    async def no_output(*command):
        return FakeProcess(command, output=False)

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        process_factory=no_output,
    )
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_OUTPUT_MISSING"


@pytest.mark.asyncio
async def test_provider_timeout_terminates_process(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    process = None

    async def factory(*command):
        nonlocal process
        process = FakeProcess(command, delay=1)
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        timeout_seconds=0.01,
        process_factory=factory,
    )
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_TIMEOUT"
    assert process.terminated


@pytest.mark.asyncio
async def test_provider_cancellation_terminates_and_reaps_process(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    cancel = asyncio.Event()
    process = None

    async def factory(*command):
        nonlocal process
        process = FakeProcess(command, delay=1)
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        cancel_event=cancel,
        process_factory=factory,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.sleep(0.01)
    cancel.set()
    with pytest.raises(CrispASRError) as error:
        await task
    assert error.value.code == "ASR_CANCELLED"
    assert process.terminated


class ReapingProcess:
    def __init__(self, command, *, delay=0.0, block_terminate=False, output=True):
        self.command = command
        self.delay = delay
        self.block_terminate = block_terminate
        self.output = output
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.communicate_calls = 0
        self.wait_started = asyncio.Event()
        self.release_wait = asyncio.Event()
        self.communicate_cancelled = False

    async def communicate(self):
        self.communicate_calls += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.communicate_cancelled = True
            raise
        if self.output:
            base = Path(self.command[self.command.index("--output-file") + 1])
            base.with_suffix(".srt").write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
            )
        if self.returncode is None:
            self.returncode = 0
        # Real communicate() completes only after the child exits; release the
        # fake wait so success-path reaping can complete immediately.
        self.release_wait.set()
        return b"", b""

    async def wait(self):
        self.wait_calls += 1
        self.wait_started.set()
        await self.release_wait.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.block_terminate:
            self.returncode = -15
            self.release_wait.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.release_wait.set()


@pytest.mark.asyncio
async def test_spawn_event_cancel_waits_for_late_process_and_reaps(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    cancel = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    process = ReapingProcess([], block_terminate=True)

    async def factory(*command):
        process.command = command
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            return process
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        cancel_event=cancel,
        process_factory=factory,
        terminate_seconds=0.01,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    cancel.set()
    await asyncio.sleep(0.02)
    assert not task.done()
    release.set()
    with pytest.raises(CrispASRError) as error:
        await task
    assert error.value.code == "ASR_CANCELLED"
    assert process.terminated and process.killed
    assert process.wait_calls >= 1


@pytest.mark.asyncio
async def test_spawn_task_cancel_waits_for_swallowed_cancel_and_reaps(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()
    process = ReapingProcess([], block_terminate=False)

    async def factory(*command):
        process.command = command
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            return process
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        process_factory=factory,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    # The factory still gets a chance to swallow cancellation and return its
    # child. Cleanup must wait for it and reap it before propagating cancel.
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_spawn_timeout_waits_for_late_process_and_reaps(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    release = asyncio.Event()
    process = ReapingProcess([], block_terminate=False)

    async def factory(*command):
        process.command = command
        await release.wait()
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        timeout_seconds=0.01,
        process_factory=factory,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.sleep(0.03)
    assert not task.done()
    release.set()
    with pytest.raises(CrispASRError) as error:
        await task
    assert error.value.code == "ASR_TIMEOUT"
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_communicate_timeout_cancels_output_pump_before_temp_removal(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    process = ReapingProcess([], delay=10, block_terminate=False)

    async def factory(*command):
        process.command = command
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        timeout_seconds=0.02,
        process_factory=factory,
        terminate_seconds=0.01,
    )
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_TIMEOUT"
    assert process.communicate_cancelled
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_provider_rejects_model_and_executable_symlinks(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    outside = tmp_path / "outside.gguf"
    outside.write_bytes(b"outside")
    (models / "link.gguf").symlink_to(outside)
    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="link.gguf"),
        process_factory=lambda *command: None,
    )
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_MODEL_NOT_FOUND"

    linked_binary = tmp_path / "linked-crispasr"
    linked_binary.symlink_to(binary)
    provider = CrispASRProvider(linked_binary, models, ASRConfig(provider="crispasr"))
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_EXECUTABLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_repeated_task_cancel_still_reaps_process_and_waits(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    entered = asyncio.Event()
    process = ReapingProcess([], delay=10, block_terminate=True)

    async def factory(*command):
        process.command = command
        entered.set()
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        timeout_seconds=10,
        process_factory=factory,
        terminate_seconds=0.01,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(process.wait_started.wait(), timeout=1)
    # wait_started means cleanup is active; cancelling twice more must not
    # interrupt the cleanup that escalates to kill and reap.
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert process.terminated and process.killed
    assert process.wait_calls >= 1


@pytest.mark.asyncio
async def test_pre_set_cancel_event_does_not_spawn_process(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    cancel = asyncio.Event()
    cancel.set()
    called = False

    async def factory(*_command):
        nonlocal called
        called = True
        return ReapingProcess([])

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        cancel_event=cancel,
        process_factory=factory,
    )
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_CANCELLED"
    assert not called


@pytest.mark.asyncio
async def test_success_cancels_event_waiter_and_reaps_without_terminating(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    cancel = asyncio.Event()
    process = ReapingProcess([], delay=0)

    async def factory(*command):
        process.command = command
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        cancel_event=cancel,
        process_factory=factory,
    )
    before = {task for task in asyncio.all_tasks()}
    result = await provider.transcribe(audio, project())
    assert result.segments[0].source_text == "hello"
    assert process.wait_calls >= 1
    assert not process.terminated
    assert not [task for task in asyncio.all_tasks() - before if not task.done()]


class DecodeCancelProcess(ReapingProcess):
    def __init__(self, command):
        super().__init__(command, delay=0)
        self.decode_started = asyncio.Event()
        self.decode_cancelled = False

    async def communicate(self):
        self.decode_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.decode_cancelled = True
            raise


@pytest.mark.asyncio
async def test_direct_cancel_during_communicate_reaps_and_cancels_output_task(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    process = DecodeCancelProcess([])

    async def factory(*command):
        process.command = command
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        process_factory=factory,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    await asyncio.wait_for(process.decode_started.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(process.wait_started.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.decode_cancelled
    assert process.terminated and process.wait_calls >= 1


@pytest.mark.asyncio
async def test_spawn_oserror_mapping_is_not_overwritten_by_cleanup(tmp_path):
    binary, models, audio = setup_files(tmp_path)
    cancel = asyncio.Event()

    async def factory(*_command):
        raise FileNotFoundError("missing binary")

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        cancel_event=cancel,
        process_factory=factory,
    )
    before = {task for task in asyncio.all_tasks()}
    with pytest.raises(CrispASRError) as error:
        await provider.transcribe(audio, project())
    assert error.value.code == "ASR_EXECUTABLE_NOT_FOUND"
    assert not [task for task in asyncio.all_tasks() - before if not task.done()]


@pytest.mark.asyncio
async def test_cancel_during_success_reap_finishes_cleanup_then_propagates(tmp_path):
    binary, models, audio = setup_files(tmp_path)

    class SlowReapProcess(ReapingProcess):
        def __init__(self, command):
            super().__init__(command, delay=0)
            self.release_wait.clear()
            self.process_exited = False

        async def communicate(self):
            result = await super().communicate()
            self.process_exited = True
            self.release_wait.clear()
            return result

        async def wait(self):
            self.wait_calls += 1
            self.wait_started.set()
            if self.process_exited:
                self.release_wait.clear()
            await self.release_wait.wait()
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    process = SlowReapProcess([])

    async def factory(*command):
        process.command = command
        return process

    provider = CrispASRProvider(
        binary,
        models,
        ASRConfig(provider="crispasr", model="model.gguf"),
        process_factory=factory,
    )
    task = asyncio.create_task(provider.transcribe(audio, project()))
    while not process.process_exited:
        await asyncio.sleep(0)
    await asyncio.wait_for(process.wait_started.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0.02)
    assert not task.done()
    process.release_wait.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert process.wait_calls >= 1
    assert not process.terminated
