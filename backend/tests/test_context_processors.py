"""Processors consume typed configuration while preserving current-text semantics."""

import asyncio
from types import SimpleNamespace

import pytest
from app.application.workflow_asr import TranscriptData
from app.domain.context_values import ASROptions, TranslationOptions
from app.domain.node_execution import NodeData, NodeExecutionContext, NodeExecutionError
from app.domain.node_registry import NODE_DEFINITIONS
from app.domain.transcript import Segment, Transcript
from app.domain.workflow import WorkflowNode
from app.nodes import asr, llm_translate, qwen_asr, qwen_asr_streaming


def transcript(language="ja", text="原文"):
    return Transcript(
        project_id="p",
        stage="raw",
        language=language,
        segments=[Segment(id="s1", start=0.2, end=1.8, source_text=text, language=language)],
    )


def context(tmp_path, node_type, inputs, *, audio_id="a", node_id="processor"):
    def artifact_path(*args):
        directory = tmp_path / node_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / args[-1]

    project = SimpleNamespace(
        audios=[
            SimpleNamespace(id="a", source=SimpleNamespace(language="ja")),
            SimpleNamespace(id="b", source=SimpleNamespace(language="fr")),
        ]
    )
    return NodeExecutionContext(
        SimpleNamespace(workflow_artifact_path=artifact_path),
        SimpleNamespace(project_id="p", audio_id=audio_id, id="run"),
        WorkflowNode(id=node_id, type=node_type, config={}),
        {key: NodeData(key, value) for key, value in inputs.items()},
        project,
        asyncio.Event(),
    )


@pytest.mark.parametrize("node_type", ["asr", "qwen_asr", "qwen_asr_streaming"])
def test_processors_have_no_inline_configuration(node_type):
    definition = NODE_DEFINITIONS[node_type]
    assert definition.fields == ()
    assert definition.settings_default_fields == ()
    assert definition.normalize_config({}) == {}
    with pytest.raises(ValueError, match="独立"):
        definition.normalize_config({"language": "ja"})
    assert definition.validation_input_ports or node_type == "asr"


@pytest.mark.asyncio
async def test_two_translations_share_model_but_use_previous_translation_and_own_context(
    tmp_path, monkeypatch
):
    calls, resolutions = [], []
    model = object()

    async def resolve(ctx, value, operation, timeout):
        assert value is model
        resolutions.append((operation, timeout))
        return "connection", "model", {"seed": 1}, "private-key"

    async def translate(**kwargs):
        calls.append(kwargs)
        prefix = ("en" if kwargs["context"] == "first preferences" else "zh") + ":"
        return SimpleNamespace(
            translations={item["id"]: prefix + item["text"] for item in kwargs["items"]},
            language=prefix[:-1],
        )

    monkeypatch.setattr("app.domain.configuration.resolve_model", resolve)
    monkeypatch.setattr("app.adapters.llm_translation.translate", translate)
    original = transcript()
    first_context = "first preferences"
    first = await llm_translate.execute(
        context(
            tmp_path,
            "llm_translate",
            {
                "llm_model": model,
                "transcript": TranscriptData(tmp_path / "raw.json", original),
                "context": first_context,
                "translation_options": TranslationOptions(timeout_seconds=33, batch_size=4),
            },
            node_id="first",
        )
    )
    second = await llm_translate.execute(
        context(
            tmp_path,
            "llm_translate",
            {
                "llm_model": model,
                "transcript": first.outputs["transcript"].value,
                "context": "second preferences",
            },
            node_id="second",
        )
    )
    assert calls[0]["source_language"] == "ja"
    assert calls[0]["context"] == first_context
    assert calls[0]["batch_size"] == 4
    assert calls[1]["items"] == [{"id": "s1", "text": "en:原文"}]
    assert calls[1]["source_language"] == "en"
    assert calls[1]["context"] == "second preferences"
    assert resolutions == [("translation", 33), ("translation", 120)]
    result = second.outputs["transcript"].value.transcript
    assert result.segments[0].source_text == original.segments[0].source_text == "原文"
    assert result.segments[0].translated_text == "zh:en:原文"
    assert (result.segments[0].id, result.segments[0].start, result.segments[0].end) == (
        "s1",
        0.2,
        1.8,
    )
    assert original.segments[0].translated_text is None
    assert result.metadata["current_text"]["language"] == "zh"


@pytest.mark.parametrize(
    "packet,error_code",
    [
        ("", "LLM_CONTEXT_INVALID"),
        (object(), "LLM_CONTEXT_INVALID"),
    ],
)
def test_preflight_rejects_context_without_actual_transcript_or_network(
    tmp_path, packet, error_code
):
    ctx = context(tmp_path, "llm_translate", {"context": packet})
    with pytest.raises(NodeExecutionError) as error:
        llm_translate.validate_inputs(ctx)
    assert error.value.code == error_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,operation", [(qwen_asr, "asr_file"), (qwen_asr_streaming, "asr_streaming")]
)
@pytest.mark.parametrize(
    "audio_id,override,expected",
    [("a", "", "ja"), ("b", "", "fr"), ("b", "recognition vocabulary", "fr")],
)
async def test_cloud_asr_reads_each_current_audio_language_and_independent_options(
    tmp_path, monkeypatch, module, operation, audio_id, override, expected
):
    calls = []

    async def resolve(ctx, value, op, timeout):
        assert (op, timeout) == (operation, 19)
        return "connection", "named-model", {"custom": True}, "private-key"

    async def transcribe(**kwargs):
        calls.append(kwargs)
        return transcript(kwargs["language"])

    monkeypatch.setattr("app.domain.configuration.resolve_model", resolve)
    if operation == "asr_file":
        monkeypatch.setattr("app.adapters.qwen_asr.transcribe", transcribe)
    else:

        async def streaming(self, **kwargs):
            return await transcribe(**kwargs)

        monkeypatch.setattr(
            "app.adapters.qwen_asr_streaming.QwenStreamingASRAdapter.transcribe", streaming
        )
    await module.execute(
        context(
            tmp_path,
            module.definition().type,
            {
                "audio": SimpleNamespace(path=tmp_path / "audio.wav"),
                "asr_model": object(),
                "asr_options": ASROptions(timeout_seconds=19),
            },
            audio_id=audio_id,
        )
    )
    assert calls[0]["language"] == expected
    assert calls[0]["model"] == "named-model"
    assert calls[0]["api_key"] == "private-key"
    assert calls[0]["additional_parameters"] == {"custom": True}


@pytest.mark.asyncio
async def test_mock_language_context_does_not_mutate_frozen_processor(tmp_path, monkeypatch):
    seen = []

    async def execute(store, run, node, path, project, event, delay):
        seen.append(node.config)
        return None, TranscriptData(tmp_path / "result.json", transcript(node.config["language"]))

    monkeypatch.setattr("app.application.workflow_asr.execute_asr_node", execute)
    ctx = context(
        tmp_path, "asr", {"audio": SimpleNamespace(path=tmp_path / "audio.wav")}, audio_id="b"
    )
    await asr.execute(ctx)
    assert seen == [{"provider": "mock", "language": "fr"}]
    assert ctx.node.config == {}
