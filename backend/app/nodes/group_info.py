"""Read group strings exclusively from the immutable run snapshot."""

from pydantic import BaseModel, ConfigDict, Field

from ..domain.node_categories import INPUT
from ..domain.node_contracts import CapabilityField, NodeDefinition
from ..domain.node_execution import NodeExecutionError, NodeResult


class GroupInfoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(default="", pattern=r"^(?:|grp_[0-9a-f]{32})$", strict=True)


def normalize(config):
    try:
        return GroupInfoConfig.model_validate(config).model_dump(mode="json")
    except ValueError:
        raise ValueError("请选择合法的音频组") from None


async def execute(context):
    from ..domain.audio_group import AudioGroup

    group_id = normalize(context.node.config)["group_id"]
    if not group_id:
        raise NodeExecutionError("GROUP_REQUIRED", "请为组资料节点选择音频组")
    raw = context.run.workflow.group_snapshots.get(group_id)
    if raw is None:
        raise NodeExecutionError("GROUP_SNAPSHOT_REQUIRED", "运行缺少冻结的音频组资料")
    try:
        group = AudioGroup.model_validate(raw)
        if group.id != group_id:
            raise ValueError
    except ValueError:
        raise NodeExecutionError("GROUP_SNAPSHOT_INVALID", "冻结音频组资料无效") from None
    result = NodeResult()
    for port in ("background", "name", "group_id", "mode"):
        result.add(port, group.id if port == "group_id" else getattr(group, port))
    return result


def definition():
    return NodeDefinition(
        type="group_info",
        label="组资料",
        category=INPUT,
        input_ports=(),
        output_ports=("background", "name", "group_id", "mode"),
        terminal=False,
        config_kind="group_info",
        fields=(CapabilityField(key="group_id", type="string", default=""),),
        required_input_ports=(),
        normalize_config=normalize,
        validate_config=normalize,
        presentation={"resource_input": {"resource_type": "audio_group", "config_key": "group_id"}},
        execute=execute,
        evaluate_config=execute,
        evaluation_kind="configuration",
        can_start_without_inputs=True,
    )
