import asyncio
from pathlib import Path

import pytest
from app.application.event_bus import EventBus
from app.config import load_preset, load_settings
from app.domain import Job, Project, SourceAsset
from app.logging_utils import configure_logging
from app.pipeline.context import PipelineContext
from app.pipeline.runner import PipelineRunner
from app.storage import FileSystemProjectStore


def test_mock_delay_precedence_defaults_preset_yaml_env(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    preset_dir = config_dir / "presets"
    preset_dir.mkdir()
    (preset_dir / "mock.yaml").write_text(
        "name: mock\nstages: [preprocess, asr, alignment, translation, export]\n"
        "providers: {asr: mock, aligner: mock, translator: mock}\nmock: {delay_ms: 21}\n",
        encoding="utf-8",
    )
    config = config_dir / "app.yaml"
    config.write_text(
        "app:\n  data_dir: ./data\nmock:\n  delay_ms: 31\nlogging:\n  level: INFO\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOICETRANSL_MOCK_DELAY_MS", "41")
    settings = load_settings(config)
    assert settings.mock_delay_ms == 41
    assert settings.mock_delay_ms_explicit is True
    assert load_preset(settings.preset_dir, "mock").mock["delay_ms"] == 21

    monkeypatch.delenv("VOICETRANSL_MOCK_DELAY_MS")
    settings = load_settings(config)
    assert settings.mock_delay_ms == 31
    assert settings.mock_delay_ms_explicit is True

    config.write_text(
        "app:\n  data_dir: ./data\nlogging:\n  level: INFO\n",
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.mock_delay_ms == 500
    assert settings.mock_delay_ms_explicit is False


def test_log_level_is_validated_and_handlers_are_idempotent(tmp_path: Path):
    logger = configure_logging("warning", tmp_path)
    assert logger.level > 0
    first_count = len(logger.handlers)
    configure_logging("WARNING", tmp_path)
    assert len(logger.handlers) == first_count


@pytest.mark.asyncio
async def test_stage_error_is_safe_for_state_and_events_and_traceback_is_logged(tmp_path: Path):
    store = FileSystemProjectStore(tmp_path)
    project = Project(
        id="prj_test",
        name="test",
        source=SourceAsset(path="source/original.wav", original_name="x.wav", size_bytes=0),
    )
    project_dir = store.create_project(project)
    job = Job(id="job_test", project_id=project.id)

    class FailingStage:
        name = next(iter(job.stages))

        async def run(self, _context):
            raise RuntimeError("Authorization: Bearer super-secret api_key=private-value")

    events = EventBus()
    queue = events.subscribe(project.id)
    configure_logging("INFO", tmp_path)
    context = PipelineContext(
        project=project,
        project_dir=project_dir,
        job=job,
        preset={},
        store=store,
        cancel_event=asyncio.Event(),
    )
    await PipelineRunner([FailingStage()], events).run(context, resume=False)

    saved = store.load_job(project.id)
    assert saved.error is not None
    assert saved.error.type == "STAGE_FAILED"
    assert saved.error.message == "阶段处理失败"
    assert saved.stages[FailingStage.name].error == "STAGE_FAILED"
    event = await queue.get()
    while event["type"] != "job.failed":
        event = await queue.get()
    assert event["data"]["message"] == "阶段处理失败"
    assert "super-secret" not in event["data"]["message"]
    log_text = (tmp_path / "voicetransl.log").read_text(encoding="utf-8")
    assert "stage failed" in log_text
    assert "super-secret" not in log_text
    assert "private-value" not in log_text
    project_log = (project_dir / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "stage failed" in project_log
    assert "super-secret" not in project_log
