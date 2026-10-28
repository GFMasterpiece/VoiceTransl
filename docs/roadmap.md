# VoiceTransl 开发线路图

> 状态快照：2026-10-28。记录已完成的主要阶段和接下来的方向；具体接口与行为以各专题文档为准。

## 已完成

- **多音频项目工作流：**一个项目包含多个独立音频；支持批量上传/重试、逐音频任务、配置覆盖、进度查看、取消、恢复和重新处理。
- **配置优先的 Pipeline：**建立预处理、ASR、对齐、翻译、导出五阶段流程；任务启动时保存配置快照，恢复任务沿用原快照。
- **本地处理与翻译：**接入 FFmpeg 预处理、本地 CrispASR、OpenAI-compatible/Anthropic HTTP 翻译，以及 SRT、VTT、LRC 导出。
- **本地 ASR 管理：**增加全局 CrispASR 路径设置、受限 GGUF 模型发现与显式选择；不自动下载模型。
- **Provider Profile 基础：**增加后端 Profile CRUD、配置引用校验和任务级非密钥快照；API Key 不保存在 Profile 或任务文件中。
- **节点工作流第一里程碑：**在旧 Pipeline 旁增加节点画布，支持音频输入向多个音频输出直通、独立输出配置、校验/版本化保存、逐音频运行与取消、不可变运行快照和历史产物下载。输出为字节一致的独立副本；尚未迁移 ASR/翻译等处理节点。见[首版范围](../spec/workflow-audio-mvp.md)和[当前架构](architecture.md#7-节点工作流首版)。

## 接下来

1. **细化下一批处理节点：**以已运行的直通图为基础，确认预处理/ASR/文本输出的类型端口、节点配置及 Provider 映射，再逐个接入现有能力；不是直接批准完整编辑器草案。
2. **明确节点重跑契约：**确定输入/配置/Provider 指纹及 artifact 引用格式，落实“强制重跑所选节点及下游、有效上游可复用、缺失上游拒绝启动”，保留旧运行；首版直通历史暂不能复用执行。
3. **补齐历史管理：**设计空间统计、手动清理及共享引用保护；目前不自动清理，不以重跑覆盖历史。
4. **云端 ASR 后续路线：**继续核对 OpenAI API 模型、上传/时间戳限制、音频外发提示和重试策略，范围获批后接通后端与 Profile 界面，并用 fake HTTP 验证。见[云端 ASR 规格](../spec/cloud-asr-openai.md)。

## 暂不纳入首版

Deepgram、AssemblyAI、腾讯云等其他云端 Provider，异步对象存储批处理、说话人分离、自动语言检测、跨音频合并及实时流式识别。待 OpenAI 首版稳定后再评估。

## 长期产品目标

最终目标是提供类似 ComfyUI 的可视化节点工作流：用户能配置节点和连接来决定处理路径，并独立设置各节点。目前已完成音频输入/输出的有限图编辑，旧五阶段仍独立保留；尚未实现处理节点编排、文本/视频图输出、任意节点重跑或通用汇合。目标范围和待确认问题见[产品目标](product_requirements.md)及[节点工作流规格](../spec/workflow-editor.md)。

## 相关专题

- 架构约定：[`architecture.md`](architecture.md)
- 整体产品目标：[`product_requirements.md`](product_requirements.md)
- 多音频资源和 API：[`voicetransl_multi_audio_project.md`](voicetransl_multi_audio_project.md)
- 前端工作流：[`frontend_workflow.md`](frontend_workflow.md)
- 本地 ASR：[`crispasr_provider.md`](crispasr_provider.md)、[`voicetransl_global_settings.md`](voicetransl_global_settings.md)
- 翻译 Provider：[`translation_providers.md`](translation_providers.md)
- Provider Profile 当前实现：[`provider_profiles.md`](provider_profiles.md)
