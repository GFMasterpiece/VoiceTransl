# VoiceTransl 架构约定

本文保留当前 Web 工作流仍有效的架构约定，替代旧 v0.1 最小实现设计；不包含历史开发计划或代码逐项说明。范围是 `backend/` 与 `frontend/`，不约束旧桌面程序。

资源、目录和 API 细节见[多音频项目契约](voicetransl_multi_audio_project.md)，交互见[前端工作流](frontend_workflow.md)，开发进度见[线路图](roadmap.md)。

## 1. 分层与运行方式

- Vue WebUI 通过 REST 查询/操作资源，通过 WebSocket 接收通知；FastAPI 负责请求校验、路由和错误转换，不直接运行模型。
- `domain/` 定义领域模型；`storage/` 的 ProjectStore 负责项目状态的读写、路径校验和恢复扫描。
- `application/` 的 JobManager 管理任务、取消信号及全局并发；PipelineRunner 管理旧五阶段顺序、状态迁移和恢复，WorkflowRunner 按依赖顺序执行音频输入、预处理、ASR 及音频/文本输出节点。
- Stage 调用 Provider/Exporter 并通过存储层保存产物；Provider 只实现算法或外部服务能力，不直接修改 Job/项目状态。
- 当前使用单进程、单 Uvicorn worker 和进程内异步任务，全局并发默认 1；不同音频可以独立排队，同一音频不能同时运行或排队两个任务。
- 阻塞型模型调用不能占用事件循环；需要可中断的外部调用使用异步传输或受控子进程。仅可协作停止且会等待退出的文件工作使用线程，不以丢弃线程代替取消。数据库、外部 Worker、多进程调度不属于当前架构。

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
| export | `output/` 中按任务配置生成的 SRT、VTT、LRC 文件及结构化 `manifest.json` |

时间以秒存储，格式转换由 Exporter 完成。时间轴由 ASR/Alignment 处理；Translator 不得改变片段数量、ID、顺序、原文和时间范围，翻译阶段需校验输出。

每个阶段先保存 `running` 状态并通知开始，再执行和保存产物，最后保存 `completed` 状态及相对 artifact 路径并通知完成。JSON/文本产物按存储层原子写入规则发布；不能把半写入的产物当作已完成结果。

恢复时跳过产物有效的已完成阶段，从未完成处继续；若某个已完成阶段的产物缺失或不可读取，需重置该阶段及其下游并重跑。重跑应可恢复，但不能静默覆盖源文件或人工编辑的字幕。当前总进度按五阶段等权计算。

## 4. 任务生命周期与事件

- 上传仅创建资源及 `pending` Job，不自动启动。启动请求异步返回，任务遵守全局并发限制。
- 任务依次进入 `pending`、`running`，最终为 `completed`、`failed` 或 `cancelled`；运行中取消可经过 `cancelling`。
- 取消是协作式的：设置取消信号，执行器在可取消边界退出；排队任务可在进入阶段前取消。不删除已有中间产物。HTTP 翻译使用可取消异步传输，取消/超时/关闭需等待本地请求和连接回收，而非丢弃后台线程；不能撤销服务商已接收的计算。
- 重启扫描所有音频，将未正常结束的运行、取消中和已提交排队任务标记为 `interrupted`，不自动重新启动。
- `failed`、`cancelled`、`interrupted` 可以继续；按新配置重新处理必须与恢复旧快照区分。
- 项目删除检查全部音频；任一成员运行、取消中或已提交排队时拒绝删除。
- WebSocket 首先提供 Job snapshot，随后推送阶段/任务事件。事件包含 `project_id`、`audio_id`、`job_id` 等标识；它不提供持久事件历史，断线后通过 REST 校正状态。

## 5. 持久化与安全

- 状态 JSON 使用 UTF-8 与 `schema_version`；产物路径保存为项目相对 POSIX 路径。当前版本为 `1.0`，持久化读取要求显式相同版本，含 Job 配置/Profile 与图运行的嵌套快照；缺失或不同版本拒绝读取，启动恢复跳过且不改写原文件。不实现跨版本迁移。新对象可以使用当前版本默认值，不能用该默认值掩盖持久化文件缺失版本。
- 原子写入使用同目录的唯一临时文件，完成刷新和同步后用 `os.replace()` 发布；失败保留旧状态，并清理本次临时文件。
- 上传先写临时资源，文件与元数据完整后才发布；失败不删除已经成功的项目或其他音频。
- 对项目/音频 ID、下载文件名和 artifact 路径做校验；解析后的路径仍须位于授权目录。客户端不能指定任意本机路径或 shell 命令。
- API 返回统一的 `{error: {code, message, ...}}` 和稳定错误码，不暴露 traceback。诊断细节仅进入服务端日志，并对密钥、认证头和敏感文本脱敏。

