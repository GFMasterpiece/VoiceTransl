# VoiceTransl 多音频项目契约

本文维护当前项目、音频和 API 边界。工作流执行见[架构约定](architecture.md)，交互见[前端工作流](frontend_workflow.md)。旧固定阶段流程已退役，不再作为当前接口契约。

## 资源与目录

- 一个项目包含零个或多个音频；空项目可以先保存图，每个实际音频有独立输入、工作流运行及产物，不共享时间轴。
- `Project.source` 是可空的主音频兼容字段，`Project.audios` 包含 `{id, source, name}`。旧项目存在 `source` 时继续构造虚拟 `aud_primary`，不搬迁文件；空项目 `source=null`、`audios=[]`，随后上传只追加真实资源，不捏造主音频。`source_language/target_language` 是可选的旧项目语言元数据；新界面只提交名称，两字段保持 `null`，旧字段或缺少字段的项目均继续可读。
- 新增音频 ID 由服务端生成，不用用户文件名作路径。不同同名文件可以独立保存。
- `SourceAsset.origin` 可空；目录导入保存 `{root_id,root_name,relative_path}`，仅作来源和导出组织元数据，不改变实际路径。根名及相对路径必须可跨平台使用，相对路径 basename 与上传文件名一致；旧音频缺失时不补写。
- 项目级 `workflow.json` 由各音频共用；每次运行捕获不可变快照，修改图不改动其他音频及历史运行。

```text
data/projects/<project-id>/
  project.json
  source/original.<ext>                    # 主音频
  workflow.json                           # 首次保存后存在
  workflow-revisions/<revision>.json
  batches/<batch-id>.json                 # 固定图、文件清单与逐项状态
  workflow-runs/<run-id>/                  # 主音频运行
    run.json
    nodes/<node-id>/<filename>
  audios/<audio-id>/
    source/original.<ext>
    workflow-runs/<run-id>/
      run.json
      nodes/<node-id>/<filename>
  job.json / config.json / primary-config.json
  audios/<audio-id>/job.json / config.json  # 兼容记录，不是图运行配置
```

源路径保存为父项目相对 POSIX 路径。主音频使用原项目根目录，新增音频放在 `audios/{aid}/` 下；旧 cache/transcript/output 文件保留原位置，不自动删除、迁移或作为新节点结果使用。

## 当前 REST API

