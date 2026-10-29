# VoiceTransl 开发线路图

> 状态快照：2026-10-29。记录已完成的主要阶段和接下来的方向；具体接口与行为以各专题文档为准。

## 已完成

- **多音频项目工作流：**一个项目包含多个独立音频；支持批量上传/重试、逐音频任务、配置覆盖、进度查看、取消、恢复和重新处理。
- **配置优先的 Pipeline：**建立预处理、ASR、对齐、翻译、导出五阶段流程；任务启动时保存配置快照，恢复任务沿用原快照。
- **本地处理与翻译：**接入 FFmpeg 预处理、本地 CrispASR、OpenAI-compatible/Anthropic HTTP 翻译，以及 SRT、VTT、LRC 导出。
- **本地 ASR 管理：**增加全局 CrispASR 路径设置、受限 GGUF 模型发现与显式选择；不自动下载模型。
- **Provider Profile 基础：**增加后端 Profile CRUD、配置引用校验和任务级非密钥快照；API Key 不保存在 Profile 或任务文件中。
- **节点工作流第一里程碑：**在旧 Pipeline 旁增加节点画布，支持音频输入向多个音频输出直通、独立输出配置、校验/版本化保存、逐音频运行与取消、不可变运行快照和历史产物下载。输出为字节一致的独立副本；尚未迁移 ASR/翻译等处理节点。见[首版范围](../spec/workflow-audio-mvp.md)和[当前架构](architecture.md#7-节点工作流首版)。
- **节点工作流第二里程碑：**接入独立配置的 Mock/FFmpeg 预处理节点，支持原音直出与处理分支并存、串联处理和直接上游产物传递；按依赖顺序执行，失败阻塞下游而独立分支继续。预处理产物可下载，源文件和历史保留。见[预处理节点](../spec/workflow-preprocess.md)。
- **节点工作流第三里程碑：**接入独立 Mock/CrispASR 识别、音频/识别结果类型端口、结构化识别快照及 TXT/SRT/VTT/LRC 原文输出；支持仅文本输出和混合分支，保留失败隔离、取消回收与历史下载。已通过真实本地识别、API、桌面及窄屏验收。见[ASR 与文本输出](../spec/workflow-asr.md)。

## 当前整改

- **兼容性与扩展性（基础项通过，扩展性仍有未完成边界）：**严格持久化版本校验、能力目录、翻译可取消请求、显式语言、自定义命名与结构化输出、分页 API 已有回归覆盖。完整检查发现：工作流运行时当前仅支持单输入/单输出节点，产物端口及前端类型仍有限定；部分请求模型会为缺失的 schema_version 补当前默认值；项目分页仍扫描并排序全部项目目录。以上不应标记为完整验收，详见本轮检查结果。旧 CLI 能力未知时不自动启用；对齐配置继续暂缓。契约及范围见[整改规格](../spec/compatibility-extensibility.md)与[架构](architecture.md)。

## 接下来

1. **优先实现云端 ASR：**按 2026-10-29 用户决定，云端识别优先于本地 ASR 的进一步完善。以现有 OpenAI 草案为起点，先明确服务商/兼容协议、节点工作流接入范围、模型及时间戳能力、上传限制、音频外发提示和重试策略，再实施后端与 Profile 界面并用 fake HTTP 验证。具体契约仍待确认，见[云端 ASR 规格](../spec/cloud-asr-openai.md)。
2. **明确节点重跑契约：**确定输入/配置/Provider 指纹及 artifact 引用格式，落实“强制重跑所选节点及下游、有效上游可复用、缺失上游拒绝启动”，保留旧运行；现有节点历史暂不能复用执行。
3. **补齐历史管理：**设计空间统计、手动清理及共享引用保护；目前不自动清理，不以重跑覆盖历史。

## 已知问题与暂缓事项

- **本地 ASR 能力契约（已验收）：**重复静态后端白名单已移除，当前 CrispASR CLI 能力目录驱动 API、配置选项和提交/队列校验。仅协议能力标记足以确认 ASR 的后端可选；其他或未知任务不启用。模型架构关联仍只采用已验证映射；不据此扩张本地推理功能，云端 ASR 仍优先。
- **对齐配置（Deferred）：**保留当前 Mock/fallback，不在本轮改变 enabled/provider/model/fallback 的生效语义，后续专门设计。

## 暂不纳入首版

Deepgram、AssemblyAI、腾讯云等其他云端 Provider，异步对象存储批处理、说话人分离、自动语言检测、跨音频合并及实时流式识别。待 OpenAI 首版稳定后再评估。

## 长期产品目标

最终目标是提供类似 ComfyUI 的可视化节点工作流：用户能配置节点和连接来决定处理路径，并独立设置各节点。目前已完成音频输入/预处理/ASR/音频与文本输出的有限图编辑，旧五阶段仍独立保留；尚未实现对齐/翻译节点、视频图输出、任意节点重跑或通用汇合。目标范围和待确认问题见[产品目标](product_requirements.md)及[节点工作流规格](../spec/workflow-editor.md)。

## 相关专题

- 架构约定：[`architecture.md`](architecture.md)
- 整体产品目标：[`product_requirements.md`](product_requirements.md)
- 多音频资源和 API：[`voicetransl_multi_audio_project.md`](voicetransl_multi_audio_project.md)
- 前端工作流：[`frontend_workflow.md`](frontend_workflow.md)
- 本地 ASR：[`crispasr_provider.md`](crispasr_provider.md)、[`voicetransl_global_settings.md`](voicetransl_global_settings.md)
- 翻译 Provider：[`translation_providers.md`](translation_providers.md)
- Provider Profile 当前实现：[`provider_profiles.md`](provider_profiles.md)
