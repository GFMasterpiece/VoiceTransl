# Provider Profile 配置

VoiceTransl 将“如何连接提供商”和“本次任务如何处理”分成两层：

- 全局 Provider Profile 保存提供商类型、显示名称、接口地址、区域、超时和 API Key 环境变量名。
- 项目或音频的节点配置只保存 `provider_profile_id`、模型、语言和识别选项。
- 任务开始时，后端将当前 Profile 复制到 Job 的 `provider_profiles_snapshot`，之后即使全局配置被修改，运行中的任务仍使用原快照。

全局配置持久化在数据目录的 `provider_profiles.json`，使用版本化文档：

```json
{
  "schema_version": "1.0",
  "profiles": [
    {
      "id": "openai-main",
      "provider_type": "openai_transcribe",
      "display_name": "OpenAI transcription",
      "base_url": "https://api.example.test/v1",
      "api_key_env": "OPENAI_API_KEY",
      "region": "global",
      "default_timeout": 180,
      "enabled": true
    }
  ]
}
```

API Key 的实际值只能由后端从 `api_key_env` 指定的环境变量读取，不能写入项目配置、Profile 文件、任务快照或前端响应。

当前 API：

- `GET /api/v1/provider-profiles`
- `POST /api/v1/provider-profiles`
- `GET /api/v1/provider-profiles/{id}`
- `PUT /api/v1/provider-profiles/{id}`
- `DELETE /api/v1/provider-profiles/{id}`

被项目或音频配置引用的 Profile 不能直接删除；应先切换节点配置或停用 Profile。当前提交只完成配置分层和快照，不包含真实云端 ASR 请求实现。
