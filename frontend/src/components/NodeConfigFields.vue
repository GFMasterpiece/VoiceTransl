<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { t } from '../i18n'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { workflowVisibleFields, type WorkflowNode, type WorkflowEdge } from '../stores/workflow'
import NodeProviderConnection from './NodeProviderConnection.vue'
import NodeConfigField from './NodeConfigField.vue'
import { inlineConnectionKeys } from '../workflow/providerConnections'

const props = defineProps<{ node: WorkflowNode; registry: NodeRegistry; locked: boolean; edges?: readonly WorkflowEdge[]; portDescription?: (port: string) => string }>()
const emit = defineEmits<{ update: [config: Record<string, unknown>]; socketStart: [port: string, event: PointerEvent]; socketEnd: [event: PointerEvent]; socketCancel: [] }>()
const inlineDescriptor = computed(() => props.registry.kind(props.node.type)?.inlineConnectionInput)
const raw = ref<Record<string, { text: string; value: unknown }>>({})
const fields = computed(() => props.registry.fieldsForNode(props.node.type, props.node.config).filter((field) => field.key !== props.registry.kind(props.node.type)?.resourceInput?.configKey && field.key !== props.registry.kind(props.node.type)?.resourceInput?.collectionKey && field.key !== props.registry.kind(props.node.type)?.resourceInput?.groupKey
  && !inlineConnectionKeys(props.registry.kind(props.node.type)).includes(field.key)))
const visibleKeys = computed(() => new Set(workflowVisibleFields(props.node, props.registry, props.edges).map((field) => field.key)))
const parameterFields = fields
const hasConnection = computed(() => !!inlineDescriptor.value)

