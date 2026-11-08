# 服务连接与 Qwen 识别

当前 Web 支持独立的 `qwen_asr` 录音文件识别与 `qwen_asr_streaming` 文件流式识别节点，原 `asr` 仍为 Mock，默认图仍为音频直通。协议与验收范围分别见[Filetrans 规格](../spec/cloud-asr-qwen.md)和[Streaming 规格](../spec/cloud-asr-qwen-streaming.md)，其他历史 Provider 类型的存在不代表有对应执行器。

## Filetrans 使用

1. 在音频输入节点上传或选择音频并明确源语言，添加 Qwen 识别节点，连到文本预览或文本输出。
2. 新节点直接配置服务地址、总超时、地域及 API Key。默认地址为 `https://maas.qianwenaiapi.com/api/v1`，总超时默认 3600 秒；地域是人工声明的元数据，不决定路由或证明实际处理地域。密码框留空保留原密钥，填写新值会在统一保存时替换，清除须显式操作。保存成功后不回填明文，仅显示已保存状态。
3. 校验、保存并运行；多份音频沿用现有批次调度，输出节点继续按前缀及原清单编号命名。可选环境变量名用于没有直接凭据时的备用；旧 Profile 节点保留原路径，也可显式切为节点内连接。环境变量值的更改需要被后端进程继承，不能用浏览器环境代替。
4. 推荐模型为 `qwen-audio-3.1-asr-flash-filetrans`。模型名可以编辑，不作为白名单；替代模型必须支持相同的 Filetrans HTTP 协议。Qwen3 的其他接口不能仅替换模型名套用。

识别语言留空使用当前运行音频已声明的源语言；可手填覆盖，也可接“当前文件信息”或字符串参数线。附加参数是 JSON 对象，默认 `{}`，例如 `{"diarization_enabled":true}`；默认不发送额外可选参数，不允许覆盖模型、输入、核心语言、认证或路由。当前没有自动语言检测、音频分块或静默截断策略。

## Streaming 使用

在语音识别分类添加 Qwen Streaming 节点，连接音频及文本预览/输出。每个实例直接填写完整 `endpoint`、总超时及 API Key；默认地址 `wss://maas.qianwenaiapi.com/api-ws/v1/inference`，默认模型 `qwen-audio-3.0-asr-flash-streaming`。模型可改为同一协议的其他模型，没有永久白名单；不同协议不能只换模型名。

仅接受实际为 PCM16 单声道的 WAV，逐文件读取采样率。非 WAV、不同位深/声道或损坏文件在连接前失败，不自动转码；上传策略允许的其他格式不代表本节点支持。当前发送已有文件，完成后输出最终转写，不提供麦克风或边识别边刷新的字幕。

Streaming 没有 Profile、地域、环境变量或连接模式字段，也不读取 Filetrans 或另一个实例的 Key。两个相同节点也须分别保存密钥。复制/导入后需重新填写自己的 Key。生产端点仅 WSS，明文 WS 仅限本机回环；拒绝认证信息、查询参数和片段，连接不跟随重定向。

语言及附加参数沿用上述核心规则。传输按音频速率分块发送，边发送边接收；只有任务完整结束才发布最终结果，中间文本与重复最终句子不进入产物。取消/超时关闭本地连接并回收收发和读取，不自动重连或重发；无法保证远端立即停止、退款或撤回已发送数据。Streaming 文档未声明本轮可承诺的文件保留时长，不沿用 Filetrans 的 48 小时规则。

## 节点连接、密钥与保存

新 Filetrans 节点显式保存 `connection_mode=inline`，非密钥字段为 `base_url`、`timeout_seconds`、可空 `region`、`api_key_env` 及不透明 `credential_ref`；Streaming 使用 `endpoint`、`timeout_seconds`、`credential_ref` 且始终是节点内连接。密钥本身不属于节点 config，不开放参数线，也不写进图、修订、运行快照或产物。浏览器仅在当前会话私有缓冲区保存待提交密码；未保存密码参与草稿保护和运行禁用，切换语言、节点尺寸和视口保留输入。失败保留输入；成功、删除节点或明确放弃时清理，撤销不恢复明文。

保存沿用 `PUT /api/v1/projects/{pid}/workflow`，通过只写 `credential_updates` 的 `set` / `clear` 操作提交，附带稳定 `request_id`；同请求重试不会反复创建凭据版本。预校验只提交图，不提交密码。允许保存未配置密钥的图，实际运行前必须有可用凭据。API 错误不回显密码。

