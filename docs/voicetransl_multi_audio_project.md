# VoiceTransl 多音频项目契约

本文是当前项目/音频资源、目录与 API 的契约，不采用“一个项目一个任务”或“每个上传文件创建独立项目”的旧假设。五阶段 Pipeline、Provider 职责、原子写入与恢复规则见[架构约定](architecture.md)，交互见[前端工作流](frontend_workflow.md)。

## 1. 资源与作用范围

一个项目包含一个或多个音频。用户上传一组音频到同一项目，在项目中选择一个、多个或全部音频，配置流程后开始处理。项目组织资源并提供公共默认配置；每个音频拥有独立任务、进度、字幕、时间轴和输出。

“共同处理”指向选中的音频应用同一套配置，再启动各自任务。不拼接源文件、不合并时间轴或输出；跨音频联合翻译上下文需后续单独设计。

- `Project.source` 保留为主音频兼容字段；`Project.audios` 包含 `{id, source, name}`，`name` 可为空。
- 主音频固定为 `aud_primary`，使用原项目根目录的资源。加载缺少 `audios` 的旧项目时构造虚拟主音频，不迁移文件。
- 新增音频 ID 由后端生成，不能使用文件名作路径。不同同名文件可以作为不同资源保存。
- `Job.project_id` 始终为父项目 ID，`Job.audio_id` 标识所属音频；旧 Job 缺少该字段时默认为 `aud_primary`。
- 每个音频只保存一个当前 Job；重新处理产生新 Job ID，不意味着保存历史任务/结果档案。
- 源文件及人工字幕不被静默覆盖，重跑仅按既有规则重建当前音频产物。

## 2. 磁盘布局与配置

```text
data/projects/<project-id>/
├── project.json                  # 父项目元数据与音频成员
├── config.json                   # 公共默认配置 / 旧主音频配置回退
├── primary-config.json           # aud_primary 显式覆盖（保存后存在）
├── job.json                      # aud_primary 当前任务
├── source/ cache/ transcript/ output/ logs/  # 主音频原路径
└── audios/<audio-id>/
    ├── config.json               # 该音频显式覆盖（保存后存在）
    ├── job.json
    └── source/ cache/ transcript/ output/ logs/
```

新增音频源路径保存为父项目相对 POSIX 路径，例如 `audios/aud_.../source/original.wav`。任务 artifact 同样相对父项目，不能将不同音频的同名 `zh.srt` 写到同一路径。

音频没有独立覆盖时读取父项目公共默认配置；新增音频将 `source_language`、`asr.language` 适配为该音频语言。适配后若源语言等于目标语言，配置无效，需显式保存该音频覆盖，不能静默改目标语言。逐音频保存配置只产生该音频覆盖，此后修改公共默认配置不会覆盖该文件。

主音频的有效配置优先级为根 `primary-config.json` → 根 `config.json` → Mock 默认；新增音频为 `audios/{aid}/config.json` → 适配语言的公共默认。`PUT /projects/{pid}/audios/aud_primary/config` 保存 `primary-config.json`，不修改公共默认或其他音频。旧 `GET/PUT /projects/{pid}/config` 继续读取/保存根公共 `config.json`，也是没有覆盖时的兼容回退；已有主音频覆盖后，修改该旧配置路径不覆盖主音频有效配置。启动主音频始终读取其有效覆盖优先级。旧文件保持原位置，不迁移已有配置。

新任务启动时使用最终有效配置生成不可变 `job.config_snapshot`。失败、取消或中断任务以 `resume=true` 继续时使用旧快照；`resume=false` 重新处理创建新 Job ID 与快照。运行、取消中或排队音频配置不可修改。

## 3. REST 与事件

全部路径以 `/api/v1` 为前缀。

