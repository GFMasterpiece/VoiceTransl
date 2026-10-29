# VoiceTransl 功能规格

`spec/` 存放需要明确范围和验收条件的功能要求；已验收的里程碑保留为 Implemented，供追溯范围与验收记录。当前使用说明与架构仍以 `docs/` 和代码为准；规格草案不是现有功能说明。

## 状态

- **Draft**：讨论稿。范围、体验或关键决策尚未确认；不能据此宣称功能已实现。
- **Approved**：用户确认范围和验收条件，可据此规划和实现。
- **Implemented**：代码已实现并通过规格验收；还需更新对应 `docs/` 的实际用法/契约及 `docs/roadmap.md`。
- **Deferred**：已记录但暂不排期，注明原因和重启讨论的条件。

将 Draft 改为 Approved 前，需检查目标、非目标、用户流程、关键约束、失败/恢复语义、兼容要求、验收标准及未决问题。实现中发现规格和现有契约冲突时，先记录影响并请求决策，不得静默改写已运行数据或当前行为。

## 索引

| 规格 | 状态 | 主题 |
| --- | --- | --- |
| [可视化节点工作流编辑器](workflow-editor.md) | Draft | 实现 ComfyUI 风格的可视化节点编排和节点独立配置 |
| [首个音频直通里程碑](workflow-audio-mvp.md) | Implemented | 已验收输入/输出图编辑、版本化保存及独立运行历史；不含处理节点与重跑 |
| [音频预处理节点里程碑](workflow-preprocess.md) | Implemented | 已验收 Mock/FFmpeg 预处理、直接上游产物、依赖调度及分支失败隔离 |
| [ASR 与文本输出节点里程碑](workflow-asr.md) | Implemented | 已验收 Mock/CrispASR、类型端口、结构化识别产物及 TXT/SRT/VTT/LRC 原文输出 |
| [OpenAI 云端 ASR](cloud-asr-openai.md) | Draft | 在现有音频处理流程中接入首个云端语音识别 Provider |

整体产品方向见 [`../docs/product_requirements.md`](../docs/product_requirements.md)，阶段与进度见 [`../docs/roadmap.md`](../docs/roadmap.md)。规格获批和实现后才把稳定行为沉淀为 `docs/` 契约；不要在两处重复维护完整细节。
