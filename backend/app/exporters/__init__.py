from __future__ import annotations

from dataclasses import dataclass

from ..domain.legacy_config import EXPORT_FORMATS, LegacyExportConfig
from .lrc import render_lrc
from .srt import render_srt
from .vtt import render_vtt

MAX_EXPORT_FILENAME_BYTES = 255
MAX_EXPORT_STEM_BYTES = 200
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class ExportFile:
    name: str
    format: str
    kind: str
    language: str
    bilingual: bool


def _truncate_utf8(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore").rstrip(" .")


def sanitize_export_stem(value: str | None) -> str:
    """Turn an uploaded filename into a safe, portable output stem."""
    if not isinstance(value, str):
        return "transcript"
    name = value.strip().replace(chr(92), "/").rsplit("/", 1)[-1]
    if not name:
        return "transcript"
    dot = name.rfind(".")
    stem = name[:dot] if dot > 0 else name
    if stem in {"", ".", ".."} or stem.startswith("."):
        stem = "transcript"
    stem = "".join(
        "_"
        if char in {"<", ">", ":", chr(34), "/", chr(92), "|", "?", "*"}
        or ord(char) < 32
        or ord(char) == 127
        else char
        for char in stem
    )
    stem = stem.strip(" .")
    if not stem or stem in {".", ".."}:
        stem = "transcript"
    if stem.split(".", 1)[0].upper() in _RESERVED:
        stem = f"audio-{stem}"
    stem = _truncate_utf8(stem, MAX_EXPORT_STEM_BYTES)
    return stem or "transcript"


def _legacy_name(config, format_name: str, bilingual: bool) -> str:
    target = "zh" if config.target_language.casefold().startswith("zh") else config.target_language
    if not bilingual:
        return f"{target}.{format_name}"
    return f"{config.source_language}-{target}.{format_name}"


def _template_name(config, format_name: str, kind: str, source_stem: str) -> str:
    if not config.export.filename_template:
        return ""
    values = {
        "source_language": config.source_language,
        "target_language": config.target_language,
        "kind": kind,
        "format": format_name,
        "filename_stem": source_stem,
    }
    stem = config.export.filename_template.format(**values)
    return stem if stem.endswith(f".{format_name}") else f"{stem}.{format_name}"


def export_files(config: LegacyExportConfig, *, source_stem: str | None = None) -> list[ExportFile]:
    """Describe every generated file; names never imply semantic fields."""
    source_stem = sanitize_export_stem(source_stem)
    files: list[ExportFile] = []
    for format_name in config.export.formats:
        kinds = [("translated", False)]
        if config.export.bilingual and format_name in {"srt", "vtt"}:
            kinds.append(("bilingual", True))
        for kind, bilingual in kinds:
            if config.export.filename_template:
                name = _template_name(config, format_name, kind, source_stem)
            elif config.export.naming == "language":
                name = _legacy_name(config, format_name, bilingual)
            else:
                stem = config.export.filename_stem or source_stem
                suffix = "-bilingual" if bilingual else ""
                name = f"{stem}{suffix}.{format_name}"
            files.append(
                ExportFile(
                    name=name,
                    format=format_name,
                    kind=kind,
                    language=config.target_language,
                    bilingual=bilingual,
                )
            )
    names = [item.name for item in files]
    if len(names) != len({name.casefold() for name in names}):
        raise ValueError("export filename collision")
    for item in files:
        validate_export_filename(item.name)
    return files


def validate_export_filename(name: str) -> None:
    if not isinstance(name, str):
        raise ValueError("invalid export filename")
    if (
        not name
        or name.casefold() == "manifest.json"
        or name in {".", ".."}
        or name != name.strip()
        or name.endswith(".")
        or "/" in name
        or chr(92) in name
        or any(char in {"<", ">", ":", chr(34), "|", "?", "*"} for char in name)
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
        or len(name.encode("utf-8")) > MAX_EXPORT_FILENAME_BYTES
    ):
        raise ValueError("invalid export filename")
    if name.split(".", 1)[0].upper() in _RESERVED:
        raise ValueError("export filename uses a reserved Windows name")


def output_names(config, *, source_stem: str | None = None) -> set[str]:
    return {item.name for item in export_files(config, source_stem=source_stem)}


def render_export(item: ExportFile, segments) -> str:
    if item.format == "srt":
        return render_srt(segments, bilingual=item.bilingual)
    if item.format == "vtt":
        return render_vtt(segments, bilingual=item.bilingual)
    return render_lrc(segments)
