<script setup lang="ts">
import { computed, ref } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { workflowVisibleFields, type WorkflowNode, type WorkflowEdge } from '../stores/workflow'
import NodeProviderConnection from './NodeProviderConnection.vue'
import NodeConfigField from './NodeConfigField.vue'
import { inlineConnectionKeys } from '../workflow/providerConnections'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean; edges?: readonly WorkflowEdge[] }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>] }>()
const inlineDescriptor = computed(() => props.registry.kind(props.node.type)?.inlineConnectionInput)
const raw = ref<Record<string, { text: string; value: unknown }>>({})
const fields = computed(() => (props.registry.kind(props.node.type)?.fields || []).filter((field) => field.key !== props.registry.kind(props.node.type)?.resourceInput?.configKey && field.key !== props.registry.kind(props.node.type)?.resourceInput?.collectionKey
  && !inlineConnectionKeys(props.registry.kind(props.node.type)).includes(field.key)))
const visibleKeys = computed(() => new Set(workflowVisibleFields(props.node, props.registry, props.edges).map((field) => field.key)))
const parameterFields = fields
const hasConnection = computed(() => !!inlineDescriptor.value)
const legacyIssue = computed(() => props.registry.validateConfig(props.node.type, props.node.config).find((issue) => issue.code === 'LEGACY_CONNECTION_UNSUPPORTED'))

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
function edit(field: Field, input: string) {
  if (disabled(field)) return
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
      <h4 class="node-config-section-title">{{ t('connections.connectionSettings') }}</h4>
      <p v-if="legacyIssue" class="node-field-error" role="alert" data-cloud-rebuild>{{ legacyIssue.message }}</p>
      <NodeProviderConnection v-else :node="node" :registry="registry" :locked="locked" @update="emit('update', $event)" />
    </section>
    <section v-if="parameterFields.length" class="node-config-section node-parameter-fields" data-node-section="parameters">
      <h4 v-if="hasConnection" class="node-config-section-title">{{ t('connections.processingParameters') }}</h4>
      <NodeConfigField v-for="field in parameterFields" :key="field.key" :field="field" :languages="registry.capabilities.languages" :text="text(field)" :disabled="disabled(field)" :error="error(field)" :hidden="!visibleKeys.has(field.key)" @edit="edit(field, $event)" />
    </section>
  </div>
</template>
