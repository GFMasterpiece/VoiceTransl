"""Mock audio preprocessing node."""

from ..domain.node_categories import PREPROCESSING
from ..domain.node_config import MockPreprocessNodeConfig
from ..domain.node_constants import AUDIO_PORT, PREPROCESS_NODE_TYPE
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeResult


def normalize(config):
    return MockPreprocessNodeConfig.model_validate(config, strict=True).model_dump(mode="json")


async def execute(context: NodeExecutionContext) -> NodeResult:
    from ..application.workflow_media import AudioData, write_audio_artifact

    upstream = context.input(AUDIO_PORT).value
    artifact = await write_audio_artifact(
        context.store,
        context.run,
        context.node.id,
        upstream,
        context.cancel_event,
        f"preprocessed{upstream.suffix}",
    )
    output = AudioData(
        path=context.store.workflow_artifact_path(
            context.run.project_id,
            context.run.audio_id,
            context.run.id,
            context.node.id,
            artifact.filename,
        ),
        suffix=upstream.suffix,
        media_type=upstream.media_type,
        size_bytes=artifact.size_bytes,
    )
    result = NodeResult(artifact=artifact)
    result.add(AUDIO_PORT, output)
    return result


def definition() -> NodeDefinition:
    return NodeDefinition(
        type=PREPROCESS_NODE_TYPE,
        label="预处理",
        category=PREPROCESSING,
        input_ports=(AUDIO_PORT,),
        output_ports=(AUDIO_PORT,),
        terminal=False,
        config_kind="preprocess",
        fields=fields_from_model(
            MockPreprocessNodeConfig,
            widgets={"provider": ("select", [{"value": "mock", "label": "Mock"}])},
        ),
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
    )
