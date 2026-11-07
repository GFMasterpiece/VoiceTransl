"""Stable built-in identifiers and configuration defaults."""

AUDIO_PORT = "audio"
TRANSCRIPT_PORT = "transcript"
TEXT_PORT = "text"
PORT_NAME = AUDIO_PORT
TEXT_FORMATS = frozenset({"txt", "srt", "vtt", "lrc"})
INPUT_NODE_TYPE = "audio_input"
AUDIO_INPUT_ID = "input"
PREPROCESS_NODE_TYPE = "preprocess"
ASR_NODE_TYPE = "asr"
TEXT_OUTPUT_NODE_TYPE = "text_output"
AUDIO_OUTPUT_NODE_TYPE = "audio_output"
DEFAULT_FILENAME_STEM = "audio-copy"
DEFAULT_TEXT_FILENAME_STEM = "transcript"
DEFAULT_TEXT_FORMAT = "txt"
NODE_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
PORT_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
BINDING_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
