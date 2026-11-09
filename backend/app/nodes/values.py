"""Typed literal parameter roots; no special handling in the scheduler."""

from pydantic import BaseModel, ConfigDict, Field

from ..domain.node_categories import PARAMETERS
from ..domain.node_contracts import NodeDefinition, fields_from_model
from ..domain.node_execution import NodeExecutionContext, NodeResult


class _ValueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class StringValueConfig(_ValueConfig):
    value: str = Field(default="", json_schema_extra={"widget": "multiline"})


class IntegerValueConfig(_ValueConfig):
    value: int = 0


class NumberValueConfig(_ValueConfig):
    value: float = 0.0


class BooleanValueConfig(_ValueConfig):
    value: bool = False


def _definition(type_id: str, label: str, config_model: type[BaseModel]) -> NodeDefinition:
    def normalize(config):
        return config_model.model_validate(config, strict=True).model_dump(mode="json")

    async def execute(context: NodeExecutionContext) -> NodeResult:
        result = NodeResult()
        result.add(type_id, normalize(context.node.config)["value"])
        return result

    return NodeDefinition(
        type=f"{type_id}_value",
        label=label,
        category=PARAMETERS,
        input_ports=(),
        output_ports=(type_id,),
        terminal=False,
        config_kind="object",
        fields=fields_from_model(config_model),
        required_input_ports=(),
        is_parameter_source=True,
        normalize_config=normalize,
        validate_config=normalize,
        execute=execute,
        evaluation_kind="configuration",
        evaluate_config=execute,
        can_start_without_inputs=True,
    )


def definitions():
    return (
        _definition("string", "字符串", StringValueConfig),
        _definition("integer", "整数", IntegerValueConfig),
        _definition("number", "数字", NumberValueConfig),
        _definition("boolean", "布尔值", BooleanValueConfig),
    )
