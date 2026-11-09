# VoiceTransl 聆译

简体中文 | [English](README_EN.md)

VoiceTransl 正在构建一个可视化音频节点工作流：像 ComfyUI 一样连接节点、独立配置各节点，并为每次运行保存不可变快照。

**当前是 Web 开发版，提供音频直通、Mock 验证及 Qwen 云端识别，不提供翻译或媒体转换。** 旧桌面实现、GalTransl、插件及其依赖和打包资源已从当前源码移除，历史简记见[线路图](docs/roadmap.md)，完整实现可从 Git 历史追溯。

## 当前能力

- 简体中文 / English 界面切换，首次按浏览器语言选择并记住偏好；切换保留表单、节点上传状态和工作流草稿。
- 项目列表分页；只填名称即可创建空项目，在音频输入节点选择或上传资源并填写源语言，上传失败可重试，不自动运行。
- 识别语言和音频源语言提供可搜索候选，支持中文、英文、原名、代码及别名，也可填写自定义代码。
- 主页[全局设置](docs/global_settings.md)可调整上传与工作流并发，并设置 ASR 新节点的默认连接、模型、地域、语言及 API Key；新节点复制默认值及独立密钥，已有节点和历史不随之改变。
- 项目级节点画布、节点独立配置、端口类型校验、图保存和修订冲突检测；节点目录、分类与配置来自后端能力接口，添加菜单按功能分组。
- 编辑菜单提供撤销/重做、多选与整组拖动、复制粘贴、工作流 JSON 导入导出；导入先进入可撤销草稿，音频文件和运行历史独立保留。
- 默认 `audio_input -> audio_output`，复制原始音频字节为独立输出，不转码；允许不包含任何处理节点。
- 可选 Mock 预处理、Mock ASR 及 TXT/SRT/VTT/LRC 文本输出；ASR 输出为固定示例，不识别上传的音频。
- 独立 Qwen Filetrans 识别：上传音频、异步识别并输出真实文本/时间戳，模型可配置，复用文本预览、字幕及批次。每个实例直接编辑地址、超时与 API Key，密钥独立加密保存，运行冻结设置及凭据版本；没有全局连接或环境变量备用。旧连接字段需删除节点后重建，历史产物保留。设置见[节点连接](docs/node_connections.md)。
- 独立 Qwen Streaming 识别节点：通过 WebSocket 流式发送已有的 PCM16 单声道 WAV，自动读取采样率，完成后复用文本预览、字幕和批次。每个节点分别配置地址和密钥，复制后重新填写，不继承全局连接；首版不包含麦克风或实时字幕界面。范围见[文件流式识别](spec/cloud-asr-qwen-streaming.md)。
- 字符串、整数、数字、布尔值节点；可按后端声明连线提供参数，首批支持 ASR 语言、输出文件名和文本格式。接线保留手填后备值，运行详情记录参数来源及实际值。
- 每音频独立运行、取消、运行历史及安全产物下载；执行始终使用提交时的图快照。
- 音频输入节点可导入多个文件/文件夹，预检、设置源语言并上传；保存清单后统一点击运行，每份音频独立应用同一工作流快照。批次面板管理进度、取消、失败/中断重试与结果 ZIP。新输出默认按前缀生成 `名称_00001.wav` 等文件名，重试保留原编号；旧图原命名不变。“当前文件信息”节点可提供逐文件命名和语言参数。

其他云端协议、本地模型、视频处理、任意节点续跑、缓存复用和历史清理尚未实现。目标与计划分别见[产品目标](docs/product_requirements.md)和[线路图](docs/roadmap.md)，不能当作现有功能。

## 开发环境

- Python 3.12 或更新版本，以及一个已激活的隔离环境（venv 或 Conda 均可）。
- Node.js 24 与 npm；依赖使用 `frontend/package-lock.json` 安装。
- 当前 Web 骨架不需要下载本地模型、FFmpeg、llama.cpp 或桌面端依赖。

在仓库根目录、已激活的 Python 环境中安装后端依赖：

```text
cd backend
python -m pip install -e ".[test]" ruff "uvicorn[standard]"
```

在 `frontend/` 安装前端依赖：

```text
npm ci
```

