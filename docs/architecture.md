# VoiceTransl 架构约定

本文描述当前维护的 Web 实现，范围是 `backend/` 与 `frontend/`。旧桌面字幕翻译程序及其依赖资源已从当前源码移除，历史实现可通过 Git 追溯。资源与 API 见[多音频项目契约](voicetransl_multi_audio_project.md)，交互见[前端工作流](frontend_workflow.md)，进度见[线路图](roadmap.md)。

## 1. 分层与运行方式

- Vue 3 前端通过 REST 读写项目、工作流及运行；FastAPI 负责请求校验和错误转换。
- 浏览器启动时统一确定 API 地址：浏览器已保存地址优先，否则采用 `VITE_API_BASE_URL` 或 `/api/v1`。主页检测连接后保存并刷新，不热切换现有上传、轮询及缓存；批次未确认请求按实际 API origin、路径与项目隔离。资源的 `/api/v1/...` 路径映射至配置的 API 前缀，完整 HTTP(S) 资源 URL 沿用原契约。
- CORS 默认向严格的 loopback HTTP(S) 前端 origin 授权；其他前端域由 `app.cors_origins` 或逗号分隔的 `VOICETRANSL_CORS_ORIGINS` 列出完整 origin（不含路径及末尾斜杠）。不支持 `*`、`null` 或携带凭据的 CORS，允许现有 REST、上传及 Range 请求。该项属于启动配置，不写入全局设置或项目；不能代替远程部署的身份认证。
- `domain/` 定义数据、节点契约及通用注册核心；`nodes/` 的独立模块声明内置节点的配置、端口和执行入口；`storage/` 负责文件读写、路径边界和恢复扫描；`application/` 管理运行、取消、并发及媒体产物发布；`adapters/` 隔离云端协议、传输及结果转换。
- 文件系统是唯一持久数据源。不引入数据库、Redis、Celery 或外部 Worker。
- 当前只支持单进程、单 Uvicorn worker，使用进程内异步任务；一次图运行占一个全局并发名额，默认额度为 1，可通过主页全局设置动态调整。稳定许可器保留等待队列，降额度不取消已执行任务，批次 worker 在取项边界收缩。
- 同一音频不能重复运行或排队；不同音频独立排队。工作流内按拓扑顺序逐个执行，支持多输入汇合；仍不提供节点并行。

## 2. 最小工作流

默认图只有 `audio_input -> audio_output`，连接双方的 `audio` 端口。它复制输入的原始字节为独立输出，沿用后缀和媒体类型；不转码，不用链接或源文件引用替代副本。

可按需添加的内置节点如下；这不是固定的五节点执行链：

| 节点 | 输入 → 输出 | 配置与行为 |
| --- | --- | --- |
| `audio_input` | 无 → `audio` | 互斥的 `audio_id` 单项或 `audio_ids` 有序资源清单；节点内导入文件/目录，图外逐文件展开；旧 `{}` 使用运行音频上下文 |
| `preprocess` | `audio` → `audio` | 仅 `provider=mock`，复制直接上游原字节，不转换采样率/声道 |
| `asr` | `audio`、可选 `language` → `transcript` | 仅 `provider=mock`、`language`（默认 `ja`），产生固定示例文本，不执行真实识别 |
| `qwen_asr` | `audio`、可选 `language` → `transcript` | 独立连接、自由模型名、语言及附加参数；临时上传、异步识别并转换真实文本/时间戳 |
| `qwen_asr_streaming` | `audio`、可选 `language` → `transcript` | 每实例独立 WebSocket 地址/Key；PCM16 单声道 WAV 按真实采样率流式发送，任务完成后输出最终转写 |
| `llm_translate` | `transcript`、可选 `model/source_language/target_language` → `transcript` | 独立 Chat Completions 连接/Key；翻译上游当前文本，保留原文及片段结构 |
| `audio_output` | `audio`、可选 `filename_stem` → 终端 | 安全前缀（默认 `audio-copy`）及 `naming_mode`；新节点默认编号，缺模式旧图原名，复制直接上游字节 |
| `text_output` | `transcript`、可选 `filename_stem/text_format` → 终端 | 安全前缀（默认 `transcript`）、`naming_mode` 和 `format`（TXT/SRT/VTT/LRC），导出统一转写文本 |
| `audio_preview` | `audio` → 终端 | 无配置，保存该次输入的预览副本，供节点内播放器读取 |
| `text_preview` | `transcript` → 终端 | 无配置，保存转写文本表示，供节点内纯文本展示 |

