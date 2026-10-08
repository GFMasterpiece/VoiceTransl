from .lrc import render_lrc
from .srt import render_srt
from .vtt import render_vtt


def output_names(config):
    names = set()
    for format_name in config.export.formats:
        names.add(f"zh.{format_name}")
        if config.export.bilingual and format_name in {"srt", "vtt"}:
            names.add(f"ja-zh.{format_name}")
    return names
