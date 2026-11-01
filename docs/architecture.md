# VoiceTransl 架构约定

本文描述当前维护的 Web 实现，范围是 `backend/` 与 `frontend/`。旧桌面字幕翻译程序及其依赖资源已从当前源码移除，历史实现可通过 Git 追溯。资源与 API 见[多音频项目契约](voicetransl_multi_audio_project.md)，交互见[前端工作流](frontend_workflow.md)，进度见[线路图](roadmap.md)。

## 1. 分层与运行方式

- Vue 3 前端通过 REST 读写项目、工作流及运行；FastAPI 负责请求校验和错误转换。
- `domain/` 定义模型及节点注册；`storage/` 负责文件读写、路径边界和恢复扫描；`application/` 管理运行、取消及并发。
- 文件系统是唯一持久数据源。不引入数据库、Redis、Celery 或外部 Worker。
- 当前只支持单进程、单 Uvicorn worker，使用进程内异步任务；一次图运行占一个全局并发名额，默认额度为 1。
- 同一音频不能重复运行或排队；不同音频独立排队。工作流内按拓扑顺序逐个执行，不提供节点并行或多输入汇合。

## 2. 最小工作流

默认图只有 `audio_input -> audio_output`，连接双方的 `audio` 端口。它复制输入的原始字节为独立输出，沿用后缀和媒体类型；不转码，不用链接或源文件引用替代副本。

可按需添加的内置节点如下；这不是固定的五节点执行链：

| 节点 | 输入 → 输出 | 配置与行为 |
| --- | --- | --- |
| `audio_input` | 无 → `audio` | 可选 `audio_id` 绑定项目内资源；节点内上传或选择音频，不能填写本机路径；旧 `{}` 使用运行音频上下文 |
| `preprocess` | `audio` → `audio` | 仅 `provider=mock`，复制直接上游原字节，不转换采样率/声道 |
| `asr` | `audio` → `transcript` | 仅 `provider=mock`、`language`（默认 `ja`），产生固定示例文本，不执行真实识别 |
| `audio_output` | `audio` → 终端 | 安全的 `filename_stem`（默认 `audio-copy`），复制直接上游字节 |
| `text_output` | `transcript` → 终端 | 安全的 `filename_stem`（默认 `transcript`）和 `format`（TXT/SRT/VTT/LRC），导出 Mock 文本 |
| `audio_preview` | `audio` → 终端 | 无配置，保存该次输入的预览副本，供节点内播放器读取 |
| `text_preview` | `transcript` → 终端 | 无配置，保存转写文本表示，供节点内纯文本展示 |

Mock ASR 的语言只标记示例结果及片段的语言，不翻译固定示例文本。真实模型、后端、VAD、分段、Provider Profile、采样率、声道及“删除源文件”均不属于当前节点配置。未知字段和非 Mock Provider 明确拒绝，不能静默回退。

图要求一个输入、至少一个终端节点（预览或导出）；除源节点外恰好一条类型兼容入边，终端无出边。允许分支和串联，拒绝环、孤岛、无效端口、未知节点及危险配置；节点与边各最多 64 个。输入直连输出或预览均合法，不要求存在处理节点或文件导出节点。

## 3. 保存、运行与产物

- 每项目一份 `workflow.json`，不可变文档修订位于 `workflow-revisions/{revision:08d}.json`。`revision` 用于保存乐观锁；`workflow_revision` 表示执行语义。纯布局、数组排序及边 ID 变化不提升执行版本。
- 没有已保存图时返回版本 0 的默认直通图，不因此落盘或改写旧 Job。首次保存建立版本 1。
- 运行绑定一个音频、图版本及不可变节点配置快照。后来编辑图不能修改已提交或排队的运行。
- 输入显式指定 `audio_id` 时，校验及保存检查项目资源归属，启动时检查其与运行路由音频一致；不一致返回 `INPUT_AUDIO_MISMATCH`。旧 `{}` 输入继续使用该次运行的音频，不在读取旧图时补写绑定。
- 主音频的运行存于项目根 `workflow-runs/{rid}/`，其他音频存于 `audios/{aid}/workflow-runs/{rid}/`。包含 `run.json` 与 `nodes/{node_id}/{filename}`。
- 成功节点立即保存状态和独立产物。artifact 包含 `artifact_id`、`port`、`filename`、`media_type`、`size_bytes`、`sha256`、`download_url`，不暴露本机绝对路径。
- 预览节点另保存可空 `preview={renderer,content_url,media_type}`，旧节点记录缺失该字段时为无预览。音频、文本预览缓存按运行和节点隔离，通过安全的 inline 内容接口读取；浏览器加载失败不更改后端运行状态。前端依据渲染器注册表显示组件，并标明结果所属运行及历史状态。
- 节点读取直接上游结果；失败只阻塞依赖下游，独立分支继续。全部终端成功为 `completed`，部分成功为 `partial`，无成功终端为 `failed`。
- 运行状态还包括 `pending/running/cancelling/cancelled/interrupted`；节点状态还包括 `pending/running/failed/blocked/cancelled/interrupted`。
- TXT 可导出无时间戳片段；SRT/VTT/LRC 要求有效时间范围。缺失时间戳只使相应文本输出失败，不影响独立音频分支。

