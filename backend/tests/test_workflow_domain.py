import pytest
from app.domain import (
    ASR_NODE_TYPE,
    AUDIO_OUTPUT_NODE_TYPE,
    INPUT_NODE_TYPE,
    PREPROCESS_NODE_TYPE,
    TEXT_OUTPUT_NODE_TYPE,
    Segment,
    TextOutputConfig,
    Transcript,
    WorkflowEdge,
    WorkflowNode,
    normalize_workflow_graph,
    validate_asr_config,
    validate_text_output_config,
    validate_workflow_graph,
    validate_workflow_transcript,
)


def wf_node(node_id, node_type, config=None):
    return WorkflowNode(id=node_id, type=node_type, config=config or {})


def wf_edge(edge_id, source, source_port, target, target_port):
    return WorkflowEdge(
        id=edge_id,
        source=source,
        source_port=source_port,
        target=target,
        target_port=target_port,
    )


def text_graph(*, asr_config=None, text_config=None):
    return (
        [
            wf_node("input", INPUT_NODE_TYPE),
            wf_node("pre", PREPROCESS_NODE_TYPE),
            wf_node("asr", ASR_NODE_TYPE, asr_config or {}),
            wf_node("text", TEXT_OUTPUT_NODE_TYPE, text_config or {}),
        ],
        [
            wf_edge("e1", "input", "audio", "pre", "audio"),
            wf_edge("e2", "pre", "audio", "asr", "audio"),
            wf_edge("e3", "asr", "transcript", "text", "transcript"),
        ],
    )


def test_workflow_asr_defaults_are_strict_and_same_as_explicit_values():
    default = validate_asr_config({})
    assert default.model_dump(mode="json") == {
        "provider": "mock",
        "provider_profile_id": None,
        "backend": "qwen3-1.7b",
        "model": "mock",
        "language": "ja",
        "vad_enabled": True,
        "chunk_seconds": 30,
    }
    explicit = validate_asr_config(default.model_dump(mode="json"))
    assert explicit == default
    nodes, edges = text_graph()
    normalized, _ = normalize_workflow_graph(nodes, edges)
    assert normalized[2].config == default.model_dump(mode="json")


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {"provider": "openai"},
        {"backend": "not-supported"},
        {"model": "../model.gguf"},
        {"model": "model.gguf."},
        {"model": " "},
        {"language": " "},
        {"language": " ja"},
        {"language": "ja\n"},
        {"provider_profile_id": "cloud"},
        {"provider_profile_id": ""},
        {"vad_enabled": 1},
        {"chunk_seconds": "30"},
        {"chunk_seconds": True},
        {"unknown": 1},
    ],
)
def test_workflow_asr_rejects_unknown_nulls_paths_and_coercion(config):
    with pytest.raises(ValueError):
        validate_asr_config(config)


def test_workflow_asr_accepts_mock_and_safe_crispasr_identifiers():
    mock = validate_asr_config({"provider": "mock", "model": "demo.gguf", "backend": "qwen3"})
    assert mock.provider == "mock"
    crisp = validate_asr_config(
        {
            "provider": "crispasr",
            "backend": "whisper",
            "model": "whisper-large-v3.gguf",
            "language": "en",
        }
    )
    assert crisp.provider == "crispasr"


@pytest.mark.parametrize(
    "config",
    [
        None,
        [],
        {"format": "ass"},
        {"filename_stem": "../escape"},
        {"filename_stem": "bad "},
        {"filename_stem": "."},
        {"filename_stem": "CON"},
        {"filename_stem": 1},
        {"format": 1},
        {"unknown": True},
    ],
)
def test_text_output_config_is_strict(config):
    with pytest.raises(ValueError):
        validate_text_output_config(config)


def test_text_output_defaults_and_safe_stem():
    assert validate_text_output_config({}) == TextOutputConfig()
    assert validate_text_output_config({"filename_stem": "日本 語_01", "format": "vtt"}) == (
        TextOutputConfig(filename_stem="日本 語_01", format="vtt")
    )


def test_graph_accepts_text_only_and_mixed_branches():
    nodes, edges = text_graph()
    assert validate_workflow_graph(nodes, edges) == []
    mixed_nodes = [
        *nodes,
        wf_node("audio", AUDIO_OUTPUT_NODE_TYPE, {"filename_stem": "original"}),
    ]
    mixed_edges = [*edges, wf_edge("e4", "input", "audio", "audio", "audio")]
    assert validate_workflow_graph(mixed_nodes, mixed_edges) == []


