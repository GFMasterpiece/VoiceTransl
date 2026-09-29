from pathlib import Path
from types import SimpleNamespace

import pytest
from app.domain import PreprocessConfig
from app.main import create_app
from app.providers.ffmpeg import FFmpegPreprocessProvider, PreprocessError
from httpx import ASGITransport, AsyncClient


def _config(**overrides):
    return PreprocessConfig(**overrides)


@pytest.mark.asyncio
async def test_ffmpeg_provider_builds_normalization_command_and_preserves_source(tmp_path):
    source = tmp_path / "original.mp3"
    output = tmp_path / "cache" / "preprocessed.wav"
    source.write_bytes(b"source")
    commands = []

    def runner(command):
        commands.append(command)
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b"wav")
        return SimpleNamespace(returncode=0)

    result = await FFmpegPreprocessProvider("custom-ffmpeg", runner).preprocess(
        source, output, _config(sample_rate=48_000, channels=2, preserve_original=True)
    )

    assert commands == [
        [
            "custom-ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ac",
            "2",
            "-ar",
            "48000",
            "-vn",
            str(output),
        ]
    ]
    assert source.read_bytes() == b"source"
    assert result["provider"] == "ffmpeg"
    assert result["preserve_original"] is True


@pytest.mark.asyncio
async def test_ffmpeg_provider_never_overwrites_source_when_preserve_original_disabled(tmp_path):
    source = tmp_path / "original.wav"
    output = tmp_path / "cache" / "preprocessed.wav"
    source.write_bytes(b"source")

    def runner(_command):
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b"wav")
        return SimpleNamespace(returncode=0)

    result = await FFmpegPreprocessProvider(runner=runner).preprocess(
        source, output, _config(preserve_original=False)
    )

    assert source.read_bytes() == b"source"
    assert output.read_bytes() == b"wav"
    assert result["preserve_original"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runner", "code"),
    [
        (
            lambda _command: (_ for _ in ()).throw(FileNotFoundError()),
            "PREPROCESS_FFMPEG_NOT_FOUND",
        ),
        (lambda _command: SimpleNamespace(returncode=1), "PREPROCESS_FFMPEG_FAILED"),
    ],
)
async def test_ffmpeg_provider_reports_stable_runner_errors(tmp_path, runner, code):
    source = tmp_path / "original.wav"
    source.write_bytes(b"source")
    with pytest.raises(PreprocessError) as raised:
        await FFmpegPreprocessProvider(runner=runner).preprocess(
            source, tmp_path / "cache" / "preprocessed.wav", _config()
        )
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_ffmpeg_provider_reports_missing_output(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"source")
    with pytest.raises(PreprocessError, match="未生成") as raised:
        await FFmpegPreprocessProvider(
            runner=lambda _command: SimpleNamespace(returncode=0)
        ).preprocess(source, tmp_path / "cache" / "preprocessed.wav", _config())
    assert raised.value.code == "PREPROCESS_OUTPUT_MISSING"


@pytest.mark.asyncio
async def test_ffmpeg_provider_rejects_missing_input(tmp_path):
    with pytest.raises(PreprocessError) as raised:
        await FFmpegPreprocessProvider(runner=lambda _command: None).preprocess(
            tmp_path / "missing.wav", tmp_path / "out.wav", _config()
        )
    assert raised.value.code == "PREPROCESS_INPUT_MISSING"


@pytest.mark.asyncio
async def test_api_accepts_ffmpeg_preprocess_configuration(tmp_path):
    app = create_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        assert created.status_code == 201
        project_id = created.json()["project"]["id"]
        config = (await client.get(f"/api/v1/projects/{project_id}/config")).json()
        config["preprocess"].update({"provider": "ffmpeg", "channels": 2})
        response = await client.put(f"/api/v1/projects/{project_id}/config", json=config)
        assert response.status_code == 200
        assert response.json()["preprocess"]["provider"] == "ffmpeg"
        assert response.json()["preprocess"]["channels"] == 2


@pytest.mark.asyncio
async def test_ffmpeg_configuration_runs_provider_and_persists_metadata(tmp_path):
    app = create_app(tmp_path)

    def runner(command):
        output = Path(command[-1])
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b"normalized wav")
        return SimpleNamespace(returncode=0)

    app.state.jobs.preprocess_runner = runner
    app.state.jobs.delay_ms = 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/projects", files={"file": ("sample.wav", b"RIFF", "audio/wav")}
        )
        project_id = created.json()["project"]["id"]
        config = (await client.get(f"/api/v1/projects/{project_id}/config")).json()
        config["preprocess"]["provider"] = "ffmpeg"
        assert (
            await client.put(f"/api/v1/projects/{project_id}/config", json=config)
        ).status_code == 200
        assert (
            await client.post(f"/api/v1/projects/{project_id}/run", json={"resume": True})
        ).status_code == 202
        await app.state.jobs.tasks[project_id]

    project_dir = app.state.store.project_dir(project_id)
    metadata = (project_dir / "cache" / "preprocess.json").read_text(encoding="utf-8")
    assert '"provider": "ffmpeg"' in metadata
    assert (project_dir / "cache" / "preprocessed.wav").read_bytes() == b"normalized wav"
    assert (project_dir / "source" / "original.wav").read_bytes() == b"RIFF"