## 4. 取消、重启与历史

- 前端通过 REST 轮询当前运行；旧 Job WebSocket 不发送节点运行事件，也不是新界面的状态来源。
- 复制在线程中分块执行，取消时发出协作信号并等待退出；异步 Mock 延迟可取消。释放音频忙碌边界前必须回收执行与文件资源，不得取消结束后继续发布产物。
- 服务启动将未结束的图运行标记为 `interrupted`，不自动继续；服务关闭等待执行清理。
- 完成、失败、取消或中断后只能按当前图新建运行，不能修改旧记录。任意节点续跑、缓存复用、空间统计、历史清理尚未实现，历史默认保留。
- 任一音频运行或排队时拒绝项目删除，返回 `PROJECT_BUSY`。

## 5. 兼容与安全

- 旧固定 Pipeline 执行器、阶段界面及运行/取消/字幕/导出接口已移除；旧配置 GET 和 Job 摘要仅用于兼容读取，配置 PUT 明确拒绝。它们不参与当前节点执行。
- 旧项目、配置和历史快照不批量迁移或清空；`preset` 等历史字段不再用于创建选择。旧图中已删除的 Provider 或参数不可再次运行，需用户显式调整后保存新修订，不能自动替换成 Mock。
- 持久化 JSON 使用 UTF-8 与显式 `schema_version=1.0`；仅接受相同版本，含嵌套快照。缺失或不同版本拒绝读取，恢复扫描跳过且不改写错误文件。
- JSON 与产物使用同目录唯一临时文件，刷新、同步后 `os.replace()` 发布；失败清理本次临时文件，保留旧状态。
- 存储路径为项目相对 POSIX 路径；解析后必须仍在授权目录。拒绝目录穿越、危险符号链接和 Windows 保留文件名，不能覆盖源文件或其他运行产物。
- 当前节点不执行外部命令、读取 API Key 或调用真实模型，不依赖旧桌面程序或本地模型工具。跨平台能力需由当前 Web 测试证明，不能根据旧桌面测试宣称已兼容；Windows 无符号链接权限导致的跳过不等于对应场景通过。
- 面向用户返回稳定错误码，不暴露 traceback、密钥或本机路径。运行数据和个人脚本不入库。

## 6. 能力来源与扩展边界

`GET /api/v1/capabilities` 从 `domain/node_registry.py` 的实时注册表及工作流领域规则发布节点、端口、数据类型、配置字段、图约束与默认图。顶层 capability API schema version 保持 `1.0`；`workflow.schema_version` 独立标记工作流 capability 契约，当前为 `1.3`，字段 shape 改变时必须提升该版本，客户端严格拒绝未知版本。配置字段的数值/字符串边界和描述从其 Pydantic schema 派生；后端复杂语义校验以 validator id 描述，不交由前端复制安全规则。前端据此生成添加入口、默认配置和通用字段表单。节点的可空 `presentation` 通过 `resource_input={resource_type,config_key}` 和 `preview={renderer}` 声明资源选择及预览展示入口，前端不按节点类型名称特判。图的节点/边数及源/终端数量约束与后端校验共用同一权威值。持久化 schema 仍为 `1.0`，新字段可选，旧数据不自动改写；前端的本地校验仅用于即时反馈，服务端仍执行最终验证。

节点通过 `register_node(NodeDefinition(...))` 关联配置校验、端口、终端标志与异步执行器；`register_port` 只定义稳定的端口 ID 与 `type_id` 绑定，另外声明目标端口需要的能力；`register_type` 定义逻辑数据类型、父类型、能力集合和运行时校验。端口身份不再承担数据类型身份：例如 `pcm_audio_out` 可以绑定到 `audio_pcm`，并通过父类型关系连接到要求 `audio` 的输入端口。能力目录发布 `{id, type_id, label, capability_requirements}` 端口对象和独立的 `data_types` 目录，工作流边只保存稳定的端口 ID，不保存展示名称。执行器接收 `NodeExecutionContext`、读取命名 `NodeData` 并返回 `NodeResult`；运行时按注册表校验值是否满足逻辑类型。新增已有数据类型的节点应通过独立执行器与注册接入，而不是修改通用调度器。不能让客户端提交任意导入路径或代码。

当前仅支持单源、非源节点单输入、画布每侧一个端口；多输入汇合、新数据源、视频及真实云端 Provider 需要先确认独立规格。全局 Profile CRUD 的留存不代表云端调用可用，见[Profile 兼容边界](provider_profiles.md)。

## 7. 前端语言边界

前端通过 Vue I18n 的集中消息目录提供简体中文与英文，语言偏好仅保存在浏览器。切换语言更新展示和文档标题，不改变项目语言、图配置、接口请求、持久化结构或运行快照，也不触发重新获取项目或能力目录。具体交互见[前端工作流](frontend_workflow.md)。

后端错误码、节点类型、端口与配置契约仍是稳定的机器标识。前端按已知标识查找展示译文，同时保留原能力数据和服务端错误详情；未知扩展使用原始标签或诊断，不因缺少翻译被拒绝。当前不提供后端日志、API 文档或生成产物内容的自动翻译。

测试及交付要求见 [AGENTS.md](../AGENTS.md)。