转写以 `metadata.current_text={version:"1",field:"source_text"|"normalized_text"|"translated_text",language:"..."}` 声明当前文本。统一领域函数供翻译、预览、导出及 checkpoint 使用，不根据节点类型或 stage 推测。无声明的旧数据继续读取原文；非法声明或缺少指定内容明确失败。翻译节点深拷贝输入、填写译文并更新当前语言；原文、ID、时间轴及片段元数据保留，第二个翻译节点消费第一个的译文。文本预览/输出执行版本为 `2`；旧历史文件不改写，旧 ASR checkpoint 仍可复用。详见[翻译规格](../spec/llm-translation.md)。
| `string_value` / `integer_value` / `number_value` / `boolean_value` | 无 → 对应基础值类型 | 参数来源节点，保存 `{value: ...}`；字符串不是 `text` 文件产物，整数可满足数字类型，布尔值不冒充整数或数字 |
| `source_info` | 无 → `filename/stem/source_language/relative_path/root_name` | 当前运行音频的只读字符串信息；无配置，作为参数根，可接输出命名或 ASR 语言 |

Mock ASR 的语言只标记示例结果及片段的语言，不翻译固定示例文本。这些 Mock 节点不接受模型、VAD、分段、Profile、采样率、声道及“删除源文件”配置；未知字段和非 Mock Provider 明确拒绝。真实识别通过独立 Qwen 节点接入，不恢复旧配置，也不在失败时回退 Mock。

图要求恰好一个音频资源源、至少一个终端节点（预览或导出）；额外的显式参数根不计入资源源数量。可达性从资源源和参数根的并集计算，所有节点仍须能到达终端，普通零输入节点不能自行成为合法根。节点可声明多个输入/输出；每个必填输入恰好一条类型兼容连接，可选输入零或一条；一个输出可连接多个下游，同一节点对允许连接不同端口的多条边。内置终端不声明输出端口。允许分支、串联和汇合，拒绝环、孤岛、无效端口、未知节点及危险配置；节点与边各最多 64 个。输入直连输出或预览均合法，不要求存在处理节点或文件导出节点。

## 3. 保存、运行与产物

