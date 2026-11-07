<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import { useWorkflowStore, type WorkflowSnapshot } from '../stores/workflow'
import { useProviderProfilesStore, type ProviderProfile } from '../stores/providerProfiles'
import ProviderTarget from './ProviderTarget.vue'
const props = defineProps<{ workflow: WorkflowSnapshot; audioCount: number; historical?: boolean }>()
const workflowStore = useWorkflowStore()
const profiles = useProviderProfilesStore()
const references = computed(() => props.workflow.nodes.flatMap((node) => {
  const descriptor = workflowStore.registry?.kind(node.type)?.providerProfileInput
  return descriptor ? [{ id: typeof node.config[descriptor.configKey] === 'string' ? node.config[descriptor.configKey] as string : '', descriptor }] : []
}))
const targets = computed(() => {
  if (props.historical) return Object.entries(props.workflow.provider_profiles_snapshot || {}).map(([id, profile]) => ({ id, profile, providerType: undefined as string | undefined, label: undefined as string | undefined }))
  return references.value.map(({ id, descriptor }) => ({ id, profile: profiles.items.find((profile) => profile.id === id) as ProviderProfile | undefined, providerType: descriptor.providerType, label: descriptor.label }))
})
</script>
<template>
  <div v-if="targets.length || references.length" class="cloud-workflow-notice" data-cloud-notice>
    <p>{{ t('profiles.cloudNotice') }} {{ t('profiles.fileCount', { count: audioCount }) }}</p>
    <p v-if="historical && !targets.length">{{ t('profiles.missingSnapshot') }}</p>
    <ProviderTarget v-for="(target, index) in targets" :key="`${target.id}-${index}`" :id="target.id" :profile="target.profile" :provider-type="target.providerType" :snapshot="historical" :label="target.label" :loading="!historical && profiles.loading" />
    <template v-if="!historical"><template v-for="({ descriptor }, index) in references" :key="index"><p v-if="descriptor.dataHandling?.temporaryUploadRetentionHours">{{ t('profiles.temporaryRetention', { hours: descriptor.dataHandling.temporaryUploadRetentionHours }) }}</p><p v-if="descriptor.dataHandling?.remoteCancelPendingOnly">{{ t('profiles.cancelPendingOnly') }}</p></template></template>
  </div>
</template>
