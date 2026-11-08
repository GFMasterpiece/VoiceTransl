<script setup lang="ts">
import { computed, useId } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { useWorkflowStore, type WorkflowNode } from '../stores/workflow'
import { useProviderProfilesStore } from '../stores/providerProfiles'
import { workflowCredentials } from '../composables/workflowCredentials'
import { inlineConnectionActive } from '../workflow/providerConnections'
import NodeConfigField from './NodeConfigField.vue'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>] }>()
const credentialId = useId()
const store = useWorkflowStore()
const credentials = workflowCredentials(store.credentialContext)
const profiles = useProviderProfilesStore()
const kind = computed(() => props.registry.kind(props.node.type)!)
const descriptor = computed(() => kind.value.inlineConnectionInput!)
const active = computed(() => inlineConnectionActive(kind.value, props.node.config))
const mode = computed(() => descriptor.value.modeKey ? kind.value.fields.find((field) => field.key === descriptor.value.modeKey) : null)
const fields = computed(() => [descriptor.value.baseUrlKey, descriptor.value.timeoutKey, descriptor.value.regionKey, descriptor.value.apiKeyEnvKey]
  .filter((key): key is string => key !== null).map((key) => kind.value.fields.find((field) => field.key === key)!))
const operation = computed(() => credentials.operation(props.node.id))
const keyValue = computed(() => credentials.value(props.node.id))
const hasReference = computed(() => !!props.node.config[descriptor.value.credentialRefKey])
function value(key: string) { return (props.node.config[key] === undefined ? kind.value.fields.find((field) => field.key === key)?.default : props.node.config[key]) ?? '' }
function setMode(event: Event) {
  if (props.locked || !descriptor.value.modeKey) return
  const next = (event.target as HTMLSelectElement).value
  const config = { ...props.node.config, [descriptor.value.modeKey]: next }
  if (next === descriptor.value.modeValue) {
    const legacy = kind.value.providerProfileInput
    const profile = profiles.items.find((profile) => profile.id === config[legacy?.configKey || ''])
    const defaults: Record<string, unknown> = { [descriptor.value.baseUrlKey]: profile?.base_url ?? legacy?.defaults.base_url,
      [descriptor.value.timeoutKey]: profile?.default_timeout ?? legacy?.defaults.default_timeout,
      ...(descriptor.value.regionKey ? { [descriptor.value.regionKey]: profile?.region ?? null } : {}), ...(descriptor.value.apiKeyEnvKey ? { [descriptor.value.apiKeyEnvKey]: profile?.api_key_env ?? null } : {}) }
    for (const field of fields.value) if (config[field.key] === undefined) config[field.key] = defaults[field.key] ?? field.default
  }
  emit('update', config)
}
function edit(key: string, event: Event) {
  if (props.locked) return
  const input = (event.target as HTMLInputElement).value
  const field = kind.value.fields.find((field) => field.key === key)!
  const next = field.type === 'integer' && input.trim() && Number.isInteger(Number(input)) ? Number(input)
    : [descriptor.value.regionKey, descriptor.value.apiKeyEnvKey].includes(key) && !input ? null : input
  emit('update', { ...props.node.config, [key]: next })
}
function editKey(event: Event) { if (!props.locked) credentials.set(props.node.id, (event.target as HTMLInputElement).value) }
function clearKey() { if (!props.locked) credentials.clear(props.node.id) }
function discardKey() { if (!props.locked) credentials.forget(props.node.id) }
function error(key: string) { return props.registry.validateConfig(props.node.type, props.node.config).find((issue) => issue.field === key)?.message || '' }
</script>
<template>
  <div class="node-inline-connection" data-node-connection>
    <label v-if="mode && descriptor.modeKey" class="node-config-row"><span class="node-config-label">{{ t('profiles.connectionMode') }}</span><select :value="value(descriptor.modeKey)" :disabled="locked" data-connection-mode @change="setMode"><option v-if="!mode.options.some((option) => option.value === value(descriptor.modeKey!))" :value="value(descriptor.modeKey)">{{ t('profiles.unknownOption', { value: String(value(descriptor.modeKey)) }) }}</option><option v-for="option in mode.options" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
    <template v-if="active">
      <NodeConfigField v-for="field in fields" :key="field.key" :field="field" :text="String(value(field.key))" :disabled="locked" :error="error(field.key)" connection @edit="edit(field.key, $event)" />
      <div class="node-credential-group">
        <label class="node-config-row"><span class="node-config-label">{{ t('profiles.apiKey') }}</span><input type="password" data-credential-input :value="keyValue" :disabled="locked" :aria-label="t('profiles.apiKey')" :aria-describedby="`${credentialId}-status`" autocomplete="new-password" spellcheck="false" :placeholder="hasReference ? t('profiles.keyUnchanged') : t('profiles.keyPlaceholder')" @input="editKey" /></label>
        <p :id="`${credentialId}-status`" class="node-credential-status" role="status" data-credential-status>{{ operation === 'clear' ? t('profiles.keyClearing') : operation === 'set' ? t('profiles.keyPending') : hasReference ? t('profiles.keyStored') : t(descriptor.apiKeyEnvKey ? 'profiles.keyNotStored' : 'profiles.keyNotStoredDirect') }}</p>
        <div class="node-credential-actions" data-credential-actions><button class="text-button" type="button" data-credential-clear :disabled="locked || (!hasReference && !operation)" @click="clearKey">{{ t('profiles.clearKey') }}</button><button v-if="operation" class="text-button" type="button" data-credential-discard :disabled="locked" @click="discardKey">{{ t('profiles.discardKey') }}</button></div>
        <details class="node-help-details" data-node-credential-help><summary>{{ t('profiles.keyStorageHelp') }}</summary><p class="node-credential-help">{{ t('profiles.keyHint') }}</p></details>
      </div>
    </template>
  </div>
</template>
