# 本地 ASR 能力发现与静态白名单问题

> 状态：Implemented and verified (2026-10-29)。本轮恢复能力单一来源、跨层自动同步与兼容性整改，不增加本地推理功能。实现范围与验证见 [兼容性与扩展性整改](compatibility-extensibility.md)；云端 ASR 仍优先。现有 Mock/CrispASR 功能保留。

## 当前契约与边界

- 后端从当前 CrispASR CLI 的 `--list-backends-json` 获取任务与能力元数据；运行时查询是唯一可变能力来源。原配置与 Provider 中的重复静态后端白名单已移除。
- CLI 通过能力 flags 识别任务：明确的非 ASR flags（如 `tts`、`s2s`、`separate`、`pitch`、`chords`、`beats`、`tab`、`piano`）优先排除；明确的 ASR flags（如 timestamps、word/token timestamps、language detection）才允许作为 ASR 选项。未知类别显示为 `other` 并禁用，不通过名称猜测。
- 识别结果由 `/api/v1/capabilities` 提供给 UI。提交旧 Pipeline 或图工作流时检查发现结果并捕获 backend/capability 快照；已排队运行不受后来 CLI/设置变化影响，不持久化本机路径。
- 领域层仍校验标识符及模型文件安全。模型架构映射仅包含经过验证的关联；未知架构不猜测。发现失败明确报告，不回退 Mock。
- 本地 CrispASR 仍使用当前命令/输出协议；相同协议的新 ASR backend 无需重复编辑名单，但不代表 CLI 的所有 ASR 任务均适配当前 Provider。不同协议仍需显式适配。

## 已验证场景

1. 非预置但带可确认 ASR 能力的 backend 可被发现；非 ASR 和不明任务不会误标成 ASR。
2. 查询失败、非法标识符、不支持 backend、运行快照、队列设置捕获和历史配置回归有测试覆盖。
3. Linux/Windows 后端回归通过。当前测试所用 CLI 返回 131 个候选、41 个可确认 ASR backend；这是本地测试环境的数据，不是内置清单或永久保证。

仅完成能力契约修复，不扩张本地推理功能。云端 ASR 仍是下一功能方向。
