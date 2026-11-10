# VoiceTransl 多音频项目契约

本文维护当前项目、音频和 API 边界。工作流执行见[架构约定](architecture.md)，交互见[前端工作流](frontend_workflow.md)。旧固定阶段流程已退役，不再作为当前接口契约。

## 资源与目录

- 一个项目包含零个或多个音频；空项目可以先保存图，每个实际音频有独立输入、工作流运行及产物，不共享时间轴。
- `Project.source` 是可空的主音频兼容字段，`Project.audios` 包含 `{id, source, name}`。旧项目存在 `source` 时继续构造虚拟 `aud_primary`，不搬迁文件；空项目 `source=null`、`audios=[]`，随后上传只追加真实资源，不捏造主音频。`source_language/target_language` 是可选的旧项目语言元数据；新界面只提交名称，两字段保持 `null`，旧字段或缺少字段的项目均继续可读。
- 新增音频 ID 由服务端生成，不用用户文件名作路径。不同同名文件可以独立保存。
- `SourceAsset.origin` 可空；目录导入保存 `{root_id,root_name,relative_path}`，仅作来源和导出组织元数据，不改变实际路径。根名及相对路径必须可跨平台使用，相对路径 basename 与上传文件名一致；旧音频缺失时不补写。
- 项目级 `workflow-v4.json` 由各音频共用；每次运行捕获不可变快照，修改图不改动其他音频及历史运行。

```text
data/projects/<project-id>/
  project.json
  source/original.<ext>                    # 主音频
  workflow-v4.json                           # 首次保存后存在
  workflow-revisions-v4/<revision>.json
  batches-v4/<batch-id>.json                 # 固定图、文件清单与逐项状态
  workflow-runs-v4/<run-id>/                  # 主音频运行
    run.json
    nodes/<node-id>/<filename>
  audios/<audio-id>/
    source/original.<ext>
    workflow-runs-v4/<run-id>/
      run.json
      nodes/<node-id>/<filename>
  job.json / config.json / primary-config.json
  audios/<audio-id>/job.json / config.json  # 兼容记录，不是图运行配置
```

源路径保存为父项目相对 POSIX 路径。主音频使用原项目根目录，新增音频放在 `audios/{aid}/` 下；旧 cache/transcript/output 文件保留原位置，不自动删除、迁移或作为新节点结果使用。

## 当前 REST API

所有路径以 `/api/v1` 为前缀。图、运行和设置文档带自身版本；分页/批次摘要不重复附加schema字段，批次详情的workflow仍带4.0版本：

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

批次使用已上传的项目资源，清单 1–10000 个不同音频，首版同项目只有一个活动批次。输入可为 `{}` 上下文或显式 `audio_ids` 清单，后者要求首次请求与图内完整清单完全同序，否则 `BATCH_AUDIO_COLLECTION_MISMATCH`；固定 `audio_id` 仍返回 `BATCH_EXPLICIT_AUDIO_BINDING`。批次预留全部排队音频，冲突单运行返回 `AUDIO_BUSY`，删除项目仍返回 `PROJECT_BUSY`。批次快照不随公共图变化，失败重试创建新批次，保留完整图并运行失败子集；服务重启标记中断，不自动重发。新文件任务保存 1-based `sequence`，子运行保存可空 `batch_item_sequence`，重试保留原序号。完整字段、错误及状态见[批处理规格](../spec/batch-processing.md)和[输入清单规格](../spec/node-batch-input.md)。

图校验业务错误由 validate 返回 200/`valid=false`；无效请求或保存图返回 422，版本冲突返回 `409 WORKFLOW_REVISION_CONFLICT`，音频重复运行返回 `409 AUDIO_BUSY`。统一错误结构为 `{error:{code,message,details?}}`。

`audio_input.config.audio_id` 固定单资源，互斥的 `audio_ids` 保存有序唯一清单（最多 10000，允许空图编辑）。校验及保存拒绝外部或不存在的资源（`INPUT_AUDIO_NOT_FOUND`）。普通运行遇空清单返回 `INPUT_AUDIO_COLLECTION_EMPTY`、多项返回 `INPUT_AUDIO_COLLECTION`；单项或固定绑定与路由 `{aid}` 不一致返回 `INPUT_AUDIO_MISMATCH`。`{}` 使用该次运行的音频上下文。绑定或顺序改变生成新执行版本，不修改旧快照。

工作流节点可带可选布局字段 `size: {width, height}`；缺失或 `null` 使用内容默认值，宽高为有限正数且不超过 1,000,000。保存与快照保留此字段；仅修改尺寸影响文档 `revision`，不提升执行 `workflow_revision`。显示时仍由前端按当前内容的最小尺寸约束，不能据此缩小到遮挡控件。