- 每项目一份 `workflow.json`，不可变文档修订位于 `workflow-revisions/{revision:08d}.json`。`revision` 用于保存乐观锁；`workflow_revision` 表示执行语义。纯布局、数组排序及边 ID 变化不提升执行版本。
- 节点可选 `size={width,height}` 保存用户布局尺寸；旧节点缺失或为 `null` 时由前端按内容决定尺寸，读取不补写旧文件。宽高必须为有限正数且不超过 1,000,000；尺寸随图保存及运行快照保留，但不进入执行语义摘要。仅调整尺寸增加文档修订，不改变 `workflow_revision` 或使已有结果失效。
- 没有已保存图时返回版本 0 的默认直通图，不因此落盘或改写旧 Job。首次保存建立版本 1。
- 编辑器的多选与最多 50 步撤销/重做只存在于当前项目浏览器会话，不进入持久化文档；保存后撤销仍以最近成功保存内容计算未保存状态，不撤销服务端操作。JSON 导入先成为可撤销草稿，仍通过现有工作流 API 校验并保存；可移植文件只含图与布局，并按目录清除项目资源绑定，不带运行历史或产物。详情见[编辑器规格](../spec/editor-operations.md)。
- 运行绑定一个音频、图版本及不可变节点配置快照。后来编辑图不能修改已提交或排队的运行。可空 `batch_id` 标识所属批次，旧历史缺省为无批次，不补写；重新打开批次子运行时不会把公共图和导航按单运行锁定。
- 节点配置字段以 `input_port` 声明可连线覆盖。首批允许 ASR `language`、两类输出的 `filename_stem`、文本输出 `format`；固定 Mock `provider` 和资源绑定 `audio_id` 不开放参数线。手填配置始终作为合法后备保留在图与节点记录中；执行时合入上游值、完整校验/规范化，再把节点副本传给执行器，不改写运行图快照。
- 新运行排队时按自身快照冻结 `parameter_sources`，实际取得上游结果后保存 `effective_config`。来源按字段区分手填与连接，连接包含边 ID 和源/目标端口；未解析实际值为 `null`。候选参数非法时明确失败而不回退手填值；缺失或失败上游阻断依赖，无关分支仍可继续。旧记录缺少新字段时保持未记录，不使用当前默认值推断，也不补写历史文件。
- 输入显式指定 `audio_id` 或有序唯一 `audio_ids` 时，校验及保存检查资源归属；两者互斥，清单最多 10000。空清单可保存但不能运行，一项与启动路由必须一致，多项必须走批次；初次批次请求与图内清单须完全同序。批次内部运行及重试只检查当前项属于冻结完整清单，不重写输入节点。旧 `{}` 继续使用该次运行的音频，不在读取旧图时补写绑定。
- 主音频的运行存于项目根 `workflow-runs/{rid}/`，其他音频存于 `audios/{aid}/workflow-runs/{rid}/`。包含 `run.json` 与 `nodes/{node_id}/{filename}`。
- 成功节点立即保存状态和独立产物。artifact 包含 `artifact_id`、`port`、`filename`、`media_type`、`size_bytes`、`sha256`、`download_url`，不暴露本机绝对路径。
- 预览节点另保存可空 `preview={renderer,content_url,media_type}`，旧节点记录缺失该字段时为无预览。音频、文本预览缓存按运行和节点隔离，通过安全的 inline 内容接口读取；浏览器加载失败不更改后端运行状态。前端依据渲染器注册表显示组件，并标明结果所属运行及历史状态。
- 节点读取直接上游结果；失败只阻塞依赖下游，独立分支继续。全部终端成功为 `completed`，部分成功为 `partial`，无成功终端为 `failed`。
- 运行状态还包括 `pending/running/cancelling/cancelled/interrupted`；节点状态还包括 `pending/running/failed/blocked/cancelled/interrupted/skipped`，其中 `skipped` 表示本次重跑未参与。
- TXT 可导出无时间戳片段；SRT/VTT/LRC 要求有效时间范围。缺失时间戳只使相应文本输出失败，不影响独立音频分支。

## 4. 取消、重启与历史

- 前端通过 REST 轮询当前运行；旧 Job WebSocket 不发送节点运行事件，也不是新界面的状态来源。
- 复制在线程中分块执行，取消时发出协作信号并等待退出；异步 Mock 延迟可取消。释放音频忙碌边界前必须回收执行与文件资源，不得取消结束后继续发布产物。
- 服务启动将未结束的图运行标记为 `interrupted`，不自动继续；服务关闭等待执行清理。
- 完成、失败、取消或中断后只能按当前图新建运行，不能修改旧记录。节点重跑使用明确选择的历史结果，普通运行仍全图重算；自动缓存调度、空间统计和历史清理尚未实现，历史默认保留。
- 任一音频运行或排队时拒绝项目删除，返回 `PROJECT_BUSY`。

### 节点重跑和结果快照

节点重跑采用当前已保存图和同音频的明确终态来源，创建新的运行。所选节点及全部后代强制执行，必要外部祖先验证后复用，其他节点为 `skipped`；仅冻结实际执行云节点的连接及密钥。预检不调用识别服务，提交锁内再次校验，幂等重放优先于忙碌及修订检查。批次单项重跑是独立运行，保留原输出编号，不更改原批次。

新运行的成功节点在 `.checkpoints/{node_id}/checkpoint.json` 保存 typed sidecar，运行 JSON 只记录指纹、大小及摘要。codec 在实际数据类型上显式注册，子类型不自动继承；节点实现语义变化通过 `execution_version` 失效。指纹比较实际源文件 SHA-256/相关元数据、完整配置与参数映射、递归上游指纹和语义输出摘要，不包含布局、边 ID 或运行路径。无 codec 扩展仍可正常运行，必要上游不可复用时明确拒绝。

