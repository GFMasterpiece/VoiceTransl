# Provider Profile 兼容边界

> 核对日期：2026-10-29。此功能不接入当前 Mock/音频直通工作流，不代表云端 ASR 已实现。

## 留存能力

后端保留 `/api/v1/provider-profiles` 的 GET/POST 和 `/{id}` 的 GET/PUT/DELETE，用于全局连接元数据 CRUD。持久化文件是数据目录中的 `provider_profiles.json`：`{schema_version:"1.0",profiles:[...]}`，原子写入，仅接受同版本。

Profile 包含 ID、显示名、Provider 类型、HTTP(S) `base_url`、区域、超时、启用标志、时间及 `api_key_env`。后者是环境变量名，不是密钥；不允许把密钥、认证头或带用户密码的 URL 存入文件或响应。重复 ID 返回 `PROVIDER_PROFILE_EXISTS`，路径与请求 ID 不同返回 `PROVIDER_PROFILE_ID_MISMATCH`。

## 不再提供的链路

- 当前节点没有 `provider_profile_id` 字段，也没有 Profile 管理/选择界面。
- 旧 Pipeline 不再执行，不再为新任务解析 Profile 或生成 Profile 快照；旧 Job 中的非密钥快照仍可只读读取。
- 不进行服务商连通性测试、读取密钥、发送音频或自动切换 Provider。
- 删除仍检查旧可编辑配置文件中的引用，返回 `PROVIDER_PROFILE_IN_USE`；这些配置现在只读，不能再用配置 PUT 解除引用。显式迁移/解除历史引用的工具尚未实现，不应自动改写历史文件以允许删除。

`openai_transcribe` 等字符串仅是连接元数据类型，不代表存在执行器。云端接入需重新批准节点配置、Profile 快照、认证、隐私、取消和错误语义，见[云端草案](../spec/cloud-asr-openai.md)。
