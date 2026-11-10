<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import CanvasDialog from './CanvasDialog.vue'
import { t } from '../i18n'
import type { CapabilityNode } from '../workflow/capabilities'
import type { NodeRegistry } from '../workflow/nodeRegistry'
import { groupNodeKinds, nodeCategoryLabel } from '../workflow/nodeCategories'
import { libraryTypeLabel, nodeLibraryDefaults, nodeLibrarySearch } from '../workflow/nodeLibrary'
const props = defineProps<{ registry: NodeRegistry; kinds: readonly CapabilityNode[]; disabledReason: (type: string) => string; actionError?: string }>()
const emit = defineEmits<{ close: []; add: [type: string] }>()
const query = ref(''), category = ref<string | null | undefined>(undefined), inputType = ref(''), outputType = ref(''), selected = ref(''), details = ref(false)
const media = typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 700px)') : null
const narrow = ref(media?.matches || false)
const backButton = ref<HTMLButtonElement | null>(null), searchInput = ref<HTMLInputElement | null>(null)
let detailsTrigger: HTMLElement | null = null
function mediaChanged(event: MediaQueryListEvent) { narrow.value = event.matches }
watch([narrow, details], () => {
  const previous = document.activeElement
  const wasInLibrary = previous instanceof Element && !!previous.closest('.node-library')
  void nextTick(() => {
    const active = document.activeElement
    if (active instanceof Element && active.closest('.node-library-dialog [role="dialog"]') && !active.closest('[hidden], [inert]')) return
    if (wasInLibrary && (previous && !previous.isConnected || active instanceof Element && active.closest('.node-library [hidden], .node-library [inert]'))) (narrow.value && details.value ? backButton.value : searchInput.value)?.focus()
  })
})
onMounted(() => media?.addEventListener?.('change', mediaChanged))
onUnmounted(() => media?.removeEventListener?.('change', mediaChanged))
function showDetails(type: string, trigger?: EventTarget | null) { selected.value = type; details.value = true; detailsTrigger = trigger instanceof HTMLElement ? trigger : null; if (narrow.value) void nextTick(() => backButton.value?.focus()) }
function backToList() { details.value = false; void nextTick(() => (detailsTrigger?.isConnected ? detailsTrigger : searchInput.value)?.focus()) }
const composing = ref(false), resultsElement = ref<HTMLElement | null>(null), id = useId()
const groups = computed(() => groupNodeKinds(props.kinds, ''))
function ports(kind: CapabilityNode, direction: 'input' | 'output') {
  const config = nodeLibraryDefaults(kind, props.registry), node = { type: kind.type, config }
  return (direction === 'input' ? props.registry.inputPortsForNode(kind.type, config) : kind.outputPorts).map(port => ({ port, definition: props.registry.port(port, node) }))
}
function types(direction: 'input' | 'output') {
  const ids = new Set(props.kinds.flatMap(kind => ports(kind, direction).flatMap(item => item.definition ? [item.definition.typeId] : [])))
  return [...ids].map(id => ({ id, label: libraryTypeLabel(id, props.registry) }))
}
const inputTypes = computed(() => types('input')), outputTypes = computed(() => types('output'))
watch(groups, entries => { if (category.value !== undefined && !entries.some(group => group.id === category.value)) category.value = undefined })
watch(inputTypes, entries => { if (inputType.value && !entries.some(type => type.id === inputType.value)) inputType.value = '' })
watch(outputTypes, entries => { if (outputType.value && !entries.some(type => type.id === outputType.value)) outputType.value = '' })
const results = computed(() => props.kinds.filter(kind => (category.value === undefined || (kind.category?.id ?? null) === category.value)
  && nodeLibrarySearch(kind, props.registry, query.value)
  && (!inputType.value || ports(kind, 'input').some(item => item.definition?.typeId === inputType.value))
  && (!outputType.value || ports(kind, 'output').some(item => item.definition?.typeId === outputType.value))))