| 方法与路径 | 语义 |
| --- | --- |
| `POST /projects` | multipart：`file`、`name`、`source_language`、`preset`；创建父项目及首个音频，201 返回 `{project, job}`，不启动 |
| `POST /projects/{pid}/audios` | multipart：`file`、可选 `source_language`（默认 `ja`）、`name`；添加音频，201 返回 `{audio, job}`，不启动 |
| `GET /projects/{pid}/audios` | 返回 `{items: [{id, source, name, job}]}`，包含主音频和所有新增音频 |
| `GET/PUT /projects/{pid}/audios/{aid}/config` | 读取有效配置 / 保存该音频配置，规则与旧配置接口一致 |
| `GET /projects/{pid}/audios/{aid}/job` | 该音频当前 Job |
| `GET /projects/{pid}/audios/{aid}/stages/{stage}` | 当前任务的单阶段状态、任务配置快照和对应真实产物 |
| `POST /projects/{pid}/audios/{aid}/run` | `{resume: true/false}`，202 返回 `{job_id, status}` |
| `POST /projects/{pid}/audios/{aid}/cancel` | 只取消该音频 |
| `GET /projects/{pid}/audios/{aid}/transcript` | 该音频最完整的已完成字幕阶段 |
| `GET /projects/{pid}/audios/{aid}/outputs` | 该音频输出列表，下载 URL 指向该音频 |
| `GET /projects/{pid}/audios/{aid}/outputs/{filename}` | 该音频输出下载，严格校验路径 |
| `POST /projects/{pid}/audios/run` | 批量启动选中的音频，见下文 |
| `DELETE /projects/{pid}` | 删除整个父项目及所有音频；任一音频忙碌返回 409 `PROJECT_BUSY` |

阶段详情响应为 `{stage, job_id, audio_id, state, config, result, result_error}`。`config` 是当前任务该阶段的不可变快照，无快照时为 `null`，与可编辑的下次处理配置分开。仅当前任务该阶段 `completed` 时读取对应产物：预处理为 `{kind: "preprocess", data: metadata}`，识别/对齐/翻译为 `{kind: "transcript", transcript}`（分别是 raw/aligned/translated），导出为 `{kind: "outputs", items}`。未完成、失败或跳过时 `result` 为 `null`；产物缺失或不可读时通过 `result_error: {code, message}` 提示，不回退到别的阶段或旧任务结果。

批量启动请求示例：

```json
{"audio_ids": ["aud_primary", "aud_..."], "resume": false}
```

`audio_ids` 非空且不能重复；开始任何任务前先校验所有 ID 属于该项目。无效集合整体拒绝，未知音频返回 404 `AUDIO_NOT_FOUND`，空/重复列表返回 422。有效集合依次提交独立任务，202 返回逐项结果，不保证全部成功或事务回滚：

```json
{"items": [
  {"audio_id": "aud_primary", "job_id": "job_...", "status": "pending"},
  {"audio_id": "aud_...", "error": {"code": "JOB_ALREADY_RUNNING", "message": "任务正在运行或排队中"}}
]}
```

该启动 API 不隐式保存配置。前端检查配置并通过各音频 `PUT .../config` 应用到选择范围，只有保存成功的音频才按新配置启动；未选音频不被修改。继续旧任务使用旧快照，需明确与“按新配置重新处理”区分。

WebSocket `/projects/{pid}/audios/{aid}/events` 先发送该音频 `job.snapshot`，后续仅通知该音频事件。事件同时包含父 `project_id`、`audio_id` 与 `job_id`。`/projects/{pid}/events` 默认首先发送主音频快照，后续可收到全项目事件；也可用 `?audio_id=...` 筛选。重连后通过 REST 恢复对应音频状态，事件不是持久状态或历史重放。

## 4. 兼容与不变量

- 旧 `/projects/{pid}/job|run|cancel|transcript|outputs` 路径保留，操作主音频，不暗中操作全部音频。旧 `/projects/{pid}/config` 保留为公共默认与主音频配置回退；逐音频配置路径操作独立覆盖，包含主音频。
- `schema_version` 保持兼容的 `1.0`，新增字段提供旧数据默认值，无强制迁移。项目仍是可独立复制的完整目录。
- 上传每项遵守同一 `upload-policy`，先写临时资源，完整后发布成员；失败不删除已有项目或成功音频。并发添加与删除须串行化成员变更。
- 同一音频最多一个运行/排队任务；不同音频可独立排队并遵守全局并发限制。
- 项目删除检查所有成员，任一运行、取消中或已提交排队的任务都使父项目忙碌。
- 重启扫描所有音频，将运行、取消中和已提交排队任务标记为 `interrupted`，不自动启动。
- Providers 不直接写状态；字幕 ID、源文和时间轴不能由 Translator 改动。单项失败不取消其他音频。
- 不修改既有运行数据，不自动合并历史上已创建的独立项目。音频删除、重排、跨音频上下文与统一时间轴不属于本次最小功能。

验证须覆盖旧项目打开与旧接口、同项目添加多个音频、选中范围配置/启动、逐项失败与独立输出、快照继续/重跑、忙碌删除与重启恢复。
