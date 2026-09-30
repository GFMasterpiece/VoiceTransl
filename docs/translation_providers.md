# HTTP 翻译 Provider

VoiceTransl 的可选 HTTP 翻译协议由 `translation.provider` 选择：

| provider | 请求路径 | 默认 API Key 环境变量 | 响应文本 |
| --- | --- | --- | --- |
| `openai_chat` | `${base_url}/chat/completions` | `OPENAI_API_KEY` | `choices[0].message.content` |
| `openai_responses` | `${base_url}/responses` | `OPENAI_API_KEY` | `output_text`，或 `output` 中 message 的 content 文本 |
| `anthropic_messages` | `${base_url}/messages` | `ANTHROPIC_API_KEY` | `content` 中的 text 块 |

## 项目配置

配置文件中的 `translation` 片段示例：

```yaml
translation:
  provider: openai_responses
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  model: gpt-4.1-mini
  target_language: zh-CN
  batch_size: 8
  temperature: 0.2
  timeout_seconds: 120
  glossary:
    先輩: 前辈
  instructions: 保留说话人的语气
```

`base_url` 是服务的 API 根地址，Provider 会自动追加协议路径。`api_key_env` 可覆盖默认环境变量名，但值必须是环境变量名称。API Key 只从该环境变量读取，不能写入项目配置或请求参数。启动后修改环境变量需要重新启动服务。

每个批次会发送带有片段 `id` 和原文的 JSON 请求，并要求服务返回如下结构。Provider 会校验所有片段 ID 恰好出现一次，并保留原片段的时间范围、原文和 ID：

```json
{"translations": [{"id": "seg_001", "translation": "译文"}]}
```

`batch_size` 限制为 1 到 128，`temperature` 限制为 0 到 2，`timeout_seconds` 限制为 1 到 3600。服务返回无效 JSON、缺少片段、重复 ID、网络错误或超时时，任务会以稳定的翻译错误码失败。取消任务会在当前请求完成后停止后续批次，并保留可恢复的任务状态。

## 运行示例

```bash
export OPENAI_API_KEY='...'
cd backend
PYTHONPATH=. python -m pytest -q
```

测试使用注入的 fake HTTP 回调，不会访问外部服务，也不会把密钥写入仓库。