Windows PowerShell 下如 `npm` 被脚本执行策略阻止，使用 `npm.cmd` 执行本文的 npm 命令。

## 启动

Windows 本机可以直接双击 `scripts/start-dev.cmd` 一键重启后端、前端并打开页面。本机脚本使用后端 `8000`、前端 `5180`：先停止对应的已有项目服务，等待端口释放，再重新启动；无关进程占用或停止权限不足时明确报错，不按名称批量结束 Python/Node。`scripts/` 是本机脚本目录，已被 Git 忽略；如果重新克隆仓库，需要自行放入该脚本及其辅助文件。脚本默认使用名为 `asmr` 的 Conda 环境；如果环境名不同，可在命令提示符中先设置 `VOICETRANSL_CONDA_ENV`。如果 Conda 不在 PATH 中，可设置 `VOICETRANSL_CONDA_COMMAND` 指向 `conda.bat` 或 `conda.exe`。

运行 `scripts/start-dev.cmd --stop` 可只停止这两个开发服务。重启会中断当前本机任务；脚本先尝试正常停止，再清理未退出的项目进程。

也可在两个独立终端手动启动服务。后端命令从 `backend/` 执行，保持单进程、单 worker：

```text
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

前端命令从 `frontend/` 执行：

```text
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

- 页面：[http://127.0.0.1:5173](http://127.0.0.1:5173/)
- API 健康检查：[http://127.0.0.1:8000/api/v1/health](http://127.0.0.1:8000/api/v1/health)
- API 交互文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

Vite 将 `/api` 代理到本机后端。上述是本机开发启动方式，不是生产部署方案，不应直接暴露到公网。多 worker 会产生独立的内存调度器，当前不支持。

## 配置与数据

服务配置位于 `config/app.yaml`；可通过 `VOICETRANSL_CONFIG` 指定另一配置文件。默认数据目录是仓库根目录的 `data/`，可用 `VOICETRANSL_DATA_DIR` 覆盖。环境变量中的相对数据路径按启动工作目录解析，建议使用绝对路径。

项目、音频、图修订、运行快照和产物由后端保存在文件系统，不依赖数据库。历史数据不随源码清理删除或自动迁移。`data/`、`scripts/` 和个人 `.codex/` 配置不入库，也不要提交密钥或本机环境文件。

## 验证

从 `backend/` 执行：

```text
python -m pytest -q
ruff check app tests
ruff format --check app tests
```

从 `frontend/` 执行：

```text
npm test -- --run
npm run build
```

Web CI 配置了 Linux/Windows 后端测试及 Ruff 检查、Linux 前端测试及构建；不再打包或发布旧桌面程序。配置 CI 不等于远端运行已经通过，具体结果以 Actions 为准。Windows 缺少创建符号链接权限时，相关测试会跳过，不能据此认为该场景已验证。

## 目录与文档

| 路径 | 用途 |
| --- | --- |
| `backend/` | FastAPI 后端、节点注册、文件存储、运行与测试 |
| `frontend/` | Vue 3 前端、节点画布与测试 |
| `config/` | 服务运行配置 |
| `docs/` | 当前架构、实际行为、产品目标和开发线路图 |
| `spec/` | 功能范围、状态与验收条件；Draft 不代表已批准或实现 |

- [架构约定](docs/architecture.md) / [后端开发说明](backend/README.md)
- [多音频资源与 API](docs/voicetransl_multi_audio_project.md) / [前端工作流](docs/frontend_workflow.md)
- [开发线路图](docs/roadmap.md) / [功能规格索引](spec/README.md)
- [仓库协作规则](AGENTS.md)

## 来源与许可

本项目历史基于 [shinnpuru/VoiceTransl](https://github.com/shinnpuru/VoiceTransl) 和 [GalTransl](https://github.com/xd2333/GalTransl)，感谢 [shinnpuru](https://github.com/shinnpuru)、[MurthiNext](https://github.com/MurthiNext) 等原项目贡献者。旧实现可从 Git 历史追溯；当前 Web 版不承诺提供上游桌面功能。

项目沿用 [GNU GPLv3](LICENSE)。请遵守许可证、素材版权及当地法律法规；许可证原文保留不变。
