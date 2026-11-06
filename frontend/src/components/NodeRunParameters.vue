<script setup lang="ts">
import { t } from '../i18n'
import type { WorkflowNodeRun, WorkflowParameterSource } from '../stores/workflow'

defineProps<{ node: WorkflowNodeRun }>()
function sourceLabel(source: WorkflowParameterSource) {
  return source.kind === 'config' ? t('workflow.parameterManual') : t('workflow.parameterOrigin', {
    node: source.source_node_id, port: source.source_port, target: source.target_port, edge: source.edge_id,
  })
}
</script>

<template>
  <dl class="run-parameters">
    <dt>{{ t('workflow.parameterSources') }}</dt>
    <dd v-if="node.parameter_sources == null">{{ t('workflow.notRecorded') }}</dd>
    <dd v-else-if="!Object.keys(node.parameter_sources).length">{{ t('workflow.parameterNone') }}</dd>
    <dd v-for="(source, field) in node.parameter_sources || {}" :key="field"><strong>{{ field }}</strong> · {{ sourceLabel(source) }}</dd>
    <dt>{{ t('workflow.effectiveConfig') }}</dt>
    <dd v-if="node.effective_config == null">{{ t(node.parameter_sources == null ? 'workflow.notRecorded' : 'workflow.parameterUnresolved') }}</dd>
    <dd v-else><pre>{{ JSON.stringify(node.effective_config, null, 2) }}</pre></dd>
  </dl>
</template>
