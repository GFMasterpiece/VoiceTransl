# 节点工作流第三里程碑：ASR 与文本输出

> 状态：Implemented（2026-10-29）。范围于 2026-10-28 获批；已完成本地真实识别、API 与前后端验收。完整编辑器仍为 Draft。

## 范围

在音频输入、预处理和音频输出基础上增加 `asr`、`text_output`。支持音频直接或经预处理进入 ASR，再导出识别原文/字幕；音频直出分支继续可用。支持多个独立 ASR 分支。保持项目级图、单音频运行、不可变快照、独立节点产物、串行拓扑执行、全局并发及音频忙碌边界。

不加入翻译、对齐、云端 ASR、视频、汇合、缓存复用、指定节点重跑、历史清理或旧配置迁移。不下载程序或模型，不修改旧 Pipeline 的结果与导出语义。

## 节点与端口

- `audio_input`：输出 `audio`；`preprocess`：输入/输出 `audio`；`audio_output`：输入 `audio`。
- `asr`：输入 `audio`，输出 `transcript`；`text_output`：输入 `transcript`，无输出。
- 边的 `source_port` / `target_port` 必须为对应端口名称；不能把音频连接到文本输出，也不能把识别结果连接到音频节点。
- 恰好一个音频输入，至少一个终端输出（audio_output 或 text_output）。输入无入边，其他节点恰好一条入边；输出无出边。所有节点必须从输入可达，所有非输出节点必须能到达终端输出；拒绝环、孤立节点、未知节点及配置。继续最多 64 节点/64 边。

## 配置

`asr.config` 复用 ASRConfig 字段及默认值：provider=mock、backend=qwen3-1.7b、model=mock、language=ja、vad_enabled=true、chunk_seconds=30、provider_profile_id=null。仅 mock/crispasr；本轮 provider_profile_id 只能为 null，不能引用云端配置。字段严格类型校验，不接受未知字段、路径、命令。CrispASR backend 限于现有支持列表，model 必须为受限文件名标识符，不能是路径。语言为该节点独立设置，不静默改为当前音频语言。省略默认值与显式默认值执行语义相同。

Mock 明确为演示识别，不识别真实语音，产物语言字段使用该节点语言；可复用现有固定示例文本。CrispASR 复用后端当前生效的可执行文件、模型目录和超时设置，只将安全模型文件名/后端标识作为节点设置；模型列表复用 `/settings/local-asr/models`，不在工作流中保存本机路径或密钥。运行在提交进入队列前捕获服务端执行设置，排队期间或执行后的设置编辑均不改变该运行。

预处理可选。CrispASR 执行前校验实际直接上游为可读取、非空、完整的 16 kHz 单声道 PCM WAV；否则该节点失败并给出需要 FFmpeg 预处理的明确错误，不隐式转换或回退 Mock。已经满足要求的源 WAV 可以直接进入 CrispASR；Mock 预处理不改变格式。

`text_output.config` 为 `{filename_stem: "transcript", format: "txt"}`；format 仅 txt/srt/vtt/lrc，每个输出节点生成一个格式，可通过多个输出分支同时导出。filename_stem 遵循现有安全文件名规则（最多 128 字符）。输出为 UTF-8、LF；TXT 按片段顺序逐段输出 source_text，非空结果末尾换行。字幕复用已有格式化能力导出原文，不要求 translated_text，也不能修改上游 Transcript。

## 产物与执行

ASR 读取直接上游文件，保存独立 `transcript.json`，内容复用 Transcript/Segment 的 schema_version、project_id、stage=raw、language、duration、segments（ID、原文、时间范围等），metadata 不包含本机路径。运行节点 artifact.port 为 transcript，media_type=application/json。音频产物继续 port=audio。

文本输出读取直接上游 ASR 产物，artifact.port 为 text，filename 后缀对应所选格式，media_type 为 text/plain（txt/lrc）、application/x-subrip（srt）、text/vtt（vtt）。所有产物记录大小、SHA256，并复用现有 outputs/{node_id} 元数据与下载接口；该历史路由名不再限定音频。输入节点仍无产物。

ASR 成功结果至少有一个非空原文片段，ID 唯一；若提供时间戳，则须为有限、非负且 end>=start，拒绝不完整时间范围。TXT 允许无时间戳，字幕输出必须具备完整有效时间范围；缺少时间戳只导致该字幕输出失败，不影响 TXT/音频独立分支。不制造时间戳。

节点成功立即持久化，产物临时写入后 fsync/原子发布，源和旧任务不得覆盖。错误只阻塞依赖下游。运行结果按所有终端输出聚合：全成功 completed，部分成功 partial，无成功 failed。取消/关闭需回收 ASR 子进程并等待文件写入/复制线程退出，未完成节点不得晚发布；已完成节点保留。CrispASR stderr/stdout 有界或丢弃，原始诊断与绝对路径不写入用户错误。重启仍标 interrupted，不自动续跑。

## 前端与验收

新增 ASR 与文本输出入口、节点独立设置和不同类型端口。ASR 复用已有模型选择体验，语言可单独编辑；文本输出可选择格式及文件名。新增节点不静默改接现有边；允许有效分支/串联，拒绝类型不匹配及循环。运行只读、草稿导航保护、版本冲突、请求竞态隔离及历史快照隔离保持有效。预处理、ASR JSON、文本和音频产物均经元数据确认后下载。

验收覆盖旧音频图兼容、仅文本输出图、混合分支/多 ASR、默认值/版本、严格配置与端口、真实上游传递、TXT/字幕内容与 SHA、时间戳缺失及无效结果、缺失程序/模型、取消/超时/关闭/无晚发布、重启历史、安全路径和旧 Job 不变。后端全量 pytest/Ruff、前端测试与构建、API 联调和桌面/移动端 UI 验收通过后更新 docs/线路图及规格状态。CrispASR 可用时做真实识别，否则明确记录依赖限制并用 fake CLI 验证集成；不声称 fake 结果为真实模型识别。本轮不自动 Git 提交。

## 验收记录（2026-10-29）

- 后端：`PYTHONPATH=. python -m pytest -q` 全量 338 项通过；`ruff check app tests` 和 `ruff format --check app tests` 通过。覆盖类型/配置、原文导出、排队设置快照、进程启动竞态、超时/取消/关闭、重复取消与写线程回收；既有 Pipeline 回归通过。
- 前端：`npm test -- --run` 共 9 个文件、145 项通过；`npm run build` 通过。覆盖节点独立设置、类型连线、历史/请求隔离；活动运行禁止切换历史，保持编辑锁与取消入口。
- 真实 ASR：本机 CrispASR 0.8.38、Qwen3 0.6B GGUF，通过 API 将合成英语语音经 FFmpeg、ASR 分别导出 TXT/SRT/VTT/LRC，并检查 transcript、媒体类型、大小及 SHA256。已符合格式的源 WAV 不经预处理也可识别；不合格输入和缺失模型按约定失败，独立音频分支继续。
- 持久化与界面：重建服务后历史和下载仍可用，旧 Job/源文件不变、无残留临时文件；桌面模型发现/节点设置、历史 TXT/JSON 实际下载及 390px 窄屏设置弹窗通过检查。
- 尚无翻译/对齐节点、视频、指定节点重跑、缓存复用或历史清理；真实识别仍需自行准备本地程序和模型。验收数据与工具脚本均不入库。