复用文件物理复制进新运行，并生成新的 artifact ID/URL；排队执行前再次核验自身副本，不依赖来源运行继续存在，也不自动补算损坏结果。sidecar 上限 16 MiB；首版文件副本未去重，会额外占用磁盘。旧历史缺 checkpoint 不回填或猜测，仍可查看、下载及重新全图运行。接口及验收见[节点重跑规格](../spec/node-rerun-reuse.md)。

## 5. 兼容与安全

- 旧固定 Pipeline 执行器、阶段界面及运行/取消/字幕/导出接口已移除；旧配置 GET 和 Job 摘要仅用于兼容读取，配置 PUT 明确拒绝。它们不参与当前节点执行。
- 旧项目、配置和历史快照不批量迁移或清空；`preset` 等历史字段不再用于创建选择。旧图中已删除的 Provider 或参数不可再次运行，需用户显式调整后保存新修订，不能自动替换成 Mock。
- 持久化 JSON 使用 UTF-8 与显式 `schema_version=1.0`；仅接受相同版本，含嵌套快照。缺失或不同版本拒绝读取，恢复扫描跳过且不改写错误文件。
- JSON 与产物使用同目录唯一临时文件，刷新、同步后 `os.replace()` 发布；失败清理本次临时文件，保留旧状态。
- 存储路径为项目相对 POSIX 路径；解析后必须仍在授权目录。拒绝目录穿越、危险符号链接和 Windows 保留文件名，不能覆盖源文件或其他运行产物。
- 当前节点不执行外部命令，不依赖旧桌面或本地模型工具；云节点仅解析自身的加密凭据版本并调用自身配置的服务，不读取 Profile 或环境变量密钥。秘密及签名地址不进入日志或产物。跨平台能力需由当前 Web 测试证明，平台条件跳过不等于对应场景通过。
- 面向用户返回稳定错误码，不暴露 traceback、密钥或本机路径。运行数据和个人脚本不入库。

## 6. 能力来源与扩展边界

`GET /api/v1/capabilities` 从实时节点注册表发布分类、端口、类型、配置、约束与默认图。API 与持久化 schema 仍为 `1.0`，独立工作流目录为 `1.15`；字段边界从 Pydantic schema 派生，复杂语义由后端校验。目录不再读取全局 Profile 文件，也不派生旧连接选项；数量约束与验证共用权威值，客户端拒绝未知目录版本。

顶层 `languages` 来自独立的 `domain/languages.py`，包含代码、中英文名、原名及别名。字段 `suggestion_source="languages"` 从节点模型显式派生，只有声明的字符串字段启用候选，不按字段名或类型猜测。上传与识别共用清单；候选不参与配置验证、规范化、图快照或摘要，不是模型能力白名单，未知自定义代码保持原样。交互与兼容见[语言候选规格](../spec/searchable-languages.md)。

目录 `1.15` 为每个节点发布严格布尔 `debug_only`，内置 Mock ASR/预处理仅在浏览器调试模式下提供添加入口及默认设置。该标记不删除注册类型，不限制已有图、导入或历史执行。全局设置按节点类别导航、类别内选择类型，字段按 `settings_default_fields` 的有序声明派生；连接/认证与处理参数分组展示，各类型配置和 Key 保持独立。展示元数据不进入持久化或执行摘要，旧目录不猜测调试属性。详见[设置组织规格](../spec/settings-node-organization.md)。

目录 `1.13` 增加全局设置能力。`NodeDefinition.settings_default_fields` 显式声明可设置的创建字段，`GET/PUT /api/v1/settings` 从注册表派生字段、初值和验证，`settings.json` 原子发布并检查修订及请求幂等。仅添加节点时复制默认配置，原模型缺省读取与既有图不变；新增声明只补齐内存视图，不改写旧设置。默认密钥在 `.default_credentials/` 独立加密，新节点保存时按冻结版本物化成项目/节点拥有的凭据，运行和重试不读全局默认。上传策略发布并发，前端每队列捕获已加载值；详情见[全局设置](global_settings.md)。

