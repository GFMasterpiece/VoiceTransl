# VoiceTransl 多音频项目契约

本文维护当前项目、音频和 API 边界。工作流执行见[架构约定](architecture.md)，交互见[前端工作流](frontend_workflow.md)。旧固定阶段流程已退役，不再作为当前接口契约。

## 资源与目录

- 一个项目包含一个或多个音频；每个音频有独立输入、工作流运行及产物，不共享时间轴。
- `Project.source` 是主音频兼容字段，`Project.audios` 包含 `{id, source, name}`。主音频 ID 为 `aud_primary`；旧项目缺少成员列表时构造虚拟主音频，不搬迁文件。
- 新增音频 ID 由服务端生成，不用用户文件名作路径。不同同名文件可以独立保存。
- 项目级 `workflow.json` 由各音频共用；每次运行捕获不可变快照，修改图不改动其他音频及历史运行。

```text
data/projects/<project-id>/
  project.json
  source/original.<ext>                    # 主音频
  workflow.json                           # 首次保存后存在
  workflow-revisions/<revision>.json
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
| `GET /projects` | `limit` 默认 20、最大 100，`offset` 默认 0；按更新时间/ID 倒序稳定分页，返回 `{items,total,limit,offset}` |
| `POST /projects` | multipart 必填 `file/source_language/target_language`，可选 `name`；201 返回 `{project,job}`，只上传不运行 |
| `GET /projects/{pid}` | 项目与音频成员元数据 |
| `POST /projects/{pid}/audios` | multipart 必填 `file/source_language`，可选 `name`；201 返回 `{audio,job}`，只追加不运行 |
| `GET /projects/{pid}/audios` | `{items:[{id,source,name,job}]}`，Job 是旧格式只读摘要，不是当前节点运行状态 |
| `DELETE /projects/{pid}` | 删除整个项目，任一音频运行/排队时返回 `409 PROJECT_BUSY` |
| `GET /projects/{pid}/workflow` | 项目工作流，无已保存图时返回默认音频直通图 |
| `PUT /projects/{pid}/workflow` | `{expected_revision,nodes,edges}`，保存有效图并返回新修订 |
| `POST /projects/{pid}/workflow/validate` | `{nodes,edges}`，返回 `{valid,errors}`，错误关联节点/边 |
| `POST /projects/{pid}/audios/{aid}/workflow-runs` | `{workflow_revision}`，202 返回绑定该音频的新运行 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs` | 分页历史摘要 `{items,total,limit,offset}`，默认 20、最大 100 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}` | 不可变图快照、节点状态及 artifact |
| `POST /projects/{pid}/audios/{aid}/workflow-runs/{rid}/cancel` | 提交活动运行取消；终态幂等返回已有记录 |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}` | 校验文件存在后返回结构化 artifact |
| `GET /projects/{pid}/audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/download` | 下载该节点的独立文件 |

创建时语言不能为空、有首尾空白或控制字符，长度最多 32；源/目标大小写不敏感不能相同。区域语言代码可用，不猜测默认语言。`preset` 不再是创建参数或执行选择。

上传规则来自 `GET /upload-policy`。文件先进入临时目录，文件与元数据完整后发布；追加失败不删除成功音频或父项目。追加和删除在项目锁下串行化。

图校验业务错误由 validate 返回 200/`valid=false`；无效请求或保存图返回 422，版本冲突返回 `409 WORKFLOW_REVISION_CONFLICT`，音频重复运行返回 `409 AUDIO_BUSY`。统一错误结构为 `{error:{code,message,details?}}`。

产物以 `artifact_id/port/filename/media_type/size_bytes/sha256/download_url` 描述，不从文件名推断业务类型。输出名称拒绝路径、控制字符、危险字符、Windows 保留名称及尾点等；各运行/节点目录隔离，即使同名也不覆盖历史或源文件。

## 旧数据兼容边界

- `GET /projects/{pid}/config` 读取旧公共配置；逐音频 `/config` 读取对应旧覆盖。主音频优先 `primary-config.json`，再根 `config.json`，缺失时使用兼容默认值；其他音频优先自身配置，再适配语言的公共默认。
- 旧 `/config` 的 PUT 已退役，只返回 `LEGACY_CONFIG_READ_ONLY`，不保存请求内容。节点图不读取这些配置。
- `GET /projects/{pid}/job` 及逐音频 `/job` 保留旧 Job 摘要读取。创建仍保存初始兼容 Job/语言配置，但不再通过固定阶段执行，也不将旧 Job 当作节点运行。
- 旧 `run/cancel/stages/transcript/outputs` 和 `audios/run` 入口已删除。旧 WebSocket 保留 Job snapshot 兼容协议，不发送节点运行进度；新前端使用 REST。
- 旧配置、Job 和图运行快照不批量改写；旧非 Mock Provider/参数不再可执行，用户需显式编辑当前图后重新运行，不自动转成 Mock。
- 所有持久文档显式要求当前 `schema_version=1.0`，含嵌套快照；缺失或不同版本拒绝读取且不改写原文件。不实现跨版本迁移。
- 服务启动会按既有恢复规则将未结束记录标记为 `interrupted`，不自动执行；节点运行不能继续旧快照，只能创建新运行。

## 不变量与限制

同一音频最多一个运行/排队任务，其他音频独立排队并共享全局并发。任一成员忙碌使整个项目不可删除。产物保存原子化，取消/关闭必须等执行资源回收。源文件、人工字幕、旧记录和其他运行不得被静默覆盖。

当前没有批量图运行 API、音频删除/重排、跨音频上下文、合并时间轴、指定节点重跑或历史清理。验证需覆盖多音频上传与重试、运行隔离、忙碌删除、版本冲突、产物下载、取消与重启状态，以及旧记录只读且不迁移。