产物以 `artifact_id/port/filename/media_type/size_bytes/sha256/download_url` 描述，不从文件名推断业务类型。`naming_mode` 默认 prefix_sequence，显式 exact 保持原名；prefix_sequence 生成 `前缀_00001` 加实际扩展名；普通运行编号 1，批次沿用固定序号，缺失有效序号报 `OUTPUT_SEQUENCE_UNAVAILABLE`。配置及 effective_config 保留前缀，artifact/ZIP 使用完整实际名。输出名称拒绝路径、控制字符、危险字符、Windows 保留名称及尾点等；各运行/节点目录隔离，不覆盖历史或源文件。

## 配置节点与冻结

图、运行、批次及全局设置为schema 4.0；项目/音频/Transcript仍1.0，API路径不变，能力目录为1.18。工作流保存/校验请求及带版本的设置请求提交当前版本；各自嵌套快照严格校验。业务JSON内的schema_version不当文档版本解析。

llm_service/asr_service 分别拥有 endpoint/credential_ref，ASR 另有识别 protocol，无 region；llm_model/asr_model 仅接对应族连接，提供自由 model_name 及有界 additional_parameters。typed端口只传公开描述和不透明引用，真实Key在调用边界解密，不能直接写入config。

Qwen config为空，仅接音频、ASR模型及执行参数，无上下文；LLM config.context 是多行字符串，context 参数口可选，执行使用覆盖后的非空白实际值。文本拼接items=[{id,value},...]保存1–32个稳定ID项，顺序决定输出、text_<id>决定连线；dynamic_inputs派生动态端口和字段。参数字段通过input_port/input_type_id声明，可空值通过nullable声明，前端不猜用途。总超时在独立执行参数，未连接时使用对应执行参数数据模型的声明默认值。

凭据写入仍通过工作流PUT的credential_updates，set/clear/use_default配合稳定request_id，归属绑定project/连接owner/provider_type。多消费者显式连同一模型可复用Key；复制连接/导出剥离ref。默认Key只在新连接保存时按捕获版本物化，执行/批次重试不读最新全局设置。

运行提交前纯求值可用配置、验证模型协议及非空上下文等，再创建工作。批次按每个audio分别检查；ASR逐audio继承资源语言，翻译读取当前文本元数据且输出语言取模型有效响应，不能把首个文件的解析值写入所有快照。模型附加字段默认不发，不能覆盖核心输入、认证和路由。

文本以metadata.current_text声明当前字段及语言；翻译、预览和导出都消费直接上游，保留原文、时间轴和ID。重跑自动物化纯配置祖先，以新递归指纹验证处理缓存；必要处理上游失效才要求前移起点，不自动追加云调用。

GET /capabilities的languages及suggestion_source只为搜索编辑，传输保存代码，未知代码不因此拒绝。连接缺失、归属错误、协议不兼容和上下文无效返回稳定安全错误，不回显原始Key或请求内容；Streaming内容校验在握手前完成。详见[服务及字段输入规格](../spec/service-families-field-inputs.md)和[节点连接](node_connections.md)。

## 新旧存储边界

新图存workflow-v4.json，修订、运行、批次使用各自v4目录，全局模板存settings-v4.json。旧图/运行/批次/模板不读取、不迁移、不转换，既有文件不删除；新列表只列新记录。项目和音频资源保持原格式及目录，没有新图的项目返回当前直通默认图。

旧固定阶段的资源级摘要仍不作为节点执行入口；本轮不改变项目和音频的资源API，不批量改写原数据。

## 不变量与限制

同一音频最多一个运行/排队任务，其他音频独立排队并共享全局并发。任一成员忙碌使整个项目不可删除。产物保存原子化，取消/关闭必须等执行资源回收。源文件、人工字幕、旧记录和其他运行不得被静默覆盖。

节点重跑创建独立运行，保存 `rerun` 来源及执行/复用/未参与列表；节点新增可省略的 `execution_kind/reused_from/checkpoint`，源身份保存实际哈希及相关元数据。checkpoint 文件位于自身运行 `.checkpoints/{node_id}/`，完整输出不塞进运行 JSON，当前4.0记录的可省略字段不自动补写。多项输入清单允许目标音频属于清单的单项重跑；原批次的条目、计数、运行 ID 和 ZIP 不变，新运行 `batch_id=null` 且保留原 `batch_item_sequence`。请求 ID 相同且内容相同返回原运行，内容不同返回 `WORKFLOW_REQUEST_CONFLICT`；必要上游缺失、变化或损坏通过 `RERUN_*` issue 拒绝并指出前移节点，见[重跑规格](../spec/node-rerun-reuse.md)。

服务关闭后拒绝新的调度，返回 `503 WORKFLOW_STOPPING`，原请求仍可返回已发布记录。关闭准备中的提交会回收读取/复制任务；已经发布的记录落为中断，不能留下无执行任务的排队记录。

当前没有音频删除/重排、跨音频上下文、合并时间轴、整批节点重跑、自动缓存调度或历史清理。验证覆盖多音频上传与重试、固定快照批次、运行隔离、忙碌删除、版本冲突、产物下载、取消与重启状态，以及旧记录原字节保留且不读取或迁移；目录规模的性能限制见批处理规格。
