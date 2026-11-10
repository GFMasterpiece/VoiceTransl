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
| [服务分类与字段旁连线](service-families-field-inputs.md) | Implemented | LLM/ASR 服务分开、移除 ASR 上下文及控件左侧参数插口 |
| [普通文本上下文与多输入拼接](plain-text-context.md) | Implemented | 普通字符串上下文、动态输入拼接、可选地域及可空字段声明 |
| [可组合的节点配置与上下文](composable-node-configuration.md) | Implemented | 首版职责拆分；现用普通文本上下文与动态拼接见上项 |
| [LLM API 翻译节点](llm-translation.md) | Implemented | 通用兼容 API、上游当前文本、链式翻译、预览导出及结果复用 |
| [浏览器后端连接设置](backend-connection.md) | Implemented | 主页连接状态、地址检测与浏览器保存、刷新隔离及受控 CORS |
| [兼容性与扩展性整改](compatibility-extensibility.md) | Approved | 严格版本、能力/节点单一来源、可取消传输、附加参数、跨平台、语言与命名/分页 |
| [节点资源与独立预览](node-resources-preview.md) | Implemented | 空项目、输入节点音频绑定、独立文本/音频预览及历史隔离 |
| [节点模块化与多端口](node-modularity.md) | Implemented | 节点模块独立、命名多端口与汇合调度、后端声明分类、目录和历史兼容 |
| [节点参数与连线输入统一](node-parameters.md) | Implemented | 可连线参数、基础值节点、参数来源与实际执行值 |
| [编辑器常用操作](editor-operations.md) | Implemented | 撤销/重做、多选、整组编辑、复制/剪切/粘贴及工作流导入导出 |
| [文件夹音频批处理](batch-processing.md) | Implemented | 多目录导入、固定快照逐文件调度、取消/重试及批次结果 |
| [输入节点资源清单与编号输出](node-batch-input.md) | Implemented | 输入节点导入、统一单/批运行、输出前缀与稳定编号 |
| [Qwen 录音文件识别](cloud-asr-qwen.md) | Implemented | 独立云端识别、节点内连接与密钥、临时上传及冻结快照 |
| [Qwen 文件流式识别](cloud-asr-qwen-streaming.md) | Implemented | 独立 WebSocket 节点、每实例连接/密钥、WAV 文件流式传输及统一结果 |
| [仅保留节点独立连接](node-connections-only.md) | Implemented | 移除 Profile/环境变量连接、全局管理及旧执行兼容，保留历史产物 |
| [可搜索语言候选](searchable-languages.md) | Implemented | 识别及源语言共用后端候选、搜索和自定义代码 |
| [全局设置与节点创建默认值](global-settings.md) | Implemented | 上传与运行并发、节点默认配置、独立默认密钥及新节点物化 |
| [设置分类与调试节点展示](settings-node-organization.md) | Implemented | 类别导航、同类字段顺序、Mock 调试开关及输入模式去重 |
| [节点级重跑与结果复用](node-rerun-reuse.md) | Implemented | 指定起点强制重算、上游 typed checkpoint 验证及新运行隔离 |
| [可视化节点工作流编辑器](workflow-editor.md) | Draft | 整体目标；当前音频直通、Mock、编辑器、批次及Qwen能力见各独立规格 |
| [OpenAI 云端 ASR](cloud-asr-openai.md) | Draft | OpenAI协议尚未实现；首个云端节点采用独立Qwen规格 |

整体产品方向见 [`../docs/product_requirements.md`](../docs/product_requirements.md)，阶段与进度见 [`../docs/roadmap.md`](../docs/roadmap.md)。首个音频直通里程碑的 Retired 摘要已合并至线路图，原规格可从 Git 历史追溯。规格获批和实现后才把稳定行为沉淀为 `docs/` 契约，不在两处重复维护完整细节。