节点密钥通过只写更新提交到标准 Fernet 加密存储，图与快照只保留项目/节点/类型绑定的不可变引用。凭据先落盘再原子发布图，相同请求重试幂等；无引用密文可保留，不能清理仍可能被历史引用的版本。密码缓冲不进入前端图/撤销，复制及导入导出清除引用；队列与重试使用原版本，见[节点连接](node_connections.md)。

目录 `1.11` 的 `presentation.inline_connection_input` 仅声明 `provider_type/label/base_url_key/timeout_key/region_key/credential_ref_key` 和可选 `data_handling`；地域角色可空，不再有 Profile、模式或环境变量角色。非持久 `InlineConnection` 仅表达节点自身连接。通用执行器按声明冻结/读取，前端按声明渲染，不使用节点类型分支。旧连接目录与配置明确拒绝，不自动迁移；历史原始元数据与产物保持可读，不恢复执行兼容。

Streaming 适配器使用单次 WebSocket 的 `run-task/task-started`、二进制音频、`result-generated`、`finish-task/task-finished` 协议；安全端点规则集中在其协议模块，配置及传输层共用。仅接受经有界头检查的 PCM16 单声道 WAV，按真实采样率限速发送并并发接收；完成后才发布统一 Transcript（stage=`raw`），真实毫秒转秒，最终句子按声道/编号去重并拒绝矛盾结果。取消及总超时会回收收发、进行中的读取和连接；无重连/重发，不提供麦克风、自动转码或实时字幕 UI。完整契约见[Streaming 规格](../spec/cloud-asr-qwen-streaming.md)。

目录 `1.8` 的每个节点发布可空 `category={id,label}`，由节点声明；共享内置类别集中在 `domain/node_categories.py`，注册核心校验标识、标签及同ID一致性。Qwen与Mock识别均归语音识别。前端根据声明分组及搜索，不维护节点到类别的第二份映射；未知类别保留后端标签，旧目录及缺分类扩展显示未分类。分类仅是展示元数据，不进入持久图、运行快照或执行语义；前端兼容 `1.2–1.7` 原目录。

目录 `1.5` 引入字段可空 `input_port` 及节点 `is_parameter_source`，保留严格命名必填输入数组；参数绑定必须存在、唯一、类型兼容且不是必连端口。`1.6` 在资源声明中增加可空集合键，形状为 `resource_input={resource_type,config_key,collection_key}`；资源字段均由专用控件处理，前端按声明识别和清除可移植导出的绑定。可空的 `creation_default` 只决定新节点配置，缺字段旧配置仍用 `default`，避免显示和执行不一致。可空 `presentation.preview={renderer}` 继续选择预览组件。前端兼容 `1.2–1.7` 的原声明，不按节点名称特判；持久化 schema 仍为 `1.0`，旧文件及运行快照不自动补写。

`domain/node_contracts.py` 定义自描述契约，`domain/node_registry_core.py` 管理通用实时注册与目录派生，`domain/node_registry.py` 保留旧导入入口并显式组合内置类型与节点。各 `nodes/` 模块拥有配置、声明和执行入口；输出配置不再由目录按 `config_kind` 特判。节点通过 `register_node(NodeDefinition(...))` 关联配置校验、端口、终端标志与异步执行器；`register_port` 只定义稳定的端口 ID 与 `type_id` 绑定，另外声明目标端口需要的能力；`register_type` 定义逻辑数据类型、父类型、能力集合和运行时校验。端口身份不再承担数据类型身份：例如 `pcm_audio_out` 可以绑定到 `audio_pcm`，并通过父类型关系连接到要求 `audio` 的输入端口。能力目录发布 `{id, type_id, label, capability_requirements}` 端口对象和独立的 `data_types` 目录，工作流边只保存稳定的端口 ID，不保存展示名称；边端口 ID 与注册规则保持一致，最多 64 字符。执行器接收 `NodeExecutionContext`、读取命名 `NodeData` 并返回 `NodeResult`；运行时按注册表校验值是否满足逻辑类型。节点使用通用 `NodeExecutionError(code,message)` 报告可展示的失败，完成资源清理后通过 `NodeExecutionCancelled` 报告取消；调度器不依赖 ASR 等节点专用异常。`required_input_ports` 逐名声明必填输入，目录中的 `required_inbound` 为其长度；旧未声明命名要求的定义继续将旧计数 0 解释为全可选，其余按全部输入必填派生。调度按每条边的 `source_port → target_port` 组装命名输入，同时校验输入与全部已声明输出的运行时类型。未连接的可选输入不传入；连接了失败上游的可选输入也会阻断当前节点。文件复制、源音频定位与原子发布集中到 `application/workflow_media.py`，调度器不特判内置节点类型。新增已有数据类型的节点通过独立模块与组合入口注册接入，无需修改通用调度器。不能让客户端提交任意导入路径或代码。

