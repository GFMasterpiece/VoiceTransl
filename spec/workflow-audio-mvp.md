# 节点工作流历史里程碑：音频直通

> 状态：Retired（2026-10-29）。本文件仅记录历史里程碑，不是当前 API 或功能验收依据。

早期实现以音频输入直连一个或多个音频输出为起点，建立了图校验、保存乐观锁、执行版本、独立运行历史、逐节点文件与安全下载；此后曾扩展真实处理节点，并保留旧固定阶段入口。

当前已移除固定 Pipeline 和真实 Provider 配置，默认回到 `audio_input -> audio_output`，只保留可选 Mock 验证节点。旧数据不自动删除或转换，历史参数不会被静默替换为 Mock。

本里程碑的详细接口和旧测试数量不再维护。当前行为与验证要求分别见[架构约定](../docs/architecture.md)、[多音频契约](../docs/voicetransl_multi_audio_project.md)、[前端工作流](../docs/frontend_workflow.md)及 [AGENTS.md](../AGENTS.md)；进度见[线路图](../docs/roadmap.md)，未来方向见[工作流规格](workflow-editor.md)。
