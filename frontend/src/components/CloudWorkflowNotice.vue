<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import { useWorkflowStore, type WorkflowSnapshot } from '../stores/workflow'
import { useProviderProfilesStore, type ProviderProfile } from '../stores/providerProfiles'
import ProviderTarget from './ProviderTarget.vue'
import { connectionDataHandling, connectionTarget, inlineConnectionActive } from '../workflow/providerConnections'
const props = defineProps<{ workflow: WorkflowSnapshot; audioCount: number; historical?: boolean }>()
const workflowStore = useWorkflowStore()
const profiles = useProviderProfilesStore()
const references = computed(() => props.workflow.nodes.flatMap((node) => {
  const descriptor = workflowStore.registry?.kind(node.type)?.providerProfileInput
  return descriptor && !inlineConnectionActive(workflowStore.registry?.kind(node.type), node.config) ? [{ id: typeof node.config[descriptor.configKey] === 'string' ? node.config[descriptor.configKey] as string : '', descriptor }] : []
}))
const inlineTargets = computed(() => props.workflow.nodes.flatMap((node) => {
  const kind = workflowStore.registry?.kind(node.type)
  const inline = kind?.inlineConnectionInput
  if (!inline || !inlineConnectionActive(kind, node.config)) return []
  return [{ nodeId: node.id, host: connectionTarget(node.config[inline.baseUrlKey]), showRegion: !!inline.regionKey, region: inline.regionKey ? node.config[inline.regionKey] : null, handling: connectionDataHandling(kind, node.config) }]
}))
const targets = computed(() => {
  if (props.historical) return Object.entries(props.workflow.provider_profiles_snapshot || {}).map(([id, profile]) => ({ id, profile, providerType: undefined as string | undefined, label: undefined as string | undefined }))
  return references.value.map(({ id, descriptor }) => ({ id, profile: profiles.items.find((profile) => profile.id === id) as ProviderProfile | undefined, providerType: descriptor.providerType, label: descriptor.label }))
})
</script>
<template>
  <div v-if="targets.length || references.length || inlineTargets.length" class="cloud-workflow-notice" data-cloud-notice>
    <p>{{ t('profiles.cloudNotice') }} {{ t('profiles.fileCount', { count: audioCount }) }}</p>
    <p v-if="historical && references.length && !targets.length">{{ t('profiles.missingSnapshot') }}</p>
    <div v-for="target in inlineTargets" :key="target.nodeId" class="provider-target" :data-inline-provider-target="!historical ? target.nodeId : undefined" :data-inline-provider-snapshot-target="historical ? target.nodeId : undefined"><strong>{{ t(historical ? 'profiles.snapshotTarget' : 'profiles.target') }}</strong><span>{{ target.host || t('profiles.unknownTarget') }}<template v-if="target.showRegion"> · {{ target.region || t('profiles.unknownRegion') }}</template></span></div>
    <ProviderTarget v-for="(target, index) in targets" :key="`${target.id}-${index}`" :id="target.id" :profile="target.profile" :provider-type="target.providerType" :snapshot="historical" :label="target.label" :loading="!historical && profiles.loading" />
    <template v-if="!historical"><template v-for="(handling, index) in [...references.map(({ descriptor }) => descriptor.dataHandling), ...inlineTargets.map((target) => target.handling)]" :key="index"><p v-if="handling?.temporaryUploadRetentionHours">{{ t('profiles.temporaryRetention', { hours: handling.temporaryUploadRetentionHours }) }}</p><p v-if="handling?.remoteCancelPendingOnly">{{ t('profiles.cancelPendingOnly') }}</p><p v-if="handling?.cancelBehavior === 'close_stream'">{{ t('profiles.cancelClosesStream') }}</p></template></template>
  </div>
</template>
