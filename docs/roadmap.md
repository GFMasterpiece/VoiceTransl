# VoiceTransl 开发线路图

> 状态快照：2026-10-28。记录已完成的主要阶段和接下来的方向；具体接口与行为以各专题文档为准。

## 已完成

- **多音频项目工作流：**一个项目包含多个独立音频；支持批量上传/重试、逐音频任务、配置覆盖、进度查看、取消、恢复和重新处理。
- **配置优先的 Pipeline：**建立预处理、ASR、对齐、翻译、导出五阶段流程；任务启动时保存配置快照，恢复任务沿用原快照。
- **本地处理与翻译：**接入 FFmpeg 预处理、本地 CrispASR、OpenAI-compatible/Anthropic HTTP 翻译，以及 SRT、VTT、LRC 导出。
- **本地 ASR 管理：**增加全局 CrispASR 路径设置、受限 GGUF 模型发现与显式选择；不自动下载模型。
- **Provider Profile 基础：**增加后端 Profile CRUD、配置引用校验和任务级非密钥快照；API Key 不保存在 Profile 或任务文件中。

## 接下来

1. **确定云端 ASR 首版边界：**核对 OpenAI 官方 API 的模型、上传限制和时间戳能力；决定超限处理、无时间戳结果、重试及音频外发确认策略。见 [云端 ASR 规格](../spec/cloud-asr-openai.md)。
2. **打通 OpenAI ASR 后端：**将其实现为现有 ASR 阶段 Provider；服务端从环境变量读取密钥，处理响应校验、超时、取消和脱敏错误；失败时不得静默退回 Mock。
3. **完成前端配置和外发提示：**提供 Profile 管理、ASR Profile 选择，并在启动云端任务前说明音频将发送到哪个服务商/区域。后端未支持前，界面不得让用户误以为云端任务可以运行。
4. **端到端验收：**用 fake HTTP 覆盖成功和错误路径；验证密钥不泄漏、任务快照可恢复、多音频任务互不影响，再更新使用说明和测试记录。

## 暂不纳入首版

Deepgram、AssemblyAI、腾讯云等其他云端 Provider，异步对象存储批处理、说话人分离、自动语言检测、跨音频合并及实时流式识别。待 OpenAI 首版稳定后再评估。

## 长期产品目标

最终目标是提供类似 ComfyUI 的可视化节点工作流：用户能配置节点和连接来决定处理路径，并独立设置各节点。当前固定五阶段流程尚不支持自由编排。目标范围和待确认问题见[产品目标](product_requirements.md)及[节点工作流规格](../spec/workflow-editor.md)。

## 相关专题

- 架构约定：[`architecture.md`](architecture.md)
- 整体产品目标：[`product_requirements.md`](product_requirements.md)
- 多音频资源和 API：[`voicetransl_multi_audio_project.md`](voicetransl_multi_audio_project.md)
- 前端工作流：[`frontend_workflow.md`](frontend_workflow.md)
- 本地 ASR：[`crispasr_provider.md`](crispasr_provider.md)、[`voicetransl_global_settings.md`](voicetransl_global_settings.md)
- 翻译 Provider：[`translation_providers.md`](translation_providers.md)
- Provider Profile 当前实现：[`provider_profiles.md`](provider_profiles.md)
