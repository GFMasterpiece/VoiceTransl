import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

EXPECTED_STAGES = ["preprocess", "asr", "alignment", "translation", "export"]


class PresetConfig(BaseModel):
    name: str
    stages: list[str]
    providers: dict[str, str]
    mock: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def validate_preset(cls, value: "PresetConfig", expected_name: str) -> "PresetConfig":
        if value.name != expected_name:
            raise ValueError(f"preset name does not match {expected_name!r}")
        if value.stages != EXPECTED_STAGES:
            raise ValueError("preset stages must contain the five v0.1 stages in order")
        if value.providers != {"asr": "mock", "aligner": "mock", "translator": "mock"}:
            raise ValueError("v0.1 preset providers must use mock providers")
        if value.mock.get("delay_ms", 0) < 0:
            raise ValueError("preset mock.delay_ms must be non-negative")
        return value


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
    # Used internally to distinguish the code default from an explicit app
    # setting.  This lets a preset provide the delay when app.yaml is silent,
    # while an explicit YAML or environment value still wins over the preset.
    mock_delay_ms_explicit: bool = False
    log_level: str = "INFO"
    preset_dir: Path = Path("./config/presets")
    ffmpeg_path: str = "ffmpeg"
    crispasr_executable: Path = Path(
        "./crispasr/linux-v0.8.38/crispasr-linux-x86_64-cuda13/crispasr"
    )
    crispasr_model_dir: Path = Path("./crispasr/models")
    crispasr_timeout_seconds: int = Field(default=300, ge=1, le=86_400)

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
    preset_dir = app.get(
        "preset_dir", config_file.parent / "presets" if config_file else Path("./config/presets")
    )
    crispasr_executable = app.get("crispasr_executable", defaults.crispasr_executable)
    crispasr_model_dir = app.get("crispasr_model_dir", defaults.crispasr_model_dir)
    for key, value in (
        ("crispasr_executable", crispasr_executable),
        ("crispasr_model_dir", crispasr_model_dir),
    ):
        if not Path(value).is_absolute() and config_file:
            if key == "crispasr_executable":
                crispasr_executable = config_root / value
            else:
                crispasr_model_dir = config_root / value
    if isinstance(preset_dir, str) and not Path(preset_dir).is_absolute() and config_file:
        preset_dir = config_root / preset_dir
    # Accept the documented top-level ``mock.delay_ms`` and the equivalent
    # app setting form used by deployments that keep operational knobs under
    # ``app``.
    yaml_mock_delay = mock.get("delay_ms", app.get("mock_delay_ms", defaults.mock_delay_ms))
    mock_delay_explicit = "delay_ms" in mock or "mock_delay_ms" in app
    values = {
        "host": app.get("host", defaults.host),
        "port": app.get("port", defaults.port),
        "data_dir": yaml_data_dir,
        "max_upload_mb": app.get("max_upload_mb", defaults.max_upload_mb),
        "allowed_extensions": app.get("allowed_extensions", defaults.allowed_extensions),
        "max_concurrent": jobs.get("max_concurrent", defaults.max_concurrent),
        "mock_delay_ms": yaml_mock_delay,
        "mock_delay_ms_explicit": mock_delay_explicit,
        "log_level": logging.get("level", defaults.log_level),
        "preset_dir": preset_dir,
        "ffmpeg_path": app.get("ffmpeg_path", defaults.ffmpeg_path),
        "crispasr_executable": crispasr_executable,
        "crispasr_model_dir": crispasr_model_dir,
        "crispasr_timeout_seconds": app.get(
            "crispasr_timeout_seconds", defaults.crispasr_timeout_seconds
        ),
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
        "preset_dir": ("VOICETRANSL_PRESET_DIR", str),
        "ffmpeg_path": ("VOICETRANSL_FFMPEG_PATH", str),
        "crispasr_executable": ("VOICETRANSL_CRISPASR_EXECUTABLE", str),
        "crispasr_model_dir": ("VOICETRANSL_CRISPASR_MODEL_DIR", str),
        "crispasr_timeout_seconds": ("VOICETRANSL_CRISPASR_TIMEOUT_SECONDS", int),
    }
    for field, (variable, converter) in env_overrides.items():
        if os.getenv(variable) is not None:
            values[field] = converter(os.environ[variable])
            if field == "mock_delay_ms":
                values["mock_delay_ms_explicit"] = True
    if os.getenv("VOICETRANSL_ALLOWED_EXTENSIONS") is not None:
        values["allowed_extensions"] = [
            extension.strip().lower().lstrip(".")
            for extension in os.environ["VOICETRANSL_ALLOWED_EXTENSIONS"].split(",")
            if extension.strip()
        ]
    return AppSettings(**values)


def load_preset(preset_dir: str | Path, name: str) -> PresetConfig:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("invalid preset name")
    path = Path(preset_dir).resolve() / f"{name}.yaml"
    root = Path(preset_dir).resolve()
    if path.parent != root or not path.is_file():
        raise FileNotFoundError(f"preset does not exist: {name}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid preset YAML: {name}") from exc
    if not isinstance(raw, dict):
        raise ValueError("preset root must be a mapping")
    return PresetConfig.validate_preset(PresetConfig.model_validate(raw), name)
