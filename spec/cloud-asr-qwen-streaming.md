# Qwen 文件流式识别节点

状态：**Implemented**（2026-11-08）。用户已授权新增独立节点，并明确连接和密钥独属于各节点实例。首版处理已有文件；麦克风采集及实时字幕界面不在本轮范围。

## 范围

- 新节点 `qwen_asr_streaming` 归入语音识别，保留现有 Filetrans 节点及默认直通图。输入一份 `audio`，可选语言参数，输出统一 `transcript`；批处理仍在图外展开，每份文件独立完成。
- 推荐模型为 `qwen-audio-3.0-asr-flash-streaming`，模型名保持自由字符串。同协议的新模型可修改配置；其他实时协议需要另行适配，不能仅换模型名。
- 当前输入限真实的单声道、16 位 PCM 编码 WAV；从文件头读取每份文件的采样率，批次不要求相同采样率。格式、声道、位深或文件完整性不符时，在连接服务前明确失败，不自动转码、截断、猜测或回退 Filetrans/Mock。
- 不扩展项目上传格式，不提供不可从现有输入上传的 raw PCM 配置，不引入 FFmpeg 或本地模型依赖。

## 协议来源

核对日期：2026-11-08，来源为用户指定平台的专属 Streaming 文档：

- [WebSocket 接入](https://platform.qianwenai.com/docs/api-reference/speech-recognition/qwen-audio-asr-streaming/websocket-api)。
- [客户端事件](https://platform.qianwenai.com/docs/api-reference/speech-recognition/qwen-audio-asr-streaming/client-events)。
- [服务端事件](https://platform.qianwenai.com/docs/api-reference/speech-recognition/qwen-audio-asr-streaming/server-events)。

完整端点默认 `wss://maas.qianwenaiapi.com/api-ws/v1/inference`。使用 `run-task / task-started / result-generated / finish-task / task-finished` 协议，不使用另一套 `session.update / input_audio_buffer` 事件。生产连接仅接受 WSS；本机回环 WS 仅用于本地兼容服务及隔离验收，拒绝 URL 认证信息、查询参数及片段。

## 每个节点独立连接与密钥

- 节点内直接填写完整 `endpoint`、总超时、模型、语言、附加参数及 API Key；不选择全局 Profile，不读取其他节点的连接或凭据，不提供环境变量继承。两个同类型节点也互不影响。
- 复用加密存储与只写保存机制，但密钥版本仍绑定项目 ID、节点 ID 和独立 `provider_type=qwen_asr_streaming`。复用存储实现不代表共享连接或密钥；跨实例、跨类型、跨项目引用均拒绝。
- 待保存密码只存在当前编辑会话私有缓冲区。复制、复制为新节点、粘贴、导入及导出剥离凭据引用，不复制明文；新节点需重新输入自己的 Key。删除、清除、撤销及重试不能让另一节点的凭据变化。
- 保存图、修订及运行快照只包含非密钥连接与不可变引用。排队、批次子运行和失败重试沿用原连接及原 Key 版本，不读取后来修改的节点。
- Filetrans 与 Streaming 均仅使用每节点连接；旧 Profile/环境变量执行兼容已按[独立连接规格](node-connections-only.md)移除，历史文件与产物不自动迁移、补写或删除。

## 目录与通用扩展

- 持久化 schema 为 `1.0`，当前能力目录为 `1.11`。`presentation.inline_connection_input` 只声明 `provider_type`、`label`、`base_url_key`、`timeout_key`、可空 `region_key`、`credential_ref_key` 及可选 `data_handling`；没有 Profile、连接模式或环境变量角色。
- Streaming 声明端点、超时和凭据角色，地域为空；Filetrans 使用相同通用声明，前端和调度器不按 Qwen 类型分支。内联连接使用不持久化的 `InlineConnection`，旧 Profile 专用模型与冻结器已删除。
- Streaming 的存储期限未声明，`remote_cancel_pending_only=false`、`cancel_behavior=close_stream`，不套用 Filetrans 临时上传期限。这些协议元数据继续保留，但节点和运行界面不展示数据处理/取消说明段落。

## 执行与结果

1. 在总超时和取消边界内校验 WAV，从真实头信息得到协议 `format=wav` 与 `sample_rate`。
2. 使用当前节点解密后的 Key 建立 WebSocket，生成新的任务 UUID 并发送一次 `run-task`。语言配置或参数线优先，空值使用该音频声明的语言，按文档发送一个 `language_hints`。
3. 等待匹配任务的 `task-started` 后，以有界文件读取和按音频速率控制的节奏发送二进制帧，同时接收结果。
4. 文件发送完成后发送一次 `finish-task`，继续收取剩余结果，直到匹配的 `task-finished` 才确认完成；提前断线不作为成功。
5. 只汇总最终句子；中间文本、句子开始和心跳不冒充最终结果。按声道与句子编号识别重复，完全相同的最终句子只保留一次，冲突的重复最终句子明确失败。保留真实字词/说话人信息，毫秒转秒，缺时间戳不伪造。
6. 节点发布独立 `transcript.json`，沿用预览、TXT/字幕导出、稳定编号及安全下载；完整结果产生前不向下游输出临时转写。

附加参数默认空，仅显式提供时发送；不得覆盖任务、模型、输入、格式、采样率、语言核心字段、连接或认证。限制 JSON 大小与深度，不把未知同协议模型当作永久白名单之外的非法值。

## 取消与失败

- 总超时覆盖文件检查、握手、收发及最终完成；关闭连接和清理等待有界。取消、超时和服务关闭都要停止并回收收发任务及进行中的有界磁盘读取，不能遗留工作线程继续读完整音频。
- 无自动重连、自动重发 `run-task` 或自动恢复任务；断线/未知状态使用安全错误，重试由现有新运行机制明确发起。
- 关闭本地 WebSocket 不能保证撤回已经发送的数据或确认远端已停止、退款。
- Key、认证头、原始远端错误、调试日志及不受控服务字段不进入图、历史、响应或产物；不把错误文本当识别内容。

## 验收

- 假 WebSocket 完整覆盖握手、任务状态顺序、边发边收、语言/附加参数、finish 后结果、真实时间戳、最终句子去重及 TXT/字幕产物。
- 覆盖 WAV 头与截断、声道/位深错误、非 WAV、混合采样率、未知同协议模型、超大/坏事件、任务 ID 不符、断线、各阶段取消、总超时、关闭清理及无重发。
- 同图包含两个 Streaming 节点及 Filetrans 节点，验证地址和 Key 独立、跨实例引用拒绝、复制无密钥，以及轮换后的队列与重试仍使用原引用。
- 旧运行/批次与产物被动读取不改原文件；旧 Profile 图明确拒绝，现有无旧字段的 Streaming 图继续使用自身凭据。纯节点连接扩展不修改通用调度器或前端类型名单。
- 后端全量与 Ruff、前端全量与构建，并在 Windows 隔离服务/模拟平台中进行真实浏览器验收。未使用真实账号时，不宣称已完成真实服务、质量或计费验收；未实测的其他系统不视为通过。

## 初版验收记录（1.10，旧连接兼容已退役）

- Windows 后端全量 **644 passed, 4 skipped**，Ruff 检查及格式检查通过；Streaming 传输、节点及原 Filetrans 定向回归 **179 passed**，覆盖各阶段取消/超时、实际线程读取回收、限速、重定向拒绝、异常事件、旧图原字节与摘要、冻结序号/连接/凭据。
- 前端全量 **457 passed**，最后双语错误码更新后相关 **71 passed**，构建通过；实际后端 1.10 目录可由前端解析，旧目录回归保留。
- 真实 Headless Edge 连接隔离后端及假 HTTP/WebSocket 平台：同图两个 Streaming 与一个 Filetrans，各自密钥；两份 8k/16k WAV 批次及普通单运行、提交后轮换地址/Key仍用原快照、真实 SRT 时间、重复最终句子去重、复制无凭据、取消关闭连接、缺 Key 提交拒绝、中英切换保留私有输入与 390px 画布检查通过，浏览器异常为 0。测试仅使用合成音频及假密钥。
- 未验证真实平台账号、质量、计费、长期保留策略或其他系统；4 项跳过不计为通过。

当前目录 1.11 与旧连接移除的验证见[独立连接规格](node-connections-only.md)；上述初版测试数仅作历史记录。
