# VoiceTransl 功能规格

`spec/` 存放需要明确范围和验收条件的功能要求；已验收的里程碑保留为 Implemented，供追溯范围与验收记录。当前使用说明与架构仍以 `docs/` 和代码为准；规格草案不是现有功能说明。

## 状态

- **Draft**：讨论稿。范围、体验或关键决策尚未确认；不能据此宣称功能已实现。
- **Approved**：用户确认范围和验收条件，可据此规划和实现。
- **Implemented**：代码已实现并通过规格验收；还需更新对应 `docs/` 的实际用法/契约及 `docs/roadmap.md`。
- **Deferred**：已记录但暂不排期，注明原因和重启讨论的条件。
- **Retired**：对应实现已退役，不能作为当前能力或实施依据；只剩历史摘要时合并到线路图，通过 Git 追溯原规格，不继续保留重复文档。

将 Draft 改为 Approved 前，需检查目标、非目标、用户流程、关键约束、失败/恢复语义、兼容要求、验收标准及未决问题。实现中发现规格和现有契约冲突时，先记录影响并请求决策，不得静默改写已运行数据或当前行为。

## 索引

| 规格 | 状态 | 主题 |
| --- | --- | --- |
| [兼容性与扩展性整改](compatibility-extensibility.md) | Approved | 严格版本、能力/节点单一来源、可取消传输、附加参数、跨平台、语言与命名/分页 |
| [节点资源与独立预览](node-resources-preview.md) | Implemented | 空项目、输入节点音频绑定、独立文本/音频预览及历史隔离 |
| [可视化节点工作流编辑器](workflow-editor.md) | Draft | 目标方向；当前仅保留音频直通和 Mock 验证能力 |
| [OpenAI 云端 ASR](cloud-asr-openai.md) | Draft | 云端服务商、协议和节点接入范围待确定，尚未实现 |

整体产品方向见 [`../docs/product_requirements.md`](../docs/product_requirements.md)，阶段与进度见 [`../docs/roadmap.md`](../docs/roadmap.md)。首个音频直通里程碑的 Retired 摘要已合并至线路图，原规格可从 Git 历史追溯。规格获批和实现后才把稳定行为沉淀为 `docs/` 契约，不在两处重复维护完整细节。
