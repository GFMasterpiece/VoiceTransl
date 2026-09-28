from pathlib import Path

from app.config import load_settings


def test_environment_overrides_yaml_settings(tmp_path, monkeypatch):
    config = tmp_path / "app.yaml"
    config.write_text(
        """
app:
  data_dir: ./yaml-data
  host: 127.0.0.1
  port: 8000
  max_upload_mb: 10
jobs:
  max_concurrent: 1
mock:
  delay_ms: 500
logging:
  level: INFO
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOICETRANSL_DATA_DIR", str(tmp_path / "env-data"))
    monkeypatch.setenv("VOICETRANSL_HOST", "0.0.0.0")
    monkeypatch.setenv("VOICETRANSL_PORT", "9000")
    monkeypatch.setenv("VOICETRANSL_MAX_UPLOAD_MB", "20")
    monkeypatch.setenv("VOICETRANSL_MAX_CONCURRENT", "3")
    monkeypatch.setenv("VOICETRANSL_MOCK_DELAY_MS", "25")
    monkeypatch.setenv("VOICETRANSL_LOG_LEVEL", "DEBUG")

    settings = load_settings(config)

    assert settings.data_dir == Path(tmp_path / "env-data")
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.max_upload_mb == 20
    assert settings.max_concurrent == 3
    assert settings.mock_delay_ms == 25
    assert settings.log_level == "DEBUG"
