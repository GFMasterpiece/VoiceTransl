# VoiceTransl 聆译

简体中文 | [English](README_EN.md)

VoiceTransl 正在构建一个可视化音频节点工作流：像 ComfyUI 一样连接节点、独立配置各节点，并为每次运行保存不可变快照。

**当前是 Web 开发版，只提供音频直通和 Mock 验证能力，不执行真实语音识别、翻译或媒体转换。** 历史桌面程序已停止维护，不再作为当前功能或安装入口；其遗留文件清理进度见[线路图](docs/roadmap.md)。

## 当前能力

- 项目列表分页、多音频上传队列、失败项单独重试；创建时明确填写源语言和目标语言，上传不自动运行。
- 项目级节点画布、节点独立配置、端口类型校验、图保存和修订冲突检测；节点目录与配置来自后端能力接口。
- 默认 `audio_input -> audio_output`，复制原始音频字节为独立输出，不转码；允许不包含任何处理节点。
- 可选 Mock 预处理、Mock ASR 及 TXT/SRT/VTT/LRC 文本输出；ASR 输出为固定示例，不识别上传的音频。
- 每音频独立运行、取消、运行历史及安全产物下载；执行始终使用提交时的图快照。

真实云端/本地模型、视频处理、任意节点续跑、缓存复用和历史清理尚未实现。目标与计划分别见[产品目标](docs/product_requirements.md)和[线路图](docs/roadmap.md)，不能当作现有功能。

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

在两个独立终端启动服务。后端命令从 `backend/` 执行，保持单进程、单 worker：

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
