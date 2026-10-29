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
  additional_parameters: {}
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

`batch_size` 限制为 1 到 128，`timeout_seconds` 限制为 1 到 3600。服务返回无效 JSON、缺少片段、重复 ID、网络错误或超时时，任务会以稳定的翻译错误码失败。

## 附加参数与旧配置

- 模型可选请求字段统一写入 `additional_parameters`，默认 `{}`。适配器默认不注入 `temperature`、`response_format` 或 `max_tokens`，也不因服务报错而静默重试其他参数组合。
- 例如需要温度时显式设置 `additional_parameters: {temperature: 0.2}`；是否接受该字段由实际服务/模型决定，不维护模型名称白名单。
- Anthropic Messages 协议要求 `max_tokens`：需显式设置，例如 `additional_parameters: {max_tokens: 4096}`。缺少时以 `TRANSLATION_PARAMETER_REQUIRED` 失败，不猜测数值。
- 附加参数受 JSON 深度、数量、字节数和有限数值限制，不允许替换核心模型/输入、地址、认证或密钥字段。API Key 始终由环境变量读取，不能放进附加参数。
- 同版本旧配置/快照中的显式 `temperature` 仍可读取，运行时映射到附加参数；若两处同时存在，以显式 `additional_parameters.temperature` 为准。不改写磁盘上的历史快照。新配置不设置旧字段，也不继承旧默认温度。

## 取消、超时与关闭

HTTP 传输使用 `httpx.AsyncClient`，不在线程中运行同步 HTTP 请求。取消信号、超时和服务关闭会取消并等待正在进行的异步请求，退出客户端上下文、释放本地连接后才完成清理；不会只停止等待而遗留请求线程。保留已经完成的阶段产物及可恢复状态，不继续发送后续批次。

此保证仅针对本地请求与资源，不能撤销服务商已经接收的计算或保证其停止计费。恢复仍遵守原任务快照，不自动改用新配置。

## 运行示例

```bash
export OPENAI_API_KEY='...'
cd backend
PYTHONPATH=. python -m pytest -q
```

测试使用异步 fake HTTP 回调及受控传输，不访问收费模型服务，也不把密钥写入仓库。