watch(results, entries => { if (!entries.some(kind => kind.type === selected.value)) selected.value = entries[0]?.type || '' }, { immediate: true })
const current = computed(() => results.value.find(kind => kind.type === selected.value))
const preview = computed(() => {
  const kind = current.value
  if (!kind) return null
  const config = nodeLibraryDefaults(kind, props.registry)
  return { kind, config, fields: props.registry.fieldsForNode(kind.type, config).filter(field => field.key !== kind.inlineConnectionInput?.credentialRefKey && field.key !== kind.resourceInput?.configKey && field.key !== kind.resourceInput?.collectionKey), inputs: ports(kind, 'input'), outputs: ports(kind, 'output') }
})
function value(key: string) {
  if (!preview.value) return ''
  const dynamic = preview.value.kind.dynamicInputs
  if (dynamic && key.startsWith(`${dynamic.configKey}.`)) return (preview.value.config[dynamic.configKey] as { id: string; value: string }[]).find(item => item.id === key.split('.')[1])?.value || ''
  const current = preview.value.config[key]
  return current == null ? '' : typeof current === 'object' ? JSON.stringify(current) : String(current)
}
function portLabel(port: string) { return preview.value?.fields.find(field => field.inputPort === port)?.label || props.registry.port(port, { type: preview.value!.kind.type, config: preview.value!.config })?.label || port }
function add(type: string) { selected.value = type; if (!props.disabledReason(type)) emit('add', type) }
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229 || composing.value) return
  if (!(event.target instanceof Element) || !event.target.closest('.canvas-node-search, .node-library-results')) return
  const fromDetailsButton = !!event.target.closest('[data-library-item-details]')
  if (fromDetailsButton && ['Enter', ' '].includes(event.key)) return
  if (event.target instanceof HTMLInputElement && ['Home', 'End'].includes(event.key)) return
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter'].includes(event.key)) return
  event.preventDefault(); event.stopPropagation()
  if (event.key === 'Enter') { if (current.value) add(current.value.type); return }
  const entries = results.value; if (!entries.length) return
  const index = entries.findIndex(kind => kind.type === selected.value)
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? entries.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + entries.length) % entries.length
  selected.value = entries[next]!.type
  void nextTick(() => {
    const row = resultsElement.value?.querySelector<HTMLElement>('[aria-selected="true"]')
    row?.scrollIntoView?.({ block: 'nearest' })
    if (fromDetailsButton) row?.querySelector<HTMLButtonElement>('[data-library-item-details]')?.focus()
  })
}
</script>
<template>
  <CanvasDialog class="node-library-dialog" :title="t('workflow.nodeLibraryTitle')" @close="emit('close')">
    <div class="node-library" :class="{ 'show-details': details }" @keydown="keydown" @compositionstart="composing = true" @compositionend="composing = false">
      <div class="node-library-search">
        <label class="canvas-node-search"><span class="visually-hidden">{{ t('workflow.searchNodes') }}</span><input ref="searchInput" v-model="query" autofocus type="search" :placeholder="t('workflow.searchNodes')" :aria-label="t('workflow.searchNodes')" :aria-controls="`${id}-results`" :aria-activedescendant="selected ? `${id}-${selected}` : undefined" /></label>
        <div class="node-library-filters">
        <label><span>{{ t('workflow.libraryInputType') }}</span><select v-model="inputType" data-library-input-type><option value="">{{ t('workflow.libraryAllTypes') }}</option><option v-for="type in inputTypes" :key="type.id" :value="type.id">{{ type.label }}</option></select></label>
        <label><span>{{ t('workflow.libraryOutputType') }}</span><select v-model="outputType" data-library-output-type><option value="">{{ t('workflow.libraryAllTypes') }}</option><option v-for="type in outputTypes" :key="type.id" :value="type.id">{{ type.label }}</option></select></label>
        </div>
        <p v-if="actionError" class="node-library-action-error" role="alert">{{ actionError }}</p>
      </div>
      <div class="node-library-columns">
        <nav class="node-library-categories" :aria-label="t('workflow.libraryCategories')">
          <button type="button" :aria-pressed="category === undefined" @click="category = undefined">{{ t('workflow.libraryAllNodes') }}</button>
          <button v-for="group in groups" :key="group.id ?? ''" type="button" :data-library-category="group.id ?? ''" :aria-pressed="category === group.id" @click="category = group.id">{{ group.label }}</button>
        </nav>
        <div class="node-library-results-pane" :hidden="narrow && details" :inert="narrow && details ? true : undefined">
          <div :id="`${id}-results`" ref="resultsElement" class="node-library-results" role="listbox" :aria-label="t('workflow.nodeLibraryTitle')">
            <div v-for="entry in results" :id="`${id}-${entry.type}`" :key="entry.type" role="option" tabindex="-1" :data-add-kind="entry.type" :aria-selected="selected === entry.type" :aria-disabled="!!disabledReason(entry.type)" :title="disabledReason(entry.type) || entry.label" @mouseenter="selected = entry.type" @focusin="selected = entry.type" @click="add(entry.type)"><span class="node-library-name">{{ entry.addLabel }}<code>{{ entry.type }}</code></span><small>{{ nodeCategoryLabel(entry.category) }}</small><small v-if="disabledReason(entry.type)" class="node-library-reason">{{ disabledReason(entry.type) }}</small><button type="button" class="text-button" data-library-item-details :aria-label="t('workflow.libraryDetailsFor', { name: entry.label })" @click.stop="showDetails(entry.type, $event.currentTarget)">{{ t('workflow.libraryDetails') }}</button></div>
            <p v-if="!results.length" role="status">{{ t('workflow.noMatchingNodes') }}</p>
          </div>
          <button v-if="current && narrow" class="secondary-button compact-button node-library-details-toggle" type="button" @click="showDetails(current.type, $event.currentTarget)">{{ t('workflow.libraryShowDetails') }}</button>
        </div>
        <aside class="node-library-details" :hidden="narrow && !details" :inert="narrow && !details ? true : undefined" :aria-label="t('workflow.libraryDetails')">
          <button v-if="narrow" ref="backButton" class="text-button node-library-details-back" type="button" @click="backToList">{{ t('workflow.libraryBack') }}</button>
          <p v-if="narrow && details && !preview" role="status">{{ t('workflow.noMatchingNodes') }}</p>
          <article v-if="preview" class="node-library-preview" data-node-library-preview>
            <header><strong>{{ preview.kind.label }}</strong><code>{{ preview.kind.type }}</code></header>
            <div class="node-library-preview-ports">
              <section v-for="direction in (['inputs', 'outputs'] as const)" :key="direction"><h3>{{ t(direction === 'inputs' ? 'workflow.libraryInputs' : 'workflow.libraryOutputs') }}</h3><div v-for="item in preview[direction]" :key="item.port" class="node-library-preview-port"><span class="node-library-port-dot" :style="{ '--port-color': registry.capabilities.dataTypes.find(type => type.id === item.definition?.typeId)?.presentation?.color }"></span><span>{{ portLabel(item.port) }}<small>{{ item.definition ? libraryTypeLabel(item.definition.typeId, registry) : item.port }}<template v-if="direction === 'inputs'"> · {{ t(preview.kind.requiredInputPorts.includes(item.port) ? 'workflow.libraryRequired' : 'workflow.optionalPort') }}</template></small></span></div><span v-if="!preview[direction].length" class="node-library-none">—</span></section>
            </div>
            <section v-if="preview.fields.length" class="node-library-preview-fields"><h3>{{ t('workflow.libraryFields') }}</h3><dl><template v-for="field in preview.fields" :key="field.key"><dt>{{ field.label }}</dt><dd><code>{{ value(field.key) || '—' }}</code></dd></template></dl></section>
          </article>
          <div v-if="current" class="node-library-preview-action"><small v-if="disabledReason(current.type)">{{ disabledReason(current.type) }}</small><button class="primary-button compact-button" type="button" data-library-add-selected :disabled="!!disabledReason(current.type)" @click="add(current.type)">{{ t('workflow.nodeLibraryTitle') }}</button></div>
        </aside>
      </div>
    </div>
  </CanvasDialog>
</template>
