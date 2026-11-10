"""Built-in node composition. Modules own contracts and execution entry points."""

from . import (
    asr,
    audio_input,
    audio_output,
    configuration_values,
    group_info,
    llm_translate,
    model_configuration,
    preprocess,
    preview,
    qwen_asr,
    qwen_asr_streaming,
    source_info,
    text_output,
    values,
)


def builtin_definitions():
    from .service_protocols import register_builtin_protocols

    register_builtin_protocols()
    return (
        audio_input.definition(),
        *configuration_values.builtin_definitions(),
        *model_configuration.definitions(),
        preprocess.definition(),
        asr.definition(),
        qwen_asr.definition(),
        qwen_asr_streaming.definition(),
        llm_translate.definition(),
        text_output.definition(),
        audio_output.definition(),
        source_info.definition(),
        group_info.definition(),
        *preview.definitions(),
        *values.definitions(),
    )
