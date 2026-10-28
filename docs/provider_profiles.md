# Provider Profile 配置

> 文档状态：实现说明（核对日期：2026-10-28）

VoiceTransl 将“如何连接提供商”和“本次任务如何处理”分成两层：

- 全局 Provider Profile 保存连接元数据，如 Provider 类型、显示名称、API 地址、区域、超时和 API Key 环境变量名。
- 项目/音频的 ASR 节点配置通过 `provider_profile_id` 引用 Profile；识别模型、语言等属于任务配置。
- 新任务开始时，后端将当前引用的 Profile 保存到 Job 的 `provider_profiles_snapshot`。恢复旧任务使用原配置及其 Profile 快照。

**重要：当前已经实现的是后端配置、持久化、API 和任务快照，不等于云端 ASR 已可用。** ASR 运行时目前仍只支持 `mock` 与本地 `crispasr`；Profile 管理界面和真实 `openai_transcribe` 请求尚未实现。整体进度与下一步见[开发线路图](roadmap.md)。

## 存储格式

全局配置存放于服务数据目录的 `provider_profiles.json`，首次使用且文件不存在时返回空列表。文件采用版本化 JSON 文档，并由存储层原子写入：

```json
{
  "schema_version": "1.0",
  "profiles": [
    {
      "schema_version": "1.0",
      "id": "openai-main",
      "provider_type": "openai_transcribe",
      "display_name": "OpenAI transcription",
      "base_url": "https://api.example.test/v1",
      "api_key_env": "OPENAI_API_KEY",
      "region": "global",
      "default_timeout": 180,
      "enabled": true,
      "created_at": "2026-10-28T00:00:00Z",
      "updated_at": "2026-10-28T00:00:00Z"
    }
  ]
}
```

`api_key_env` 是环境变量的**名称**，不是密钥。密钥值不属于 Profile 模型，不能保存进 Profile 文件、项目配置、Job 快照或 API 响应。Profile 的 `base_url` 只允许 HTTP/HTTPS URL，不能嵌入用户名或密码；标识和环境变量名经过格式校验，`default_timeout` 范围为 1 至 3600 秒。

## API

所有端点以 `/api/v1` 为前缀：

| 方法与路径 | 行为 |
| --- | --- |
| `GET /provider-profiles` | 列出全局 Profile |
| `POST /provider-profiles` | 创建 Profile；重复 ID 返回 `409 PROVIDER_PROFILE_EXISTS` |
| `GET /provider-profiles/{id}` | 读取单个 Profile |
| `PUT /provider-profiles/{id}` | 更新 Profile；请求 ID 必须与路径 ID 一致 |
| `DELETE /provider-profiles/{id}` | 删除未被可编辑项目/音频配置引用的 Profile |

Profile 被项目或音频的 `asr.provider_profile_id` 引用时，删除返回 `409 PROVIDER_PROFILE_IN_USE`。需先移除或更换可编辑配置中的引用。既有 Job 保存独立的非密钥 Profile 快照，因此不依赖继续读取已删除的全局 Profile。

## 当前运行边界

| 能力 | 当前状态 |
| --- | --- |
| 版本化全局存储与 CRUD API | 已实现 |
| `provider_profile_id` 配置字段 | 已实现于 ASR 配置模型 |
| 新任务校验 Profile 存在且已启用 | 已实现 |
| Job 保存及恢复非密钥 Profile 快照 | 已实现 |
| 从 `api_key_env` 读取密钥并发出云端 ASR 请求 | 未实现 |
| ASR 运行时选择 `openai_transcribe` Provider | 未实现，ASR 仍限 `mock` / `crispasr` |
| Provider Profile 前端管理与选择流程 | 未实现 |

创建、更新 Profile 不会自动切换 Provider、测试连通性或发送音频。`enabled=false` 的 Profile 可以保存，但不能用于新任务。翻译 Provider 的 API Key 环境变量读取属于独立能力，参见 [HTTP 翻译 Provider](translation_providers.md)。

实现云端 ASR 时，必须补上真实密钥的服务端解析、请求/错误处理、隐私告知及前端配置流程；不得因为已有 Profile API 就宣称这些功能已经完成。
