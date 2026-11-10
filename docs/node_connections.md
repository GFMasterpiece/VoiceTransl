# LLM 与 ASR 服务

LLM 和 ASR 分别使用服务、模型及逻辑端口，不能跨类混接。当前契约见[服务分类与字段输入](../spec/service-families-field-inputs.md)。

## 搭建

| 用途 | 连接路径 | 地址示例 |
| --- | --- | --- |
| LLM 翻译 | LLM 服务 → LLM 模型 → LLM 翻译 | https://example.com/v1，调用时追加 /chat/completions |
| Qwen 录音识别 | ASR 服务 → ASR 模型 → Qwen 语音识别 | https://maas.qianwenaiapi.com/api/v1，选择 qwen_filetrans |
| Qwen 文件流式识别 | ASR 服务 → ASR 模型 → Qwen 流式识别 | wss://maas.qianwenaiapi.com/api-ws/v1/inference，选择 qwen_streaming |

模型名称由实际服务决定，可自由配置。LLM 服务当前只有 Chat Completions，不展示 ASR 协议选择；ASR 服务只列已实现的识别协议。两类服务均没有地域字段，实际路由由地址决定。端点需含协议，不得包含 URL 认证、查询或片段；明文 WS 仅允许本机回环。

上传音频时提供源语言，识别使用该份音频声明。ASR 没有上下文口、字段或适配器参数，附加 JSON 也不能插入 context。LLM 的 context 在翻译节点直接多行填写；可从编辑框左侧插口连文本值或多输入拼接，接线后隐藏编辑框，断线恢复手填值。

模型名及显式请求参数在各自模型节点，总超时、分批等在执行参数节点。字段能否接线由目录 input_port 声明；地址、协议和 Key 在服务节点手填。模型请求参数默认空，不能覆盖认证、核心输入、路由或结构协议。

同族连接、模型和执行参数可通过显式连线供多个消费者使用，不按地址、名称或值自动合并。LLM/ASR 连接及模型端口类型不同，前后端与运行恢复均拒绝混接。

## 密钥与模板

密钥归服务实例，绑定项目、owner 节点及其凭据类型；从后端声明取类型，不借用其他族或未连接服务的 Key。留空保留，替换/清除显式操作，复制/剪切粘贴和导入导出剥离引用与私有缓冲。

[全局设置](global_settings.md)只提供新建节点模板。LLM/ASR 服务与模型分别设置，默认 Key 在新服务首次保存时按捕获版本物化为其独立凭据；现有图及排队任务不补读最新模板。

Key 通过工作流 PUT 的只写 credential_updates 和稳定 request_id 保存。失败保留私有输入，成功清空；密码不进入普通草稿、撤销、浏览器存储或测量克隆。只改密码也触发未保存和运行保护。

服务端采用 Fernet 加密，数据根 .credentials/ 的主密钥和密文需一起保护与备份；不抵御整个数据根泄露。图、typed binding、checkpoint、响应和日志不保存明文 Key；仅在适配器调用边界解密。不提供 Profile 或环境变量备用。

## 运行及恢复

图、运行、批次和设置为 4.0，能力目录 1.18，使用 v4 存储。不读取、迁移或转换旧命名空间，旧文件原样留盘；项目与音频资源仍为 1.0，可继续使用。

提交/排队/重试固定图、实际参数依赖及 Key 版本，不回读最新模板。模型绑定校验族、owner、协议、实际模型名及请求参数与冻结图一致，不能只比较未接线的手填后备。重跑重新物化必要纯配置祖先，并用新指纹检验处理缓存。

Filetrans 继续隔离控制、临时上传与结果下载的认证和 Cookie；Streaming 仅处理 PCM16 单声道 WAV，自动读采样率、限速发送、只发布最终文本，不自动转码或重连。取消/超时回收本地请求、收发及文件读取，不能撤回远端已处理内容。缺真实时间戳时不补造，TXT 可接受正文，字幕仍要求真实时间轴。

协议历史见 [Filetrans](../spec/cloud-asr-qwen.md)、[Streaming](../spec/cloud-asr-qwen-streaming.md)，现用翻译见[翻译说明](translation_providers.md)。