每次图运行仍为单源、单音频，批次在图外组织多份音频；多端口不代表混音。每节点仍至多保存一份 artifact 和一份 preview，多份持久导出使用独立输出节点。参数连线、动态字段、选定输出执行、缓存、事件和子图分别见对应规格，不作为当前已实现能力。云协议分别使用可取消 HTTP 或 WebSocket、完整总超时和有界传输。旧 `provider_profiles_snapshot` 仅是被动历史元数据，不参与提交、重试或鉴权；旧服务连接已按[独立连接规格](../spec/node-connections-only.md)移除。

## 7. 文件夹导入与批次调度

- 目录选择在浏览器完成，服务端只接收上传的音频和受校验的来源元数据，不扫描客户端传来的任意本机路径。`SourceAsset.origin` 可空，原始目录不作为实际存储路径；旧资源读取不补写。
- `application/batch_manager.py` 在图外组织逐文件运行，`storage/batch_store.py` 持久化批次，沿用同一节点执行器。批次提交时固定图、清单、原文件名、源语言和来源，排队期间不读取最新公共图；源元数据当前无修改 API。
- `JobManager` 的音频预留覆盖批次全部排队项，与单运行共用忙碌边界及 semaphore。首版一个项目最多一个活动批次，服务端固定数量 dispatcher 按全局并发额度派发，不为每个文件创建等待任务。单文件失败不停止其他文件。
- 取消先停止派发，再取消活动执行并等待回收；关闭服务先停止批次，再停止单文件任务。启动扫描把未结束批次和文件项标记 `interrupted`，不自动恢复调用。明确重试创建新批次及新运行，沿用原快照和选中文件的来源记录，不改旧历史。
- 批次及文件项使用分页接口。打包在临时 ZIP 中按来源根、相对父目录、文件任务和节点隔离，附带 `manifest.json`；产物仍按原运行归属和路径检查，下载结束清理临时文件。
- 新批次的 `BatchItem.sequence=index+1` 固定输出编号，重试复制原序号，可有间隙；旧批次缺字段时仅在新重试按原索引生成。子运行冻结可空的 `batch_item_sequence`，不按完成顺序推算。`application/output_naming.py` 统一音频/文本的 exact 与 prefix_sequence 命名：缺模式旧图原名，新节点默认编号，普通单运行编号为 1；批次缺序号明确报错。配置保留用户前缀，artifact 和 ZIP 保留实际完整名称及扩展名，合法 128 字符前缀不截断。
- 本轮未引入数据库、外部 Worker、文件系统监听、混音或跨音频图执行。更大规模清单的持久化/查询性能与真实 Provider 分别评估，不以有界调度等同于所有大目录性能已经验证。

完整批次协议、状态和验收见[批处理规格](../spec/batch-processing.md)，接口索引见[多音频项目契约](voicetransl_multi_audio_project.md)。

## 8. 前端语言边界

前端通过 Vue I18n 的集中消息目录提供简体中文与英文，语言偏好仅保存在浏览器。切换语言更新展示和文档标题，不改变项目语言、图配置、接口请求、持久化结构或运行快照，也不触发重新获取项目或能力目录。具体交互见[前端工作流](frontend_workflow.md)。

后端错误码、节点类型、端口与配置契约仍是稳定的机器标识。前端按已知标识查找展示译文，同时保留原能力数据和服务端错误详情；未知扩展使用原始标签或诊断，不因缺少翻译被拒绝。当前不提供后端日志、API 文档或生成产物内容的自动翻译。

测试及交付要求见 [AGENTS.md](../AGENTS.md)。
