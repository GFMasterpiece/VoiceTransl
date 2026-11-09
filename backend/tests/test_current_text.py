import pytest
from app.application.workflow_asr import _render_text
from app.domain.current_text import read_current_text, with_current_text
from app.domain.node_execution import NodeExecutionError
from app.domain.transcript import Segment, Transcript


def transcript():
    return Transcript(
        project_id="project",
        stage="raw",
        language="ja",
        segments=[
            Segment(
                id="s",
                start=1,
                end=2,
                source_text="original",
                normalized_text="normalized",
                translated_text="translation",
                language="ja",
                speaker="speaker",
                metadata={"words": [{"text": "original", "start": 1}]},
            ),
        ],
    )


def test_legacy_transcripts_keep_source_semantics_and_new_views_are_explicit():
    original = transcript()
    before = original.model_dump_json()
    assert read_current_text(original) == ([{"id": "s", "text": "original"}], "ja")
    assert _render_text(original, "txt") == "original\n"
    for field, text, language in (
        ("normalized_text", "normalized", "ja"),
        ("translated_text", "translation", "x-custom"),
        ("source_text", "original", "ja"),
    ):
        viewed = with_current_text(original, field, language)
        assert read_current_text(viewed) == ([{"id": "s", "text": text}], language)
        assert _render_text(viewed, "txt") == text + "\n"
        for format_name in ("srt", "vtt", "lrc"):
            assert text in _render_text(viewed, format_name)
        assert viewed.segments == original.segments
    assert original.model_dump_json() == before


@pytest.mark.parametrize(
    "marker",
    [
        None,
        {},
        {"version": "2", "field": "source_text", "language": "ja"},
        {"version": "1", "field": [], "language": "ja"},
        {"version": "1", "field": "unknown", "language": "ja"},
        {"version": "1", "field": "source_text", "language": ""},
    ],
)
def test_invalid_current_view_never_falls_back_to_source(marker):
    value = transcript()
    value.metadata["current_text"] = marker
    with pytest.raises(NodeExecutionError) as error:
        _render_text(value, "txt")
    assert error.value.code == "CURRENT_TEXT_INVALID"


@pytest.mark.parametrize("missing", [None, "", "  "])
def test_selected_translation_must_be_complete(missing):
    value = transcript()
    value.segments[0].translated_text = missing
    with pytest.raises(NodeExecutionError) as error:
        with_current_text(value, "translated_text", "en")
    assert error.value.code == "CURRENT_TEXT_MISSING"


def test_current_view_preserves_empty_source_and_timestamp_rules():
    value = transcript()
    value.segments[0].start = value.segments[0].end = None
    viewed = with_current_text(value, "translated_text", "en")
    assert _render_text(viewed, "txt") == "translation\n"
    with pytest.raises(ValueError):
        _render_text(viewed, "srt")
    value.segments[0].source_text = ""
    with pytest.raises(ValueError):
        with_current_text(value, "translated_text", "en")
