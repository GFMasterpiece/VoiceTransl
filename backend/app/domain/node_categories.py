"""Shared display categories; membership is declared by each built-in node."""

from .node_contracts import NodeCategory

INPUT = NodeCategory("input", "输入")
PREPROCESSING = NodeCategory("preprocessing", "预处理")
SPEECH_RECOGNITION = NodeCategory("speech_recognition", "语音识别")
TRANSLATION = NodeCategory("translation", "翻译")
PREVIEW = NodeCategory("preview", "预览")
OUTPUT = NodeCategory("output", "输出")
PARAMETERS = NodeCategory("parameters", "参数")
