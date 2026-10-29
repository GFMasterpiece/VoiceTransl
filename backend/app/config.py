import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class AppSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("./data")
    max_upload_mb: int = Field(default=4096, ge=1)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: ["wav", "flac", "mp3", "m4a", "aac", "ogg", "opus"]
    )
    max_concurrent: int = Field(default=1, ge=1)
    mock_delay_ms: int = Field(default=500, ge=0)
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = str(value).upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    raw: dict = {}
    config_file = Path(config_path).resolve() if config_path else None
    if config_file:
        if not config_file.is_file():
            raise FileNotFoundError(f"configuration file does not exist: {config_file}")
        try:
            raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML configuration: {config_file}") from exc
        if not isinstance(raw, dict):
            raise ValueError("configuration root must be a mapping")
    app = raw.get("app", {})
    jobs = raw.get("jobs", {})
    mock = raw.get("mock", {})
    logging = raw.get("logging", {})
    defaults = AppSettings()
    config_root = config_file.parent.parent if config_file else Path.cwd()
    yaml_data_dir = app.get("data_dir", defaults.data_dir)
    if isinstance(yaml_data_dir, str) and not Path(yaml_data_dir).is_absolute() and config_file:
        yaml_data_dir = config_root / yaml_data_dir
    yaml_mock_delay = mock.get("delay_ms", app.get("mock_delay_ms", defaults.mock_delay_ms))
    values = {
        "host": app.get("host", defaults.host),
        "port": app.get("port", defaults.port),
        "data_dir": yaml_data_dir,
        "max_upload_mb": app.get("max_upload_mb", defaults.max_upload_mb),
        "allowed_extensions": app.get("allowed_extensions", defaults.allowed_extensions),
        "max_concurrent": jobs.get("max_concurrent", defaults.max_concurrent),
        "mock_delay_ms": yaml_mock_delay,
        "log_level": logging.get("level", defaults.log_level),
    }
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
