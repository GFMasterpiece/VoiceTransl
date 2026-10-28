# VoiceTransl 架构约定

本文保留当前 Web 工作流仍有效的架构约定，替代旧 v0.1 最小实现设计；不包含历史开发计划或代码逐项说明。范围是 `backend/` 与 `frontend/`，不约束旧桌面程序。

资源、目录和 API 细节见[多音频项目契约](voicetransl_multi_audio_project.md)，交互见[前端工作流](frontend_workflow.md)，开发进度见[线路图](roadmap.md)。

## 1. 分层与运行方式

- Vue WebUI 通过 REST 查询/操作资源，通过 WebSocket 接收通知；FastAPI 负责请求校验、路由和错误转换，不直接运行模型。
- `domain/` 定义领域模型；`storage/` 的 ProjectStore 负责项目状态的读写、路径校验和恢复扫描。
- `application/` 的 JobManager 管理任务、取消信号及全局并发；PipelineRunner 管理旧五阶段顺序、状态迁移和恢复，WorkflowRunner 执行首版音频直通节点图。
- Stage 调用 Provider/Exporter 并通过存储层保存产物；Provider 只实现算法或外部服务能力，不直接修改 Job/项目状态。
- 当前使用单进程、单 Uvicorn worker 和进程内异步任务，全局并发默认 1；不同音频可以独立排队，同一音频不能同时运行或排队两个任务。
- 阻塞型模型调用不能占用事件循环，应通过子进程或线程执行。数据库、外部 Worker、多进程调度不属于当前架构。

## 2. 资源与配置

- 文件系统是唯一持久状态来源。一个项目对应独立目录，包含一个或多个音频；各音频拥有自己的当前 Job、字幕、进度和输出，不共享时间轴。
- 项目公共默认配置与音频覆盖的优先级、主音频 `aud_primary` 的旧接口兼容规则，以多音频项目契约为准；不再采用“一项目一任务”的限制。
- 新任务保存不可变 `job.config_snapshot`；引用的 Provider Profile 保存到非密钥 `provider_profiles_snapshot`。恢复任务沿用原快照，重新处理创建新 Job ID 和新快照。
- 旧 Pipeline 每个音频仅维护一个当前 Job，不承诺旧任务历史归档；新增节点工作流另存运行历史，不能混淆两套行为。
- 服务配置按代码默认值、YAML、环境变量的顺序覆盖。真实 API Key 仅由后端从指定环境变量读取，不进入项目文件、Profile、快照或前端。

## 3. Pipeline 与产物

旧 Pipeline 阶段顺序固定为 `preprocess → asr → alignment → translation → export`：

| 阶段 | 当前音频范围内的主要产物 |
| --- | --- |
| preprocess | `cache/preprocess.json`；FFmpeg 另生成 `cache/preprocessed.wav` |
| asr | `transcript/raw.json` |
| alignment | `transcript/aligned.json` |
| translation | `transcript/translated.json` |
| export | `output/` 中按任务配置生成的 SRT、VTT、LRC 文件 |

时间以秒存储，格式转换由 Exporter 完成。时间轴由 ASR/Alignment 处理；Translator 不得改变片段数量、ID、顺序、原文和时间范围，翻译阶段需校验输出。

每个阶段先保存 `running` 状态并通知开始，再执行和保存产物，最后保存 `completed` 状态及相对 artifact 路径并通知完成。JSON/文本产物按存储层原子写入规则发布；不能把半写入的产物当作已完成结果。

恢复时跳过产物有效的已完成阶段，从未完成处继续；若某个已完成阶段的产物缺失或不可读取，需重置该阶段及其下游并重跑。重跑应可恢复，但不能静默覆盖源文件或人工编辑的字幕。当前总进度按五阶段等权计算。

## 4. 任务生命周期与事件