所有路径以 `/api/v1` 为前缀：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /settings` | 全局修订、并发配置及其字段声明、各节点创建默认字段/值和密钥状态；不返回明文 Key |
| `PUT /settings` | `{expected_revision,request_id,global,node_defaults,credential_updates?}`，原子保存并发及新建节点模板；默认密钥只写更新 |
| `GET /upload-policy` | `{allowed_extensions,max_upload_bytes,upload_concurrency}`，上传并发独立于工作流执行并发 |
| `GET /projects` | `limit` 默认 20、最大 100，`offset` 默认 0；按更新时间/ID 倒序稳定分页，返回 `{items,total,limit,offset}` |
| `POST /projects` | 旧上传创建兼容接口：multipart 必填 `file/source_language/target_language`，可选 `name`；201 返回 `{project,job}`，只上传不运行 |
| `POST /projects/empty` | JSON 仅必填 `name`（去除首尾空白后长度 1–200）；201 返回 `{project,job:null}`。可选 `source_language/target_language` 为旧客户端保留，省略或 `null` 时不猜测默认值 |
| `GET /projects/{pid}` | 项目与音频成员元数据 |
| `POST /projects/{pid}/audios` | multipart 必填 `file/source_language`，可选 `name` 及全有或全无的 `origin_root_id/origin_root_name/origin_relative_path`；201 返回 `{audio,job}`，只追加不运行 |
| `GET /projects/{pid}/audios` | `{items:[{id,source,name,job}]}`，Job 是旧格式只读摘要，不是当前节点运行状态 |
| `GET /projects/{pid}/audios/{aid}/source` | 按项目资源归属安全读取原音频，inline 响应支持 Range，使用可信 MIME 和 `nosniff` |
| `DELETE /projects/{pid}` | 删除整个项目，任一音频运行/排队时返回 `409 PROJECT_BUSY` |
| `GET /projects/{pid}/workflow` | 项目工作流，无已保存图时返回默认音频直通图 |
| `PUT /projects/{pid}/workflow` | `{expected_revision,nodes,edges,request_id?,credential_updates?}`，保存有效图并返回新修订；密钥更新为只写，附稳定请求 ID |
| `POST /projects/{pid}/workflow/validate` | `{nodes,edges}`，返回 `{valid,errors}`，错误关联节点/边 |
| `POST /projects/{pid}/audios/{aid}/workflow-runs` | `{workflow_revision}` 全图运行；可加 `rerun:{source_run_id,from_node_id}` 和必填稳定 `request_id` 创建节点重跑；202 返回新运行 |
| `POST /projects/{pid}/audios/{aid}/workflow-runs/plan` | `{workflow_revision,rerun}`，只读预检返回 `valid/execute_node_ids/reuse_node_ids/skipped_node_ids/issues` |
| `GET /projects/{pid}/audios/{aid}/workflow-runs` | 分页历史摘要 `{items,total,limit,offset}`，默认 20、最大 100 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}` | 不可变图快照、节点状态及 artifact |
| `POST /projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel` | 提交活动运行取消；终态幂等返回已有记录 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}` | 校验文件存在后返回结构化 artifact |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/download` | 下载该节点的独立文件 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/content` | 校验归属与文件存在后 inline 读取该节点缓存，供文本或音频预览，支持 Range 和 `nosniff` |
| `POST /projects/{pid}/batches` | `{workflow_revision,audio_ids,request_id}`，202 返回固定工作流的新批次摘要；相同 key/载荷返回原批次 |
| `GET /projects/{pid}/batches` | 分页批次历史，默认 20、最大 100 |
| `GET /projects/{pid}/batches/{bid}` | 批次详情及固定工作流快照 |
| `GET /projects/{pid}/batches/{bid}/items` | 分页文件任务、状态、运行 ID 及来源 |
| `POST /projects/{pid}/batches/{bid}/cancel` | 停止派发并请求取消活动运行；终态幂等 |
| `POST /projects/{pid}/batches/{bid}/retry` | `{request_id,include_cancelled:false}`，以原快照为失败/中断项新建批次，可明确包含取消项 |
| `GET /projects/{pid}/batches/{bid}/download` | ZIP 及结构化结果清单，按来源/文件任务/节点隔离命名 |

空项目创建不要求语言；旧客户端显式提供的语言字符串仍不能空白、有首尾空白或控制字符，长度最多 32，两者均提供时大小写不敏感不能相同。旧上传创建接口仍必填两种语言；追加音频只要求该资源的源语言，不要求项目目标语言。区域语言代码可用，不猜测默认语言。`preset` 不再是创建参数或执行选择。

上传规则来自 `GET /upload-policy`。文件先进入临时目录，文件与元数据完整后发布；追加失败不删除成功音频或父项目。追加和删除在项目锁下串行化。

批次使用已上传的项目资源，清单 1–10000 个不同音频，首版同项目只有一个活动批次。输入可为旧 `{}` 上下文或显式 `audio_ids` 清单，后者要求首次请求与图内完整清单完全同序，否则 `BATCH_AUDIO_COLLECTION_MISMATCH`；固定 `audio_id` 仍返回 `BATCH_EXPLICIT_AUDIO_BINDING`。批次预留全部排队音频，冲突单运行返回 `AUDIO_BUSY`，删除项目仍返回 `PROJECT_BUSY`。批次快照不随公共图变化，失败重试创建新批次，保留完整图并运行失败子集；服务重启标记中断，不自动重发。新文件任务保存 1-based `sequence`，子运行保存可空 `batch_item_sequence`，重试保留原序号。完整字段、错误及状态见[批处理规格](../spec/batch-processing.md)和[输入清单规格](../spec/node-batch-input.md)。

图校验业务错误由 validate 返回 200/`valid=false`；无效请求或保存图返回 422，版本冲突返回 `409 WORKFLOW_REVISION_CONFLICT`，音频重复运行返回 `409 AUDIO_BUSY`。统一错误结构为 `{error:{code,message,details?}}`。

`audio_input.config.audio_id` 固定单资源，互斥的 `audio_ids` 保存有序唯一清单（最多 10000，允许空图编辑）。校验及保存拒绝外部或不存在的资源（`INPUT_AUDIO_NOT_FOUND`）。普通运行遇空清单返回 `INPUT_AUDIO_COLLECTION_EMPTY`、多项返回 `INPUT_AUDIO_COLLECTION`；单项或固定绑定与路由 `{aid}` 不一致返回 `INPUT_AUDIO_MISMATCH`。旧 `{}` 仍使用该次运行的音频上下文。绑定或顺序改变生成新执行版本，不修改旧快照；读取旧图不补字段。

工作流节点可带可选布局字段 `size: {width, height}`；缺失或 `null` 使用内容默认值，宽高为有限正数且不超过 1,000,000。保存与快照保留此字段；仅修改尺寸影响文档 `revision`，不提升执行 `workflow_revision`。显示时仍由前端按当前内容的最小尺寸约束，不能据此缩小到遮挡控件。

产物以 `artifact_id/port/filename/media_type/size_bytes/sha256/download_url` 描述，不从文件名推断业务类型。`naming_mode` 缺失或 exact 保持原名，prefix_sequence 生成 `前缀_00001` 加实际扩展名；普通运行编号 1，批次沿用固定序号，缺失有效序号报 `OUTPUT_SEQUENCE_UNAVAILABLE`。配置及 effective_config 保留前缀，artifact/ZIP 使用完整实际名。输出名称拒绝路径、控制字符、危险字符、Windows 保留名称及尾点等；各运行/节点目录隔离，不覆盖历史或源文件。

## 云端连接快照

`qwen_asr` 与 Mock 独立，沿用同一单运行和批次接口。节点保存自身 `base_url/timeout_seconds/region/credential_ref` 以及模型、语言及附加参数；实际 Key 经 `credential_updates` 的 set/clear 存入独立加密存储，不进入 config。新节点还可显式提交 `use_default` 和创建时捕获的默认密钥版本，由后端物化独立节点凭据。校验允许无密钥草稿，运行前必须有本节点有效引用，不读其他节点或环境变量。引用绑定项目/节点/类型，替换生成新版本，稳定请求 ID 重试不得重复轮换。

全局 `/provider-profiles` CRUD 已删除，旧连接文件不再加载。Filetrans 的 `provider_profile_id/connection_mode/api_key_env` 不再接受；含这些字段的旧图可以读取，但保存、运行及重试须明确拒绝，需删除后重新添加节点。原文件不自动改写；历史 `provider_profiles_snapshot` 仅作为不参与执行的原始元数据保留，不补读最新连接。

`qwen_asr_streaming` 复用相同保存、单运行和批次 API，无新增上传路由，保存 `endpoint/timeout_seconds/credential_ref` 及模型、语言和附加参数。Provider 类型和凭据归属各自独立。批次与重试仅使用原图及凭据版本；持久化仍 `1.0`，当前能力目录为 `1.14`。

`GET /capabilities` 顶层发布 `languages:[{code,names:{"zh-CN",en},native_name,aliases}]`，字段以 `suggestion_source:"languages"|null` 声明编辑候选。该清单不改变上传/配置的语言验证，不进入图和历史；传输继续只用原字符串代码，清单外的代码不因此被拒绝。源语言仍必填，云识别的空语言仍继承音频声明，Mock 保留非空校验及默认 `ja`。

运行提交时连接/凭据缺失或归属不符返回安全稳定错误，旧连接字段使用 `LEGACY_CONNECTION_UNSUPPORTED` 或同说明的配置校验错误。Streaming WAV 内容检查在执行时、握手前完成，使用 `QWEN_STREAM_*` 错误，不自动转码。取消详情可保存在节点 error 中，最终 cancelled 不代表远端终止，见[节点连接](node_connections.md)。

## 旧数据兼容边界

- `GET /projects/{pid}/config` 读取旧公共配置；逐音频 `/config` 读取对应旧覆盖。主音频优先 `primary-config.json`，再根 `config.json`，缺失时使用兼容默认值；其他音频优先自身配置，再适配语言的公共默认。
- 旧 `/config` 的 PUT 已退役，只返回 `LEGACY_CONFIG_READ_ONLY`，不保存请求内容。节点图不读取这些配置。
- `GET /projects/{pid}/job` 及逐音频 `/job` 保留旧 Job 摘要读取。创建仍保存初始兼容 Job/语言配置，但不再通过固定阶段执行，也不将旧 Job 当作节点运行。
- 空项目没有主音频或旧 Job，主 `/job` 返回 404；缺省 `/config` 继续按旧语言字段或旧默认值生成只读兼容值，不因此写文件，也不补写项目语言。节点执行不读取旧配置，节点上传的实际音频仍拥有各自初始兼容 Job。
- 旧 `run/cancel/stages/transcript/outputs` 和 `audios/run` 入口已删除。旧 WebSocket 保留 Job snapshot 兼容协议，不发送节点运行进度；新前端使用 REST。
- 旧配置、Job 和图运行快照不批量改写；旧非 Mock Provider/参数不再可执行，用户需显式编辑当前图后重新运行，不自动转成 Mock。
- 所有持久文档显式要求当前 `schema_version=1.0`，含嵌套快照；缺失或不同版本拒绝读取且不改写原文件。不实现跨版本迁移。
- 服务启动会按既有恢复规则将未结束记录标记为 `interrupted`，不自动执行；节点运行不能继续旧快照，只能创建新运行。

## 不变量与限制

同一音频最多一个运行/排队任务，其他音频独立排队并共享全局并发。任一成员忙碌使整个项目不可删除。产物保存原子化，取消/关闭必须等执行资源回收。源文件、人工字幕、旧记录和其他运行不得被静默覆盖。

节点重跑创建独立运行，保存 `rerun` 来源及执行/复用/未参与列表；节点新增可省略的 `execution_kind/reused_from/checkpoint`，源身份保存实际哈希及相关元数据。checkpoint 文件位于自身运行 `.checkpoints/{node_id}/`，完整输出不塞进运行 JSON，旧历史缺字段不补写。多项输入清单允许目标音频属于清单的单项重跑；原批次的条目、计数、运行 ID 和 ZIP 不变，新运行 `batch_id=null` 且保留原 `batch_item_sequence`。请求 ID 相同且内容相同返回原运行，内容不同返回 `WORKFLOW_REQUEST_CONFLICT`；必要上游缺失、变化或损坏通过 `RERUN_*` issue 拒绝并指出前移节点，见[重跑规格](../spec/node-rerun-reuse.md)。

服务关闭后拒绝新的调度，返回 `503 WORKFLOW_STOPPING`，原请求仍可返回已发布记录。关闭准备中的提交会回收读取/复制任务；已经发布的记录落为中断，不能留下无执行任务的排队记录。

当前没有音频删除/重排、跨音频上下文、合并时间轴、整批节点重跑、自动缓存调度或历史清理。验证覆盖多音频上传与重试、固定快照批次、运行隔离、忙碌删除、版本冲突、产物下载、取消与重启状态，以及旧记录只读且不迁移；目录规模的性能限制见批处理规格。
