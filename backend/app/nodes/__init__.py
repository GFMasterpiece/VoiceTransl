"""Built-in node composition. Modules own contracts and execution entry points."""

from . import (
    asr,
    audio_input,
    audio_output,
    preprocess,
    preview,
    qwen_asr,
    source_info,
    text_output,
    values,
)


def builtin_definitions():
    return (
        audio_input.definition(),
        preprocess.definition(),
        asr.definition(),
        qwen_asr.definition(),
        text_output.definition(),
        audio_output.definition(),
        source_info.definition(),
        *preview.definitions(),
        *values.definitions(),
    )
