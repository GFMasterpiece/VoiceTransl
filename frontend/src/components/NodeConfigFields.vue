<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { workflowFieldHeight, type WorkflowNode } from '../stores/workflow'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>] }>()
const id = useId()
const raw = ref<Record<string, { text: string; value: unknown }>>({})
const fields = computed(() => (props.registry.kind(props.node.type)?.fields || []).filter((field) => field.key !== props.registry.kind(props.node.type)?.resourceInput?.configKey))
type Field = (typeof fields.value)[number]
function value(field: Field) { return props.node.config[field.key] === undefined ? field.default : props.node.config[field.key] }
function text(field: Field): string {
  const current = value(field)
  const buffer = raw.value[field.key]
  if (buffer && JSON.stringify(buffer.value) === JSON.stringify(current)) return buffer.text
  if (current === null || current === undefined) return ''
  return field.type === 'json' && typeof current === 'object' ? JSON.stringify(current, null, 2) : String(current)
}
function edit(field: Field, event: Event) {
  if (props.locked) return
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
    <label v-for="field in fields" :key="field.key" class="node-config-row" :class="{ 'is-json': field.type === 'json', 'has-error': !!error(field) }" :style="{ height: `${workflowFieldHeight(field)}px` }" :title="field.description || field.label">
      <span class="node-config-label" :title="field.label">{{ field.label }}</span>
      <select v-if="field.type === 'select' || field.type === 'boolean'" :data-field="field.key" :value="text(field)" :disabled="locked" :aria-label="field.label" :aria-invalid="!!error(field)" :aria-describedby="error(field) ? `${id}-${field.key}-error` : undefined" @change="edit(field, $event)">
        <template v-if="field.type === 'boolean'"><option value="true">{{ t('workflow.yes') }}</option><option value="false">{{ t('workflow.no') }}</option></template>
        <template v-else><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></template>
      </select>
      <textarea v-else-if="field.type === 'json'" :data-field="field.key" :value="text(field)" :disabled="locked" :aria-label="field.label" :aria-invalid="!!error(field)" :aria-describedby="error(field) ? `${id}-${field.key}-error` : undefined" spellcheck="false" @input="edit(field, $event)" />
      <input v-else type="text" :inputmode="field.type === 'number' || field.type === 'integer' ? 'decimal' : undefined" :data-field="field.key" :value="text(field)" :disabled="locked" :aria-label="field.label" :aria-invalid="!!error(field)" :aria-describedby="error(field) ? `${id}-${field.key}-error` : undefined" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :step="field.type === 'integer' ? 1 : undefined" @input="edit(field, $event)" />
      <small v-if="error(field)" :id="`${id}-${field.key}-error`" class="node-field-error" :title="error(field)">{{ error(field) }}</small>
    </label>
  </div>
</template>
