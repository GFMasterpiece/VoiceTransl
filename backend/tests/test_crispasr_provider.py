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