## 6. 当前能力边界

预处理支持 Mock/FFmpeg，ASR 支持 Mock/本地 CrispASR，时间轴对齐仍为 Mock/fallback；HTTP 翻译支持 OpenAI-compatible Chat Completions、OpenAI Responses 和 Anthropic Messages。Profile 的 CRUD 与任务快照已实现，但实际云端 ASR 和 Profile 管理界面尚未接通。

## 7. 节点工作流首版

- 与旧 Pipeline 并存，包含一个 `audio_input`、零个或多个 `preprocess` / `asr` 和至少一个终端输出（`audio_output` 或 `text_output`）；支持直出、串联预处理、识别及分支并存。除输入外各节点恰好一条类型兼容入边，输入无入边，终端输出无出边；拒绝环和不可达节点。尚无汇合、对齐/翻译节点或视频图输出。
- 预处理配置复用 `PreprocessConfig`，保存时补齐默认值；省略默认值与显式默认值具有相同执行语义。Mock 生成独立的原字节副本，不转换采样率/声道；FFmpeg 由服务端配置可执行文件，生成独立 WAV。`preserve_original` 为兼容字段，无论取值如何都不能删除或覆盖源文件。
- 端口有类型：音频输入/预处理输出 `audio`，预处理/ASR/音频输出接收 `audio`；ASR 输出 `transcript`，文本输出接收 `transcript`。边的端口名与类型必须匹配，不能以音频字节冒充文本。仅含 ASR 与文本输出而无音频输出的图合法。
- 节点按拓扑依赖顺序逐个执行，读取其直接上游的产物；音频输出复制直接上游字节，沿用其后缀和媒体类型，FFmpeg 下游使用 `.wav` / `audio/wav`。节点数组顺序不决定执行顺序；单次图运行仍占一个全局并发名额，不增加节点并行调度。
- ASR 独立配置 Mock/CrispASR、语言、模型/后端、VAD 和分段时长，不绑定旧 Pipeline 配置或源语言。Mock 是固定示例识别，产物语言沿用节点设置；CrispASR 使用服务端程序/模型根目录和节点的安全标识，执行前要求直接上游为完整、非空、16 kHz 单声道 PCM WAV；不满足时提示添加或调整 FFmpeg，不隐式转换。已符合格式的源 WAV 可直接识别。服务端执行设置在运行提交时捕获，排队期间的设置变化不影响该运行；本机路径不写入图或识别产物。
- ASR 结果保存独立 `transcript.json`（沿用 Transcript/Segment 格式），artifact.port 为 `transcript`；文本输出每节点选择 TXT/SRT/VTT/LRC 中一种，导出原文，artifact.port 为 `text`。TXT 不要求时间戳；字幕需完整有效时间范围，缺失时只使对应输出失败。字幕适配不修改上游译文字段或旧导出语义。
- 每项目保存 `workflow.json` 和不可变的 `workflow-revisions/{revision:08d}.json`。`revision` 用于乐观锁；`workflow_revision` 用于执行版本。纯布局、节点/边数组排序、边 ID 的变化不提升执行版本。未保存时返回默认图（版本 0），第一次保存建立版本 1。
- 每音频独立保存 `workflow-runs/{rid}/run.json` 与 `nodes/{node_id}/{filename}`；主音频放项目根目录，其他音频放 `audios/{aid}/` 下。运行包含不可变图/配置快照，成功的预处理、ASR 及输出节点产物包含大小、SHA256 和下载地址，不覆盖旧 `job.json` 或旧产物。
- API 在项目范围提供 `/workflow` 读取/保存、`/workflow/validate` 校验，在音频范围提供 `/workflow-runs` 创建/分页查询，以及运行详情、取消、逐节点输出元数据/下载。接口基线见[音频直通里程碑](../spec/workflow-audio-mvp.md)，扩展见[预处理节点](../spec/workflow-preprocess.md)和[ASR/文本输出契约](../spec/workflow-asr.md)；所有节点产物复用同一逐节点下载路由，历史路径名 `outputs` 不限定音频。
- 新旧任务共用 JobManager 注册表、音频锁和全局并发限制，同音频不能重叠；任一活动任务阻止项目删除。旧取消接口不能取消图运行，须用图运行自己的取消接口。
- 复制在线程中分块执行，校验字节数、计算 SHA256，同目录临时文件同步后原子发布。成功节点立即落盘；节点失败只阻塞其依赖下游，独立分支继续。整体按最终输出聚合：全部成功为 `completed`，部分成功为 `partial`，无成功输出为 `failed`。拒绝危险文件名、目录穿越和源/产物路径符号链接。
- FFmpeg 不经过 shell，写入同节点目录的临时 WAV，完成并校验后原子发布；固定超时为 300 秒。取消、超时和服务关闭需要终止并回收子进程、等待复制线程退出及清理未完成临时文件，之后才释放音频忙碌边界；错误不向客户端暴露绝对路径或原始 stderr。
- CrispASR 同样使用安全参数数组而非 shell；超时取运行提交时生效的服务器配置，stdout/stderr 丢弃，不将原始诊断写入用户错误。取消和关闭须处理启动竞态、终止及回收进程，并等待产物写入线程退出；未完成节点不得在取消结束后发布产物。文本文件使用 UTF-8/LF，字幕原文适配使用片段副本，不改变上游识别结果。
- 当前图运行通过 REST 轮询，不新增 WebSocket 事件。退出/重启将未完成图运行标为 `interrupted`，不自动恢复；可按当前图新建运行。历史默认保留，但尚无指定节点重跑、结果复用、空间统计或清理入口。