后端使用 [Fernet 认证加密](https://cryptography.io/en/latest/fernet/) 独立保存不可变凭据版本，引用同时绑定项目、节点及 Provider；不能把其他节点或项目的引用直接套用。复制、粘贴、复制节点及工作流导入导出按目录声明剥离本机引用，之后重新配置密钥。旧版本保留供已有队列、历史及重试使用，清除当前绑定不会删除历史版本。主密钥损坏或丢失时明确失败，不重新生成替代。

主密钥与密文均存于后端本机数据根的 `.credentials/`，不包含在工作流导出内。目录含 `master.key`、用于校验主密钥的 `store.check` 和 `versions/` 密文；Windows 继承数据目录 ACL，POSIX 创建时限制权限。备份应同时保护它们；若整个数据目录同时泄露，加密不再提供隔离保护。删除项目当前不清理全局密文，尚无主密钥轮换或历史凭据垃圾回收。

## 旧 Profile API 与目录

`/api/v1/provider-profiles` 提供 GET/POST，`/{id}` 提供 GET/PUT/DELETE。全局 `provider_profiles.json` 保存 `{schema_version:"1.0",profiles:[...]}`，原子写入，仅接受同版本。

字段包括 ID、显示名、Provider 类型、HTTP(S) `base_url`、地域、总超时、启用状态、时间及 `api_key_env`。实际密钥由服务端执行时解析，不返回给前端。URL 不允许用户名密码；云节点提交额外拒绝 query/fragment，避免把认证信息混入服务地址。

目录自 `workflow.schema_version=1.7` 起通过 `presentation.provider_profile_input` 声明旧连接字段、类型、显示名及创建默认值，后端按已启用且类型匹配的连接派生下拉选项。`1.9` 增加 `presentation.inline_connection_input`，声明模式及节点内连接各字段角色；前端不硬编码节点类型或连接字段。可选 `data_handling` 声明临时存储时长和远端取消边界，界面按声明展示。新增同协议模型只改节点配置；新增不同协议需独立适配器、节点声明和验收。

当前目录 `1.10` 的内联声明直接提供 Provider 类型和显示名，不再依赖 Profile 声明；模式角色成对为空表示始终内联，地域与备用环境变量角色可空。Streaming 单独声明 `cancel_behavior=close_stream`，旧目录及 Filetrans 的解释方式保留。内联连接使用非持久 `InlineConnection`，不扩张旧 HTTP ProviderProfile 的 URL 契约。

连接修改只刷新连接与目录缓存，不重新加载当前图；草稿、撤销、节点内待上传文件和视口保留。失效 ID 保留并显错，不自动改选。删除检查公共可编辑图及旧 Pipeline 引用，仍被引用返回 `PROVIDER_PROFILE_IN_USE`；历史不可变快照不单独阻止删除。旧只读 Pipeline 暂无解除引用的迁移工具。

## 快照与处理链路

内联连接随运行图冻结完整的非密钥设置及凭据版本引用；Filetrans 使用备用环境变量时，也在提交时把其值封存为凭据版本，Streaming 必须使用自身保存的 Key。排队、批次子运行和失败重试继续用原版本，后来换地址、密钥或环境变量不影响已提交任务。直接凭据优先；明确绑定却无法解密时失败，不能静默回退环境变量。

旧 Profile 模式在普通运行提交、批次创建时将非密钥 Profile 冻结在 `workflow.provider_profiles_snapshot`。实际密钥值仍由冻结的变量名在执行时解析。缺 `connection_mode` 的旧图继续按此语义处理，读取不补内联字段；旧运行、旧批次缺快照保持未记录，不能补用当前连接。

本地文件先获取本次模型的临时 OSS 凭证，按凭证返回的大小/有效期分块上传，再以 `input.file_urls` 提交单文件异步识别，轮询并下载结果 JSON。OSS 和结果请求不携带平台 Bearer，也不共享认证 Cookie；签名地址、上传凭证和原始服务错误不进入日志、快照或产物。识别提交不自动重发，避免丢响应后重复创建任务。

返回句子使用真实时间戳（毫秒转秒），字词与说话人保留为结构化信息；只有全文时保留空时间戳。TXT 可导出无时间文本，SRT/VTT/LRC 继续要求完整时间范围。失败不回退 Mock。

## Filetrans 取消与限制

总超时覆盖凭证、上传、提交、轮询和下载；取消、超时及关闭会停止并等待本地请求、文件流和任务回收。已知任务 ID 时仅尝试一次有界远端取消，平台仅允许取消 `PENDING`，`RUNNING` 可能继续执行或计费。本地取消不等同于远端退款或删除音频。

临时文件约 48 小时后由平台清理，没有找到提前删除接口。平台文档不建议此临时上传接口用于生产高并发，本轮不提供生产对象存储、自动重试或真实服务连通性测试。文件和时长限制还受项目上传策略、模型和本次上传凭证约束，超限不截断冒充成功。

旧 Profile 提交可返回 `PROVIDER_PROFILE_REQUIRED/NOT_FOUND/DISABLED/TYPE_MISMATCH`、缺地址/环境变量名等稳定错误；内联连接和凭据失败使用对应固定错误。执行失败继续使用安全的 `QWEN_*` 代码及诊断，不暴露密钥、签名 URL 或服务错误全文。

协议来源及核对日期见对应规格。验证使用假 HTTP、WebSocket 与 Windows 隔离本地服务；没有真实账号验证时，不宣称已验证计费、长音频质量或实际服务保留政策。
