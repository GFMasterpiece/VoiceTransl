<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import { useWorkflowStore, type WorkflowSnapshot } from '../stores/workflow'
import { connectionTarget } from '../workflow/providerConnections'
const props = defineProps<{ workflow: WorkflowSnapshot; audioCount: number; historical?: boolean }>()
const workflowStore = useWorkflowStore()
const targets = computed(() => props.workflow.nodes.flatMap((node) => {
  const kind = workflowStore.registry?.kind(node.type)
  const inline = kind?.inlineConnectionInput
  if (!inline) return []
  const unsupported = workflowStore.registry?.validateConfig(node.type, node.config).some((issue) => issue.code === 'LEGACY_CONNECTION_UNSUPPORTED')
  return [{ nodeId: node.id, host: unsupported ? '' : connectionTarget(node.config[inline.baseUrlKey]), showRegion: !!inline.regionKey,
    region: inline.regionKey ? node.config[inline.regionKey] : null }]
}))
const missingTarget = computed(() => props.historical && (targets.value.some((target) => !target.host)
  || (!targets.value.length && !!props.workflow.provider_profiles_snapshot)))
</script>
<template>
  <div v-if="targets.length || missingTarget" class="cloud-workflow-notice" data-cloud-notice>
    <p>{{ t('connections.fileCount', { count: audioCount }) }}</p>
    <p v-if="missingTarget" data-historical-target-missing>{{ t('connections.historicalTargetMissing') }}</p>
    <div v-for="target in targets" :key="target.nodeId" class="provider-target" :data-inline-provider-target="!historical ? target.nodeId : undefined" :data-inline-provider-snapshot-target="historical ? target.nodeId : undefined"><strong>{{ t(historical ? 'connections.snapshotTarget' : 'connections.target') }}</strong><span>{{ target.host || t('connections.unknownTarget') }}<template v-if="target.showRegion"> · {{ target.region || t('connections.unknownRegion') }}</template></span></div>
  </div>
</template>
