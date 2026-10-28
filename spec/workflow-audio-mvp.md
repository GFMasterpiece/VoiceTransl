# 节点工作流第一里程碑：音频直通

> 状态：Implemented（2026-10-28 验收；仅限本文件的音频直通里程碑，完整编辑器规格仍为 Draft）。当前用法见 [前端工作流](../docs/frontend_workflow.md#7-节点工作流首版)，持久化与运行边界见 [架构约定](../docs/architecture.md#7-节点工作流首版)。

## 范围

在现有项目工作区增加独立的节点工作流入口，完成“音频输入 → 音频输出”的编辑、保存、执行及历史产物下载。不替换旧五阶段 Pipeline，不迁移旧任务/配置，不修改既有运行数据。

首版仅提供一个 `audio_input` 节点和一个或多个 `audio_output` 节点，端口类型均为 `audio`；允许输入扇出到多个输出。输入绑定当前所选音频，不接受客户端文件路径。输出按原字节复制为独立文件，不转码、不用源文件引用或链接代替副本。

## 非目标

本里程碑不实现 ASR/翻译等处理节点、视频处理、云端识别、通用汇合调度、强制重跑、历史产物清理或全局模板。输出历史默认保留。完整编辑器后续能力及待决策项见 [workflow-editor.md](workflow-editor.md)。

## 工作流数据与校验

项目工作流响应包含 `schema_version`、`project_id`、`revision`、`workflow_revision`、`updated_at`、`nodes` 和 `edges`：

```json
{
  "nodes": [
    {"id": "input", "type": "audio_input", "config": {}, "position": {"x": 80, "y": 120}},
    {"id": "output", "type": "audio_output", "config": {"filename_stem": "audio-copy"}, "position": {"x": 420, "y": 120}}
  ],
  "edges": [
    {"id": "input-output", "source": "input", "source_port": "audio", "target": "output", "target_port": "audio"}
  ]
}
```

- `revision` 是乐观锁版本；未保存默认图为 0，第一次保存建立版本 1，此后实际变化才递增。完全相同的重复保存不生成版本。
- `workflow_revision` 是执行版本，仅执行语义变更时递增；节点位置、节点/边数组排序、仅边 ID 的变化不使输出失效。
- 输出的 `filename_stem` 为无路径的安全文件名主体，默认 `audio-copy`；实际文件名补上输入音频的后缀。不同输出节点的文件按节点目录隔离，不覆盖同名产物。
- 保存有效图：恰好一个输入、至少一个输出；输出恰好有一条入边；节点/边 ID 唯一、端点存在、方向/类型合法、无环、无孤立节点。拒绝未知节点类型和危险参数；节点和边各最多 64 个，并限制字段长度。
- 客户端可编辑不完整草稿；校验可定位到节点/边。保存和启动无效图均被拒绝。
- 无已保存工作流时，GET 返回默认直通图；不得因此改写旧 Job。

## API 契约

以下路径均以 `/api/v1/projects/{pid}` 为前缀：

| 方法与路径 | 请求/响应 |
| --- | --- |
| `GET /workflow` | 当前工作流完整响应 |
| `PUT /workflow` | `{expected_revision, nodes, edges}`，返回保存后的完整工作流 |
| `POST /workflow/validate` | `{nodes, edges}`，返回 `{valid, errors: [{code, message, node_id?, edge_id?}]}` |
| `POST /audios/{aid}/workflow-runs` | `{workflow_revision}`，202 返回新的完整运行记录 |
| `GET /audios/{aid}/workflow-runs` | `{items, total, limit, offset}`，按音频列历史摘要 |
| `GET /audios/{aid}/workflow-runs/{rid}` | 完整运行记录与节点状态/产物 |
| `POST /audios/{aid}/workflow-runs/{rid}/cancel` | 活动运行返回 202 和运行记录；终态幂等返回原记录 |
| `GET /audios/{aid}/workflow-runs/{rid}/outputs/{node_id}` | 输出元数据 |
| `GET /audios/{aid}/workflow-runs/{rid}/outputs/{node_id}/download` | 下载独立输出文件 |

业务图校验错误由 validate 返回 200 / `valid=false`；无效请求结构可返回 422。保存版本过期或请求的执行版本不再是当前版本时返回 `409 WORKFLOW_REVISION_CONFLICT`，不能静默覆盖或运行另一版本。错误沿用 `{error: {code, message, details?}}` 格式。

历史列表的摘要项包含 `id`、`status`、`workflow_revision`、`created_at`、`finished_at` 和 `error`；不包含 `workflow` 或 `nodes`，查看时需请求运行详情。运行/节点错误为 `{type, message}`（运行错误可另含 `node_id`），不同于 HTTP 错误外层格式。

## 运行、持久化与安全

- 运行绑定确定的工作流快照和所选音频；保存图不改变既有运行。工作流文档历史和运行记录单独保存，不覆盖旧 `job.json`。
- 运行详情包含 `id`、`project_id`、`audio_id`、`workflow_revision`、状态/时间、取消标志、错误、`workflow` 快照和 `nodes` 执行记录。节点记录包含节点身份、配置快照、状态/时间、错误和 artifact。
- 运行状态为 `pending/running/completed/partial/failed/cancelling/cancelled/interrupted`；节点状态为 `pending/running/completed/failed/blocked/cancelled/interrupted`。具体状态码是新图运行的契约，不替换旧 Job 枚举。
- 产物包含 `artifact_id`、`port`、`filename`、`media_type`、`size_bytes`、`sha256`、`download_url`；不向客户端暴露本机绝对路径。历史列表默认分页 20 项，按音频隔离。
- 复制使用分块读写及协作式取消，先写目标同目录的唯一临时文件，完成同步后原子发布。成功节点结果立即持久化，复制未成功不标完成。
- 使用旧 JobManager 的统一任务注册/锁及全局 semaphore。图运行和旧 Pipeline 不能同时占用同一音频；活动图运行同样阻止项目删除。
- 新取消接口不能误写旧 Job；旧取消接口遇到图运行须明确拒绝，不伪装成功。退出或重启后未正常结束的图运行标为 `interrupted`，不自动执行。
- 源文件、运行目录、产物路径均进行授权根边界检查，拒绝目录穿越和危险符号链接。文件和 URL 由服务端生成。

## 前端与验收

- 保留既有视觉风格及五阶段工作区，单独入口显示“节点工作流（首版）”，说明目前只支持音频直通。
- 支持节点添加/删除、拖动布局、连接/断开、缩放/滚动浏览和独立输出配置面板；保存失败/版本冲突不清空草稿。
- 支持启动前校验、当前运行状态、历史运行选择、取消及真实输出下载；首版通过轮询当前活动运行详情更新，不新增 WebSocket 协议。
- 切换音频/项目或关闭编辑器时，过期请求不能覆盖新视图，轮询和拖动监听正确释放；离开未保存草稿需提示。
- 测试覆盖图校验、语义/布局版本、乐观锁、逐音频隔离、独立历史、复制字节一致、下载越界、共享并发和忙碌删除保护、取消/重启恢复；前端覆盖编辑、保存、运行/错误、切换和清理。
- 后端全量 pytest、Ruff，前端全量测试和构建均须通过；完成后更新实际行为文档和线路图，不据此把完整编辑器标为已实现。

## 验收记录

- 后端：全量 `pytest` 265 passed（1 条现有 Starlette/httpx 弃用警告）；工作流专项 18 passed；Ruff lint 与 format check 通过。
- 前端：9 个测试文件、125 tests passed；`vue-tsc --noEmit && vite build` 通过。包含保存/轮询竞态、历史摘要、音频切换、草稿导航保护、产物缺失/检查重试及移动端适配回归。
- 独立临时数据目录联调：浏览器编辑/保存/执行一个输入到三个输出，全部完成；下载文件逐字节及 SHA256 匹配源 WAV，旧运行仍可查询/下载，旧 `job.json` 仍为 `pending`。
- 保存/执行旧版本均返回 409；验证布局保存不提升执行版本、历史快照不映射到不同当前图、390px 窄屏与桌面断点。未使用真实模型或云端服务，不代表处理节点已验收。
- 真实浏览器下载按钮取得的文件与源音频逐字节一致；临时移走合成测试产物后，界面显示文件缺失且隐藏对应下载，恢复文件并重新读取后下载恢复可用。