@pytest.mark.parametrize(
    "source_port,target_port",
    [("audio", "transcript"), ("transcript", "audio"), ("text", "text"), ("other", "other")],
)
def test_graph_rejects_type_mismatched_or_unknown_ports(source_port, target_port):
    nodes, edges = text_graph()
    edges[-1] = wf_edge("e3", "asr", source_port, "text", target_port)
    errors = validate_workflow_graph(nodes, edges)
    assert any(error.code == "INVALID_PORT" for error in errors)


def test_graph_requires_terminal_text_output_and_rejects_islands():
    nodes, edges = text_graph()
    no_terminal = [node for node in nodes if node.type != TEXT_OUTPUT_NODE_TYPE]
    no_terminal_edges = edges[:-1]
    errors = validate_workflow_graph(no_terminal, no_terminal_edges)
    assert any(error.code == "INVALID_OUTPUT_COUNT" for error in errors)
    island = [*nodes, wf_node("orphan", ASR_NODE_TYPE)]
    errors = validate_workflow_graph(island, edges)
    assert any(error.code == "UNCONNECTED_NODE" for error in errors)


def test_graph_rejects_asr_without_downstream_terminal():
    nodes = [
        wf_node("input", INPUT_NODE_TYPE),
        wf_node("asr", ASR_NODE_TYPE),
        wf_node("audio", AUDIO_OUTPUT_NODE_TYPE, {"filename_stem": "x"}),
    ]
    edges = [
        wf_edge("e1", "input", "audio", "asr", "audio"),
        wf_edge("e2", "input", "audio", "audio", "audio"),
    ]
    errors = validate_workflow_graph(nodes, edges)
    assert any(error.code == "NODE_HAS_NO_OUTPUT" and error.node_id == "asr" for error in errors)


def test_graph_enforces_64_node_and_edge_limits():
    nodes, edges = text_graph()
    repeated = [wf_node("input", INPUT_NODE_TYPE)]
    repeated.extend(wf_node(f"n{index}", ASR_NODE_TYPE) for index in range(64))
    errors = validate_workflow_graph(repeated, [])
    assert any(error.code == "TOO_MANY_NODES" for error in errors)
    too_many_edges = [wf_edge(f"e{i}", "input", "audio", "asr", "audio") for i in range(65)]
    errors = validate_workflow_graph(nodes, too_many_edges)
    assert any(error.code == "TOO_MANY_EDGES" for error in errors)


def test_workflow_transcript_result_contract():
    valid = Transcript(
        project_id="p",
        stage="raw",
        language="ja",
        duration=1,
        segments=[Segment(id="s1", start=0, end=1, source_text="hi")],
    )
    validate_workflow_transcript(valid)
    no_time = valid.model_copy(update={"segments": [Segment(id="s1", source_text="hi")]}, deep=True)
    validate_workflow_transcript(no_time)

    invalid_values = [
        valid.model_copy(update={"segments": []}, deep=True),
        valid.model_copy(
            update={"segments": [Segment(id="", start=0, end=1, source_text="hi")]}, deep=True
        ),
        valid.model_copy(
            update={"segments": [Segment(id="s1", start=0, end=1, source_text=" ")]}, deep=True
        ),
        valid.model_copy(update={"duration": float("nan")}, deep=True),
        valid.model_copy(update={"duration": -1}, deep=True),
    ]
    for value in invalid_values:
        with pytest.raises(ValueError):
            validate_workflow_transcript(value)

    with pytest.raises(ValueError):
        validate_workflow_transcript(
            valid.model_copy(
                update={"segments": [Segment(id="s1", start=0, source_text="hi")]}, deep=True
            )
        )
    with pytest.raises(ValueError):
        validate_workflow_transcript(
            valid.model_copy(
                update={"segments": [Segment(id="s1", start=0, end=-1, source_text="hi")]},
                deep=True,
            )
        )


@pytest.mark.parametrize(
    "field",
    ["provider", "backend", "model", "language", "vad_enabled", "chunk_seconds"],
)
def test_workflow_asr_rejects_explicit_null_fields(field):
    with pytest.raises(ValueError):
        validate_asr_config({field: None})


def test_workflow_transcript_rejects_duplicate_ids_nonfinite_and_null_ranges():
    segments = [
        Segment(id="same", start=0, end=1, source_text="a"),
        Segment(id="same", start=1, end=2, source_text="b"),
    ]
    value = Transcript(project_id="p", stage="raw", language="ja", segments=segments)
    with pytest.raises(ValueError, match="唯一"):
        validate_workflow_transcript(value)

    for start, end in ((float("inf"), float("inf")), (0, float("inf")), (0, None)):
        payload = Transcript(
            project_id="p",
            stage="raw",
            language="ja",
            segments=[Segment(id="s", start=start, end=end, source_text="a")],
        )
        with pytest.raises(ValueError):
            validate_workflow_transcript(payload)