- 上传仅创建资源及 `pending` Job，不自动启动。启动请求异步返回，任务遵守全局并发限制。
- 任务依次进入 `pending`、`running`，最终为 `completed`、`failed` 或 `cancelled`；运行中取消可经过 `cancelling`。
- 取消是协作式的：设置取消信号，执行器在可取消边界退出；排队任务可在进入阶段前取消。不删除已有中间产物。
- 重启扫描所有音频，将未正常结束的运行、取消中和已提交排队任务标记为 `interrupted`，不自动重新启动。
- `failed`、`cancelled`、`interrupted` 可以继续；按新配置重新处理必须与恢复旧快照区分。
- 项目删除检查全部音频；任一成员运行、取消中或已提交排队时拒绝删除。
- WebSocket 首先提供 Job snapshot，随后推送阶段/任务事件。事件包含 `project_id`、`audio_id`、`job_id` 等标识；它不提供持久事件历史，断线后通过 REST 校正状态。

## 5. 持久化与安全

- 状态 JSON 使用 UTF-8 与 `schema_version`；产物路径保存为项目相对 POSIX 路径。新增兼容字段提供默认值，不为整理文档而迁移运行数据。
- 原子写入使用同目录的唯一临时文件，完成刷新和同步后用 `os.replace()` 发布；失败保留旧状态，并清理本次临时文件。
- 上传先写临时资源，文件与元数据完整后才发布；失败不删除已经成功的项目或其他音频。
- 对项目/音频 ID、下载文件名和 artifact 路径做校验；解析后的路径仍须位于授权目录。客户端不能指定任意本机路径或 shell 命令。
- API 返回统一的 `{error: {code, message, ...}}` 和稳定错误码，不暴露 traceback。诊断细节仅进入服务端日志，并对密钥、认证头和敏感文本脱敏。

## 6. 当前能力边界

预处理支持 Mock/FFmpeg，ASR 支持 Mock/本地 CrispASR，时间轴对齐仍为 Mock/fallback；HTTP 翻译支持 OpenAI-compatible Chat Completions、OpenAI Responses 和 Anthropic Messages。Profile 的 CRUD 与任务快照已实现，但实际云端 ASR 和 Profile 管理界面尚未接通。

## 7. 节点工作流首版

- 与旧 Pipeline 并存，只支持一个 `audio_input` 向一个或多个 `audio_output` 扇出。输出逐个复制原音频字节为独立普通文件，不转码、不生成字幕；不是通用 DAG 执行器。
- 每项目保存 `workflow.json` 和不可变的 `workflow-revisions/{revision:08d}.json`。`revision` 用于乐观锁；`workflow_revision` 用于执行版本。纯布局、节点/边数组排序、边 ID 的变化不提升执行版本。未保存时返回默认图（版本 0），第一次保存建立版本 1。
- 每音频独立保存 `workflow-runs/{rid}/run.json` 与 `nodes/{node_id}/{filename}`；主音频放项目根目录，其他音频放 `audios/{aid}/` 下。运行包含不可变图/配置快照，成功输出包含大小、SHA256 和下载地址，不覆盖旧 `job.json` 或旧产物。
- API 在项目范围提供 `/workflow` 读取/保存、`/workflow/validate` 校验，在音频范围提供 `/workflow-runs` 创建/分页查询，以及运行详情、取消、逐节点输出元数据/下载。完整字段及错误码见[已验收的里程碑契约](../spec/workflow-audio-mvp.md)。
- 新旧任务共用 JobManager 注册表、音频锁和全局并发限制，同音频不能重叠；任一活动任务阻止项目删除。旧取消接口不能取消图运行，须用图运行自己的取消接口。
- 复制在线程中分块执行，校验字节数、计算 SHA256，同目录临时文件同步后原子发布。成功节点立即落盘；分支失败不阻止其他输出，整体可为 `partial`。拒绝危险文件名、目录穿越和源/产物路径符号链接。
- 当前图运行通过 REST 轮询，不新增 WebSocket 事件。退出/重启将未完成图运行标为 `interrupted`，不自动恢复；可按当前图新建运行。历史默认保留，但尚无指定节点重跑、结果复用、空间统计或清理入口。

各 Provider 的使用、限制和安全规则见 [CrispASR](crispasr_provider.md)、[全局本地 ASR 设置](voicetransl_global_settings.md)、[翻译 Provider](translation_providers.md) 与 [Provider Profile](provider_profiles.md)。测试命令及交付要求统一维护在 [AGENTS.md](../AGENTS.md)。
