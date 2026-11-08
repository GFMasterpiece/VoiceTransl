<script setup lang="ts">
import { computed, ref } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { workflowVisibleFields, type WorkflowNode, type WorkflowEdge } from '../stores/workflow'
import { useProviderProfilesStore } from '../stores/providerProfiles'
import ProviderTarget from './ProviderTarget.vue'
import NodeProviderConnection from './NodeProviderConnection.vue'
import NodeConfigField from './NodeConfigField.vue'
import { connectionDataHandling, connectionTarget, inlineConnectionActive, inlineConnectionKeys } from '../workflow/providerConnections'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean; edges?: readonly WorkflowEdge[] }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>]; manageProfiles: [] }>()
const profiles = useProviderProfilesStore()
const profileDescriptor = computed(() => props.registry.kind(props.node.type)?.providerProfileInput)
const inlineDescriptor = computed(() => props.registry.kind(props.node.type)?.inlineConnectionInput)
const inlineActive = computed(() => inlineConnectionActive(props.registry.kind(props.node.type), props.node.config))
const inlineHost = computed(() => connectionTarget(props.node.config[inlineDescriptor.value?.baseUrlKey || '']))
const dataHandling = computed(() => connectionDataHandling(props.registry.kind(props.node.type), props.node.config))
const inlineRegion = computed(() => inlineDescriptor.value?.regionKey ? props.node.config[inlineDescriptor.value.regionKey] : null)
const profileId = computed(() => profileDescriptor.value && typeof props.node.config[profileDescriptor.value.configKey] === 'string' ? props.node.config[profileDescriptor.value.configKey] as string : '')
const raw = ref<Record<string, { text: string; value: unknown }>>({})
const fields = computed(() => (props.registry.kind(props.node.type)?.fields || []).filter((field) => field.key !== props.registry.kind(props.node.type)?.resourceInput?.configKey && field.key !== props.registry.kind(props.node.type)?.resourceInput?.collectionKey
  && !inlineConnectionKeys(props.registry.kind(props.node.type)).includes(field.key) && !(inlineActive.value && field.key === profileDescriptor.value?.configKey)))
const visibleKeys = computed(() => new Set(workflowVisibleFields(props.node, props.registry, props.edges).map((field) => field.key)))
const connectionFields = computed(() => fields.value.filter((field) => field.key === profileDescriptor.value?.configKey))
const parameterFields = computed(() => fields.value.filter((field) => field.key !== profileDescriptor.value?.configKey))
const hasConnection = computed(() => !!profileDescriptor.value || !!inlineDescriptor.value)
type Field = (typeof fields.value)[number]
function connection(field: Field) { return field.inputPort ? props.edges?.find((edge) => edge.target === props.node.id && edge.target_port === field.inputPort) : undefined }
function disabled(field: Field) { return props.locked || !!connection(field) }
function value(field: Field) { return props.node.config[field.key] === undefined ? field.default : props.node.config[field.key] }
function text(field: Field): string {
  const current = value(field)
  const buffer = raw.value[field.key]
  if (buffer && JSON.stringify(buffer.value) === JSON.stringify(current)) return buffer.text
  if (current === null || current === undefined) return ''
  return field.type === 'json' && typeof current === 'object' ? JSON.stringify(current, null, 2) : String(current)
}
function edit(field: Field, event: Event) {
  if (disabled(field)) return
  const input = (event.target as HTMLInputElement).value
  let next: unknown = input
  if (field.type === 'boolean') next = input === 'true'
  if ((field.type === 'integer' || field.type === 'number') && input.trim() !== '' && /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(input) && Number.isFinite(Number(input))) next = Number(input)
  if (field.type === 'json') {
    try {
      const parsed: unknown = JSON.parse(input)
      if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) next = parsed
    } catch { /* Keep incomplete text in the draft so dirty and leave protection remain truthful. */ }
  }
  raw.value[field.key] = { text: input, value: next }
  emit('update', { ...props.node.config, [field.key]: next })
}
function error(field: Field) {
  if (field.type === 'json' && typeof value(field) === 'string') {
    try { JSON.parse(String(value(field))) } catch { return t('workflow.jsonInvalid') }
    return t('workflow.jsonObject')
  }
  return props.registry.validateConfig(props.node.type, props.node.config).find((issue) => issue.field === field.key)?.message || ''
}
</script>

<template>
  <div class="node-config-fields" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop>
    <section v-if="hasConnection" class="node-config-section" data-node-section="connection">
      <h4 class="node-config-section-title">{{ t('profiles.connectionSettings') }}</h4>
      <NodeProviderConnection v-if="inlineDescriptor" :node="node" :registry="registry" :locked="locked" @update="emit('update', $event)" />
      <NodeConfigField v-for="field in connectionFields" :key="field.key" :field="field" :text="text(field)" :disabled="disabled(field)" :error="error(field)" :hidden="!visibleKeys.has(field.key)" :placeholder="t('profiles.choose')" @edit="edit(field, $event)" />
      <div class="node-provider-settings">
        <div v-if="inlineActive" class="provider-target" :data-inline-provider-target="node.id"><strong>{{ t('profiles.target') }}</strong><span>{{ inlineHost || t('profiles.unknownTarget') }}<template v-if="inlineDescriptor?.regionKey"> · {{ inlineRegion || t('profiles.unknownRegion') }}</template></span></div>
        <ProviderTarget v-else-if="profileDescriptor" :id="profileId" :profile="profiles.items.find((profile) => profile.id === profileId)" :provider-type="profileDescriptor.providerType" :label="profileDescriptor.label" :loading="profiles.loading" />
        <p v-if="!inlineActive && profiles.error" class="node-field-error">{{ profiles.error }}</p>
        <button v-if="!inlineActive && profileDescriptor" type="button" class="text-button" data-open-profiles :disabled="locked" @click="emit('manageProfiles')">{{ t('profiles.manage') }}</button>
        <details class="node-help-details" data-node-data-handling><summary>{{ t('profiles.dataHandling') }}</summary><div><p>{{ t('profiles.cloudNotice') }}</p><p v-if="dataHandling?.temporaryUploadRetentionHours">{{ t('profiles.temporaryRetention', { hours: dataHandling.temporaryUploadRetentionHours }) }}</p><p v-if="dataHandling?.remoteCancelPendingOnly">{{ t('profiles.cancelPendingOnly') }}</p><p v-if="dataHandling?.cancelBehavior === 'close_stream'">{{ t('profiles.cancelClosesStream') }}</p></div></details>
      </div>
    </section>
    <section v-if="parameterFields.length" class="node-config-section node-parameter-fields" data-node-section="parameters">
      <h4 v-if="hasConnection" class="node-config-section-title">{{ t('profiles.processingParameters') }}</h4>
      <NodeConfigField v-for="field in parameterFields" :key="field.key" :field="field" :text="text(field)" :disabled="disabled(field)" :error="error(field)" :hidden="!visibleKeys.has(field.key)" @edit="edit(field, $event)" />
    </section>
  </div>
</template>
