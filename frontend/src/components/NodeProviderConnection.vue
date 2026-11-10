<script setup lang="ts">
import { computed, useId } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { useWorkflowStore, type WorkflowNode } from '../stores/workflow'
import { workflowCredentials } from '../composables/workflowCredentials'
import NodeConfigField from './NodeConfigField.vue'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>] }>()
const credentialId = useId()
const store = useWorkflowStore()
const credentials = workflowCredentials(store.credentialContext)
const kind = computed(() => props.registry.kind(props.node.type)!)
const descriptor = computed(() => kind.value.inlineConnectionInput!)
const fields = computed(() => [descriptor.value.baseUrlKey, descriptor.value.timeoutKey, descriptor.value.regionKey]
  .filter((key): key is string => key !== null).map((key) => kind.value.fields.find((field) => field.key === key)!))
const operation = computed(() => credentials.operation(props.node.id))
const keyValue = computed(() => credentials.value(props.node.id))
const hasReference = computed(() => !!props.node.config[descriptor.value.credentialRefKey])
function value(key: string) { return (props.node.config[key] === undefined ? kind.value.fields.find((field) => field.key === key)?.default : props.node.config[key]) ?? '' }
function edit(key: string, input: string) {
  if (props.locked) return
  const field = kind.value.fields.find((field) => field.key === key)!
  const next = field.type === 'integer' && input.trim() && Number.isInteger(Number(input)) ? Number(input)
    : [descriptor.value.regionKey].includes(key) && !input ? null : input
  emit('update', { ...props.node.config, [key]: next })
}
function editKey(event: Event) { if (!props.locked) credentials.set(props.node.id, (event.target as HTMLInputElement).value) }
function clearKey() { if (!props.locked) credentials.clear(props.node.id) }
function discardKey() { if (!props.locked) credentials.forget(props.node.id) }
function error(key: string) { return props.registry.validateConfig(props.node.type, props.node.config).find((issue) => issue.field === key)?.message || '' }
</script>
<template>
  <div class="node-inline-connection" data-node-connection>
      <NodeConfigField v-for="field in fields" :key="field.key" :field="field" connectable :socket-disabled="locked" :languages="registry.capabilities.languages" :text="String(value(field.key))" :disabled="locked" :error="error(field.key)" connection @edit="edit(field.key, $event)" />
      <div class="node-credential-group">
        <label class="node-config-row"><span class="node-config-label">{{ t('connections.apiKey') }}</span><input type="password" data-credential-input :value="keyValue" :disabled="locked" :aria-label="t('connections.apiKey')" :aria-describedby="`${credentialId}-status`" autocomplete="new-password" spellcheck="false" :placeholder="hasReference ? t('connections.keyUnchanged') : t('connections.keyPlaceholder')" @input="editKey" /></label>
        <p :id="`${credentialId}-status`" class="node-credential-status" role="status" data-credential-status>{{ operation === 'clear' ? t('connections.keyClearing') : operation === 'set' ? t('connections.keyPending') : operation === 'use_default' ? t('settings.pendingDefault') : hasReference ? t('connections.keyStored') : t('connections.keyNotStoredDirect') }}</p>
        <div class="node-credential-actions" data-credential-actions><button class="text-button" type="button" data-credential-clear :disabled="locked || (!hasReference && !operation)" @click="clearKey">{{ t('connections.clearKey') }}</button><button v-if="operation" class="text-button" type="button" data-credential-discard :disabled="locked" @click="discardKey">{{ t('connections.discardKey') }}</button></div>
      </div>
  </div>
</template>
