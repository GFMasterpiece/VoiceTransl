from pathlib import Path
import os

import yaml
from pydantic import BaseModel, Field

class AppSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./data")
    max_upload_mb: int = 4096
    allowed_extensions: list[str] = Field(default_factory=lambda: ["wav","flac","mp3","m4a","aac","ogg","opus"])
    max_concurrent: int = 1
    mock_delay_ms: int = 500
    log_level: str = "INFO"

def load_settings(config_path: str | Path | None = None) -> AppSettings:
    raw: dict = {}
    if config_path and Path(config_path).exists():
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    app = raw.get("app", {})
    jobs = raw.get("jobs", {})
    mock = raw.get("mock", {})
    logging = raw.get("logging", {})
    defaults = AppSettings()
    values = {
        "host": app.get("host", defaults.host),
        "port": app.get("port", defaults.port),
        "data_dir": app.get("data_dir", os.getenv("VOICETRANSL_DATA_DIR", defaults.data_dir)),
        "max_upload_mb": app.get("max_upload_mb", defaults.max_upload_mb),
        "allowed_extensions": app.get("allowed_extensions", defaults.allowed_extensions),
        "max_concurrent": jobs.get("max_concurrent", defaults.max_concurrent),
        "mock_delay_ms": mock.get("delay_ms", defaults.mock_delay_ms),
        "log_level": logging.get("level", defaults.log_level),
    }
    # Environment variables are useful for container and test deployments and
    # intentionally cover only operational settings, never secrets.
    # Environment variables are the final layer, including when a YAML file
    # supplies the same setting.
    env_overrides = {
        "data_dir": ("VOICETRANSL_DATA_DIR", str),
        "host": ("VOICETRANSL_HOST", str),
        "port": ("VOICETRANSL_PORT", int),
        "max_upload_mb": ("VOICETRANSL_MAX_UPLOAD_MB", int),
        "max_concurrent": ("VOICETRANSL_MAX_CONCURRENT", int),
        "mock_delay_ms": ("VOICETRANSL_MOCK_DELAY_MS", int),
        "log_level": ("VOICETRANSL_LOG_LEVEL", str),
    }
    for field, (variable, converter) in env_overrides.items():
        if os.getenv(variable) is not None:
            values[field] = converter(os.environ[variable])
    if os.getenv("VOICETRANSL_ALLOWED_EXTENSIONS") is not None:
        values["allowed_extensions"] = [
            extension.strip().lower().lstrip(".")
            for extension in os.environ["VOICETRANSL_ALLOWED_EXTENSIONS"].split(",")
            if extension.strip()
        ]
    return AppSettings(**values)