function fieldKey(event: KeyboardEvent) {
  if (event.target instanceof Element && event.target.closest('.node-field-socket') && (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) return
  if (event.key === 'Escape') emit('socketCancel')
  event.stopPropagation()
}
type Field = (typeof fields.value)[number]
function connection(field: Field) { return field.inputPort ? props.edges?.find((edge) => edge.target === props.node.id && edge.target_port === field.inputPort) : undefined }
function socketStyle(field: Field) { const typeId = field.inputPort ? props.registry.port(field.inputPort, props.node)?.typeId : null; const color = props.registry.capabilities.dataTypes.find(type => type.id === typeId)?.presentation?.color; return color ? { '--port-color': color } : undefined }
function disabled(field: Field) { return props.locked || !!connection(field) }
const dynamic = computed(() => props.registry.kind(props.node.type)?.dynamicInputs)
const slots = computed(() => dynamic.value && Array.isArray(props.node.config[dynamic.value.configKey]) ? props.node.config[dynamic.value.configKey] as { id: string; value: string }[] : [])
function slotId(field: Field) { return dynamic.value && field.key.startsWith(`${dynamic.value.configKey}.`) ? field.key.split('.')[1] : null }
function value(field: Field) { const id = slotId(field); return id ? slots.value.find((slot) => slot.id === id)?.value : props.node.config[field.key] === undefined ? field.default : props.node.config[field.key] }
watch(() => props.node.config, () => { for (const key of Object.keys(raw.value)) if (!fields.value.some((field) => field.key === key)) delete raw.value[key] })
function changeSlots(items: { id: string; value: string }[]) { if (!props.locked && dynamic.value) emit('update', { ...props.node.config, [dynamic.value.configKey]: items }) }
function addSlot() { if (!dynamic.value || slots.value.length >= dynamic.value.maxItems) return; let id = ''; do { id = `s${crypto.randomUUID().replace(/-/g, '').slice(0, 20)}` } while (slots.value.some((slot) => slot.id === id)); changeSlots([...slots.value, { id, value: dynamic.value.field.default as string }]) }
function removeSlot(id: string) { if (dynamic.value && slots.value.length > dynamic.value.minItems) changeSlots(slots.value.filter((slot) => slot.id !== id)) }
function moveSlot(id: string, step: number) { const items = [...slots.value]; const index = items.findIndex((slot) => slot.id === id); const target = index + step; if (index < 0 || target < 0 || target >= items.length) return; [items[index], items[target]] = [items[target]!, items[index]!]; changeSlots(items) }

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
  const id = slotId(field)
  if (id) changeSlots(slots.value.map((slot) => slot.id === id ? { ...slot, value: next as string } : slot))
  else emit('update', { ...props.node.config, [field.key]: next })
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
  <div class="node-config-fields" @pointerdown.stop @click.stop @dblclick.stop @keydown="fieldKey" @keyup.stop @wheel.stop>
    <section v-if="hasConnection" class="node-config-section" data-node-section="connection">
      <h4 class="node-config-section-title">{{ t('connections.connectionSettings') }}</h4>
      <template v-for="field in parameterFields" :key="field.key">
        <NodeConfigField :field="field" connectable :socket-disabled="locked" :socket-label="field.inputPort ? portDescription?.(field.inputPort) : undefined" :socket-style="socketStyle(field)" @socket-start="(port, event) => emit('socketStart', port, event)" @socket-end="emit('socketEnd', $event)" @socket-cancel="emit('socketCancel')" :languages="registry.capabilities.languages" :text="text(field)" :disabled="disabled(field)" :error="error(field)" :hidden="!visibleKeys.has(field.key)" @edit="edit(field, $event)" />
        <div v-if="slotId(field)" class="node-slot-actions" role="group" :aria-label="field.label" :data-slot="slotId(field)">

          <button class="text-button" type="button" :disabled="locked || slots[0]?.id === slotId(field)" @click="moveSlot(slotId(field)!, -1)">{{ t('workflow.moveInputUp') }}</button>
          <button class="text-button" type="button" :disabled="locked || slots[slots.length - 1]?.id === slotId(field)" @click="moveSlot(slotId(field)!, 1)">{{ t('workflow.moveInputDown') }}</button>
          <button class="text-button" type="button" :disabled="locked || slots.length <= dynamic!.minItems" @click="removeSlot(slotId(field)!)">{{ t('workflow.removeInput') }}</button>
        </div>
      </template>
      <button v-if="dynamic" class="secondary-button compact-button node-add-input" type="button" data-add-input :disabled="locked || slots.length >= dynamic.maxItems" @click="addSlot">{{ t('workflow.addInput') }}</button>
      <NodeProviderConnection :node="node" :registry="registry" :locked="locked" @update="emit('update', $event)" />
    </section>
    <section v-if="!hasConnection && parameterFields.length" class="node-config-section node-parameter-fields" data-node-section="parameters">
      <template v-for="field in parameterFields" :key="field.key">
        <NodeConfigField :field="field" connectable :socket-disabled="locked" :socket-label="field.inputPort ? portDescription?.(field.inputPort) : undefined" :socket-style="socketStyle(field)" @socket-start="(port, event) => emit('socketStart', port, event)" @socket-end="emit('socketEnd', $event)" @socket-cancel="emit('socketCancel')" :languages="registry.capabilities.languages" :text="text(field)" :disabled="disabled(field)" :error="error(field)" :hidden="!visibleKeys.has(field.key)" @edit="edit(field, $event)" />
        <div v-if="slotId(field)" class="node-slot-actions" role="group" :aria-label="field.label" :data-slot="slotId(field)">

          <button class="text-button" type="button" :disabled="locked || slots[0]?.id === slotId(field)" @click="moveSlot(slotId(field)!, -1)">{{ t('workflow.moveInputUp') }}</button>
          <button class="text-button" type="button" :disabled="locked || slots[slots.length - 1]?.id === slotId(field)" @click="moveSlot(slotId(field)!, 1)">{{ t('workflow.moveInputDown') }}</button>
          <button class="text-button" type="button" :disabled="locked || slots.length <= dynamic!.minItems" @click="removeSlot(slotId(field)!)">{{ t('workflow.removeInput') }}</button>
        </div>
      </template>
      <button v-if="dynamic" class="secondary-button compact-button node-add-input" type="button" data-add-input :disabled="locked || slots.length >= dynamic.maxItems" @click="addSlot">{{ t('workflow.addInput') }}</button>
    </section>
  </div>
</template>