## 8. 能力目录、节点扩展与平台边界

- `GET /api/v1/capabilities` 是前端的能力入口，返回当前协议版本、节点目录及端口、ASR Provider/后端诊断和旧 Pipeline 导出选项。局部本地 ASR 查询失败不应使整个目录失效；返回明确的不可用状态，不能用内置后端名单伪装查询成功。
- 服务端节点定义集中在 `domain/node_registry.py`，以 `register_node(NodeDefinition(...))` 关联类型、端口、配置归一化/校验、字段描述、终端标志和异步执行器；端口的运行时数据契约由 `register_port` 定义。前端从目录生成添加入口、默认配置、校验及通用字段编辑器；专用编辑器只是展示适配，不是另一份能力白名单。
- 执行器接收 `NodeExecutionContext`，通过命名端口读取 `NodeData`，返回 `NodeResult`；通用调度器按注册定义分派，不新增节点类型分支。新增与已有数据类型兼容的处理/终端节点需要独立执行器及一次注册，不接受客户端提供任意 Python 导入路径或可执行代码。
- 这仍是首版单源、逐节点顺序执行图，而不是完整插件平台。当前每个非源节点只接收一个输入，画布每侧只支持一个端口；多输入汇合、新数据源和视频运行契约均未实现，应明确拒绝不能执行的目录形状。新增目录项不表示对应外部模型/协议已验证。
- 平台相关文件锁、受限目录/文件打开、位置读取及权限操作集中于 `application/platform_io.py`；子进程生命周期集中于 `platform_process.py`。POSIX 使用独立进程组；Windows 使用 Job Object 与启动握手，先纳入进程树约束再启动真实命令，避免子进程抢先逃逸。所有真实命令以参数数组调用，不经 shell。
- 取消、超时和服务关闭须等待进程及执行资源回收后再释放忙碌边界；父进程退出不能证明后代已结束。Linux 与 Windows 的关键场景分别回归；Windows 测试不代表全部发行版/部署方式已认证，macOS 未实机验证。平台不支持必要安全保证时明确失败，不静默降级成不安全路径。

各 Provider 的使用、限制和安全规则见 [CrispASR](crispasr_provider.md)、[全局本地 ASR 设置](voicetransl_global_settings.md)、[翻译 Provider](translation_providers.md) 与 [Provider Profile](provider_profiles.md)。测试命令及交付要求统一维护在 [AGENTS.md](../AGENTS.md)。
