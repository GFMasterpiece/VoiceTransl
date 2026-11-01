<script setup lang="ts">
import { t, locale, message, messageRef, hasMessage } from '../i18n'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  workflowErrorLabel, workflowIssueLabel,
  WORKFLOW_NODE_HEIGHT,
  WORKFLOW_NODE_WIDTH,
  executionSignature,
  inputPortForNode as registeredInputPortForNode,
  isTerminalNode,
  nodeLabel as registeredNodeLabel,
  outputPortForNode as registeredOutputPortForNode,
  issueText,
  useWorkflowStore,
  validateNodeConfig as registeredValidateNodeConfig,
  type WorkflowNode,
} from '../stores/workflow'

const props = defineProps<{ projectId: string; audioId: string; audioName: string }>()
const store = useWorkflowStore()
const canvas = ref<HTMLDivElement | null>(null)
const stage = ref<SVGSVGElement | null>(null)
const drag = ref<{ nodeId: string; startX: number; startY: number; x: number; y: number } | null>(null)
const panning = ref<{ startX: number; startY: number; x: number; y: number } | null>(null)
const connectFrom = ref<string | null>(null)
const settingsOpen = ref(false)
const historyOpen = ref(false)
const discardPrompt = ref(false)
let pendingClose: (() => void) | null = null
const genericDraft = ref<Record<string, unknown>>({})
const settingsErrorState = messageRef()
const settingsError = settingsErrorState.text
const selectedKind = computed(() => selected.value && registry.value ? registry.value.kind(selected.value.type) : null)
const genericFields = computed(() => selectedKind.value?.fields || [])
function fieldValue(field: { key: string; default: unknown }) {
  const value = genericDraft.value[field.key]
  return value === undefined ? (field.default ?? '') : value
}
function setFieldValue(field: { key: string; type: string }, value: unknown) {
  genericDraft.value = { ...genericDraft.value, [field.key]: field.type === 'integer' || field.type === 'number' ? Number(value) : value }
}
function jsonFieldValue(field: { key: string }) {
  const value = genericDraft.value[field.key]
  return value === undefined ? '{}' : JSON.stringify(value, null, 2)
}
function setJsonFieldValue(field: { key: string }, event: Event) {
  try {
    const value = JSON.parse((event.target as HTMLTextAreaElement).value) as unknown
    if (!value || typeof value !== 'object' || Array.isArray(value)) { settingsErrorState.set(message('workflow.jsonObject')); return }
    genericDraft.value = { ...genericDraft.value, [field.key]: value }
    settingsErrorState.set('')
  } catch { settingsErrorState.set(message('workflow.jsonInvalid')) }
}
const selected = computed(() => store.selectedNode)
const currentInput = computed(() => store.draft.nodes.find((node) => registry.value?.kind(node.type)?.isSource))
const controlsLocked = computed(() => store.saving || store.validating || !!store.activeRun)
const canValidateAndSave = computed(() => store.dirty && !store.saving && !store.loading && !store.validating && !store.activeRun)
const canRun = computed(() => !store.dirty && !store.saving && !!store.document && !store.activeRun && !store.actionLoading)

const nodeWidth = WORKFLOW_NODE_WIDTH
const nodeHeight = WORKFLOW_NODE_HEIGHT
const registry = computed(() => store.registry)
const nodeKinds = computed(() => registry.value?.kinds || [])
const connectPointer = ref<{ x: number; y: number } | null>(null)
const connectPointerId = ref<number | null>(null)
function outputPortForNode(type: string) { return registry.value ? registeredOutputPortForNode(type, registry.value) : null }
function inputPortForNode(type: string) { return registry.value ? registeredInputPortForNode(type, registry.value) : null }
function nodeLabel(node: WorkflowNode) { return registry.value ? registeredNodeLabel(node, registry.value) : node.type }
function validateNodeConfig(node: Pick<WorkflowNode, 'id' | 'type' | 'config'>) { return registry.value ? registeredValidateNodeConfig(node, registry.value) : [{ code: 'CAPABILITIES_UNAVAILABLE', ...issueText('workflow.capUnavailable') }] }
const runByNode = computed(() => new Map((store.run?.nodes || []).map((node) => [node.node_id, node])))

function nodeStatus(node: WorkflowNode) {
  if (!store.runMatchesDraft) return 'draft'
  return runByNode.value.get(node.id)?.status || 'draft'
}

function nodeIssue(node: WorkflowNode) {
  return store.validationIssues.find((issue) => issue.node_id === node.id)
}

function nodeTransform(node: WorkflowNode) {
  return `translate(${node.position.x} ${node.position.y})`
}

function edgePath(edge: { source: string; target: string }) {
  const source = store.draft.nodes.find((node) => node.id === edge.source)
  const target = store.draft.nodes.find((node) => node.id === edge.target)
  if (!source || !target) return ''
  const sx = source.position.x + nodeWidth
  const sy = source.position.y + nodeHeight / 2
  const tx = target.position.x
  const ty = target.position.y + nodeHeight / 2
  return connectionPath(sx, sy, tx, ty)
}

function connectionPath(sx: number, sy: number, tx: number, ty: number) {
  const bend = Math.max(48, Math.abs(tx - sx) * .45)
  return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`
}

const previewPath = computed(() => {
  if (!connectFrom.value || !connectPointer.value) return null
  const source = store.draft.nodes.find((node) => node.id === connectFrom.value)
  if (!source) return null
  return connectionPath(
    source.position.x + nodeWidth,
    source.position.y + nodeHeight / 2,
    connectPointer.value.x,
    connectPointer.value.y,
  )
})

function canvasPoint(event: PointerEvent) {
  const bounds = stage.value?.getBoundingClientRect()
  if (!bounds) return { x: 0, y: 0 }
  return { x: (event.clientX - bounds.left - store.pan.x) / store.zoom, y: (event.clientY - bounds.top - store.pan.y) / store.zoom }
}

function openNode(node: WorkflowNode) {
  if (controlsLocked.value) return
  store.selectNode(node.id)
  const defaults = registry.value?.createDefaultConfig(node.type) || {}
  genericDraft.value = { ...defaults, ...node.config }
  settingsErrorState.set('')
  settingsOpen.value = true
  void nextTick(() => {
    const focusTarget = document.querySelector<HTMLElement>('.workflow-settings-modal select, .workflow-settings-modal input, .workflow-settings-modal textarea, .workflow-settings-modal button')
    focusTarget?.focus()
  })
}

function closeSettings() {
  if (store.saving) return
  settingsOpen.value = false
}

function commitSettings() {
  if (!selected.value || store.saving || store.activeRun) return
  const config = { ...genericDraft.value }
  const issues = validateNodeConfig({ id: selected.value.id, type: selected.value.type, config })
  if (issues.length) {
    settingsErrorState.set(issues[0].messageKey ? message(issues[0].messageKey, issues[0].messageParams) : issues[0].message)
    return
  }
  store.updateNode(selected.value.id, { config })
  settingsOpen.value = false
}

function removeSelected() {
  if (!selected.value || registry.value?.kind(selected.value.type)?.removable === false) return
  if (!window.confirm(t('workflow.deleteConfirm', { name: nodeLabel(selected.value) }))) return
  store.removeNode(selected.value.id)
  settingsOpen.value = false
}

function onNodePointerDown(event: PointerEvent, node: WorkflowNode) {
  if (store.activeRun) return
  const target = event.target as Element
  if (target.closest('button') || target.closest('.node-port')) return
  event.preventDefault()
  const point = canvasPoint(event)
  drag.value = { nodeId: node.id, startX: point.x, startY: point.y, x: node.position.x, y: node.position.y }
  store.selectNode(node.id)
  ;(event.currentTarget as SVGGElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (store.activeRun) return
  if (connectFrom.value) {
    if (connectPointerId.value !== null && event.pointerId !== connectPointerId.value) return
    connectPointer.value = canvasPoint(event)
    return
  }
  if (drag.value) {
    const point = canvasPoint(event)
    const node = store.draft.nodes.find((item) => item.id === drag.value!.nodeId)
    if (!node) return
    node.position = {
      x: Math.max(16, Math.round(drag.value.x + point.x - drag.value.startX)),
      y: Math.max(16, Math.round(drag.value.y + point.y - drag.value.startY)),
    }
    store.markDirty()
    return
  }
  if (panning.value) {
    store.pan.x = panning.value.x + event.clientX - panning.value.startX
    store.pan.y = panning.value.y + event.clientY - panning.value.startY
  }
}

function onPointerUp(event: PointerEvent) {
  const target = event.target as Element | null
  if (connectFrom.value && (event.type === 'pointercancel' || !target?.closest('.node-port-in'))) {
    cancelConnection()
  }
  drag.value = null
  panning.value = null
  if (stage.value?.hasPointerCapture?.(event.pointerId)) stage.value.releasePointerCapture(event.pointerId)
}

function startPan(event: PointerEvent) {
  if ((event.target as Element).closest('.workflow-node') || event.button !== 0 || store.activeRun) return
  panning.value = { startX: event.clientX, startY: event.clientY, x: store.pan.x, y: store.pan.y }
}

function onWheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  const direction = event.deltaY > 0 ? -.1 : .1
  setZoom(store.zoom + direction)
}

function setZoom(value: number) {
  store.zoom = Math.min(1.8, Math.max(.5, Math.round(value * 10) / 10))
}

function fitCanvas() {
  const nodes = store.draft.nodes
  if (!nodes.length) return
  const minX = Math.min(...nodes.map((node) => node.position.x))
  const minY = Math.min(...nodes.map((node) => node.position.y))
  const maxX = Math.max(...nodes.map((node) => node.position.x + nodeWidth))
  const maxY = Math.max(...nodes.map((node) => node.position.y + nodeHeight))
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const bounds = canvas.value?.getBoundingClientRect()
  if (!bounds || bounds.width <= 0 || bounds.height <= 0) return
  const candidate = Math.min(1.2, (bounds.width - 48) / width, (bounds.height - 72) / height)
  store.zoom = Math.max(.5, Math.floor(candidate * 100) / 100)
  store.pan = {
    x: (bounds.width - width * store.zoom) / 2 - minX * store.zoom,
    y: (bounds.height - height * store.zoom) / 2 - minY * store.zoom,
  }
}

function startConnection(node: WorkflowNode, event: PointerEvent) {
  if (!outputPortForNode(node.type) || store.activeRun || event.button !== 0 || event.isPrimary === false) return
  event.preventDefault()
  connectFrom.value = node.id
  connectPointerId.value = Number.isFinite(event.pointerId) ? event.pointerId : null
  connectPointer.value = canvasPoint(event)
  store.setError('')
}

function cancelConnection() {
  connectFrom.value = null
  connectPointer.value = null
  connectPointerId.value = null
}

function finishConnection(node: WorkflowNode, event: PointerEvent) {
  if (connectPointerId.value !== null && event.pointerId !== connectPointerId.value) return
  const source = connectFrom.value
  if (!source || !inputPortForNode(node.type)) {
    cancelConnection()
    return
  }
  cancelConnection()
  if (source !== node.id) store.connect(source, node.id)
}

function removeEdge(edgeId: string) {
  if (store.activeRun) return
  store.disconnect(edgeId)
}

async function validateAndSave() {
  settingsOpen.value = false
  const signature = executionSignature(store.draft, registry.value || undefined)
  try {
    const issues = await store.validate()
    if (issues.length || store.error) return
    if (executionSignature(store.draft, registry.value || undefined) !== signature) {
      store.setError(message('workflow.changedDuringValidation'))
      return
    }
    await store.save()
  } catch {
    // Keep the current draft and node context; the store exposes the API error.
  }
}

async function reloadCapabilities() {
  store.capabilities = null
  await store.load(props.projectId, props.audioId)
}

async function runWorkflow() {
  if (store.dirty) {
    store.setError(message('workflow.saveFirst'))
    return
  }
  try { await store.startRun() } catch { /* Store exposes the actionable failure. */ }
}

async function cancelWorkflow() {
  try { await store.cancelRun() } catch { /* Store exposes the actionable failure. */ }
}

function selectHistory(runId: string) {
  historyOpen.value = true
  void store.loadRun(runId)
}

watch(() => controlsLocked.value, (locked) => {
  if (locked && settingsOpen.value) {
    settingsOpen.value = false
    settingsErrorState.set('')
  }
})

export type CloseRequestResult = 'completed' | 'pending' | 'blocked'
function requestClose(action: () => void): CloseRequestResult {
  if (store.activeRun) {
    store.setError(message('workflow.leaveActive'))
    return 'blocked'
  }
  if (store.saving || store.validating) {
    store.setError(message('workflow.leaveSaving'))
    return 'blocked'
  }
  if (store.dirty) {
    pendingClose = action
    discardPrompt.value = true
    return 'pending'
  }
  action()
  return 'completed'
}

function discardAndClose() {
  if (store.activeRun) return
  discardPrompt.value = false
  store.discardDraft()
  const action = pendingClose
  pendingClose = null
  action?.()
}

function keyboardSelect(event: KeyboardEvent, node: WorkflowNode) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    openNode(node)
  }
  if (event.key === 'Delete' && registry.value?.kind(node.type)?.removable !== false && !store.activeRun) {
    event.preventDefault()
    if (window.confirm(t('workflow.deleteShortConfirm', { name: nodeLabel(node) }))) store.removeNode(node.id)
  }
}

function outputCheckState(nodeId: string) {
  return store.outputChecks[nodeId]?.state || 'checking'
}

function canDownloadOutput(nodeId: string) {
  return outputCheckState(nodeId) === 'available' && !!store.outputs[nodeId]?.download_url
}

function download(nodeId: string) {
  void store.downloadArtifact(nodeId)
}

async function retryOutputChecks() {
  const active = store.run
  if (!active) return
  await store.loadOutputs(props.projectId, props.audioId, active.id)
}

function nodeKindLabel(node: WorkflowNode) {
  return registry.value?.label(node.type) || node.type
}

function nodeKicker(node: WorkflowNode) {
  return nodeKindLabel(node)
}

function nodeDescription(node: WorkflowNode) {
  const definition = registry.value?.kind(node.type)
  if (definition?.isSource) return t('workflow.inputHelp', { name: props.audioName })
  return t('workflow.genericDescription', { name: nodeLabel(node) })
}

function nodeSettingsKicker(node: WorkflowNode) {
  return nodeLabel(node)
}

function nodeSettingsTitle(node: WorkflowNode) {
  return nodeLabel(node)
}

function deleteLabel() { return t('workflow.deleteNode') }

function onWindowPointerUp(event: PointerEvent) {
  if (!connectFrom.value) return
  if (connectPointerId.value !== null && event.pointerId !== connectPointerId.value) return
  cancelConnection()
}

function configFieldLabel(field: { type: string; options: readonly { value: string; label: string }[] }, value: unknown) {
  if (field.type === 'select') return field.options.find((option) => option.value === value)?.label || String(value)
  if (field.type === 'boolean' && typeof value === 'boolean') return t(value ? 'workflow.yes' : 'workflow.no')
  return String(value)
}

function nodeMeta(node: WorkflowNode) {
  const definition = registry.value?.kind(node.type)
  if (definition?.isSource) return t('workflow.boundAudio')
  return (definition?.fields || []).map((field) => {
    const value = node.config[field.key] === undefined ? field.default : node.config[field.key]
    return value === null || value === undefined || value === '' ? '' : `${field.label}: ${configFieldLabel(field, value)}`
  }).filter(Boolean).slice(0, 2).join(' · ') || nodeLabel(node)
}

function runStatusLabel(status?: string) {
  const key = `workflow.runStatus_${status}`
  return status ? (hasMessage(key) ? t(key) : status) : t('workflow.notRun')
}

function nodeStatusLabel(status: string) {
  const key = `workflow.nodeStatus_${status}`
  return hasMessage(key) ? t(key) : status
}

function numberLabel(value?: number | null) {
  return value === undefined || value === null ? '—' : new Intl.NumberFormat(locale.value).format(value)
}

function percentLabel(value: number) {
  return new Intl.NumberFormat(locale.value, { style: 'percent', maximumFractionDigits: 0 }).format(value)
}

function sizeLabel(bytes: number) {
  const megabytes = bytes >= 1024 * 1024
  const value = megabytes ? bytes / 1024 / 1024 : Math.max(1, Math.round(bytes / 1024))
  return `${new Intl.NumberFormat(locale.value, { minimumFractionDigits: megabytes ? 1 : 0, maximumFractionDigits: megabytes ? 1 : 0 }).format(value)} ${megabytes ? 'MB' : 'KB'}`
}

function dateLabel(value?: string | null) {
  if (!value) return t('workflow.notRecorded')
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? t('workflow.notRecorded') : new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'medium', hour12: false }).format(date)
}

defineExpose({ requestClose })

onMounted(() => {
  void store.load(props.projectId, props.audioId)
  window.addEventListener('pointerup', onWindowPointerUp)
  window.addEventListener('pointercancel', onWindowPointerUp)
})
onUnmounted(() => {
  store.stop()
  pendingClose = null
  window.removeEventListener('pointerup', onWindowPointerUp)
  window.removeEventListener('pointercancel', onWindowPointerUp)
})
</script>

<template>
  <section class="node-workflow" aria-labelledby="node-workflow-title" :aria-busy="store.loading || store.saving ? 'true' : 'false'">
    <header class="node-workflow-heading">
      <div>
        <span class="section-kicker">{{ t('workflow.kicker') }}</span>
        <h2 id="node-workflow-title">{{ t('workflow.title') }} <span class="mvp-badge">{{ t('workflow.audioBranch') }}</span></h2>
        <p>{{ t('workflow.intro') }}</p>
      </div>
      <div class="node-workflow-actions">
        <button class="secondary-button" type="button" :disabled="!!store.activeRun" @click="historyOpen = !historyOpen">{{ historyOpen ? t('workflow.collapseHistory') : t('workflow.historyCount', { count: numberLabel(store.history.length) }) }}</button>
      </div>
    </header>

    <div v-if="store.capabilitiesError || store.error" class="alert node-workflow-alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ store.capabilitiesError || store.error }}</span><button type="button" :aria-label="t('workflow.closeAlert')" @click="store.capabilitiesError = ''; store.error = ''">×</button><button v-if="store.capabilitiesError" class="secondary-button compact-button" type="button" @click="reloadCapabilities">{{ t('workflow.retryCapabilities') }}</button></div>
    <div v-if="store.notice" class="workflow-notice" role="status">{{ store.notice }}</div>
        <div v-if="discardPrompt" class="workflow-discard" role="alertdialog" aria-labelledby="discard-title"><div><strong id="discard-title">{{ t('workflow.discardTitle') }}</strong><p>{{ t('workflow.discardDescription') }}</p></div><div><button class="secondary-button" type="button" @click="discardPrompt = false">{{ t('workflow.continueEditing') }}</button><button class="primary-button" type="button" @click="discardAndClose">{{ t('workflow.discardChanges') }}</button></div></div>

    <div v-if="store.loading" class="workflow-loading" role="status">{{ t('workflow.loading') }}</div>
    <template v-else>
      <div class="workflow-mode-note"><span class="mode-dot" aria-hidden="true"></span><strong>{{ t('workflow.asrTextOutput') }}</strong><span>{{ t('workflow.boundAudioHelp', { name: props.audioName }) }}</span></div>
      <div class="workflow-toolbar" role="toolbar" :aria-label="t('workflow.toolbar')">
        <button v-for="kind in nodeKinds" :key="kind.type" class="secondary-button compact-button" type="button" :disabled="store.saving || !!store.activeRun || (kind.isSource && !!currentInput) || store.draft.nodes.length >= (registry?.capabilities.constraints.maxNodes || 0) || (kind.maxInstances !== null && store.draft.nodes.filter((node) => node.type === kind.type).length >= kind.maxInstances)" @click="store.addNode(kind.type)">＋ {{ kind.addLabel }}</button>
        <span class="toolbar-rule" aria-hidden="true"></span>
        <span class="toolbar-spacer"></span>
        <button class="secondary-button compact-button" type="button" :disabled="!canValidateAndSave" @click="validateAndSave">{{ store.saving ? t('workflow.saving') : store.validating ? t('workflow.validating') : t('workflow.validateAndSave') }}</button>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomOut')" :disabled="store.zoom <= .5" @click="setZoom(store.zoom - .1)">−</button>
        <span class="zoom-value" aria-live="polite">{{ percentLabel(store.zoom) }}</span>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomIn')" :disabled="store.zoom >= 1.8" @click="setZoom(store.zoom + .1)">+</button>
        <button class="secondary-button compact-button" type="button" @click="fitCanvas">{{ t('workflow.fit') }}</button>
        <button class="secondary-button compact-button" type="button" @click="store.pan = { x: 0, y: 0 }">{{ t('workflow.resetView') }}</button>
      </div>

      <div class="workflow-main">
        <div ref="canvas" class="workflow-canvas" @wheel="onWheel">
          <div class="canvas-caption"><span>{{ t('workflow.canvasHelp') }}</span><span>{{ t('workflow.version', { version: numberLabel(store.document?.workflow_revision) }) }}</span></div>
          <div class="workflow-stage" :style="{ transform: `translate(${store.pan.x}px, ${store.pan.y}px) scale(${store.zoom})` }">
            <svg ref="stage" class="workflow-svg" :viewBox="'0 0 900 480'" width="900" height="480" role="group" :aria-label="t('workflow.canvasAria')" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp">
              <defs><pattern id="workflow-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="oklch(91% .016 82)" stroke-width="1" /></pattern></defs>
              <rect width="900" height="480" fill="url(#workflow-grid)" />
              <path v-for="edge in store.draft.edges" :key="edge.id" class="workflow-edge-hit" :d="edgePath(edge)" @pointerdown.stop @click.stop="!controlsLocked && removeEdge(edge.id)" />
              <path v-for="edge in store.draft.edges" :key="`${edge.id}-line`" class="workflow-edge" :class="{ 'is-run': store.runMatchesDraft && store.run?.status === 'completed' }" :d="edgePath(edge)" aria-hidden="true" />
              <path v-if="previewPath" class="workflow-edge-preview" :d="previewPath" aria-hidden="true" />
              <g v-for="node in store.draft.nodes" :key="node.id" class="workflow-node" :class="[`is-${node.type}`, `is-state-${nodeStatus(node)}`, { 'is-selected': store.selectedNodeId === node.id, 'is-invalid': !!nodeIssue(node) }]" :transform="nodeTransform(node)" role="button" tabindex="0" :aria-label="t('workflow.nodeAria', { name: nodeLabel(node), kind: nodeKindLabel(node), status: nodeStatusLabel(nodeStatus(node)) })" :aria-pressed="store.selectedNodeId === node.id ? 'true' : 'false'" @pointerdown.stop="onNodePointerDown($event, node)" @dblclick.stop="openNode(node)" @click.stop="store.selectNode(node.id)" @keydown="keyboardSelect($event, node)">
                <rect class="node-card" :width="nodeWidth" :height="nodeHeight" rx="16" />
                <rect class="node-accent" width="5" :height="nodeHeight" rx="2.5" />
                <text class="node-kicker" x="22" y="27">{{ nodeKicker(node) }}</text>
                <text class="node-name" x="22" y="52">{{ nodeLabel(node) }}</text>
                <text class="node-meta" x="22" y="72">{{ nodeMeta(node) }}</text>
                <rect :width="nodeWidth" :height="nodeHeight" fill="transparent" />
                <circle v-if="inputPortForNode(node.type)" class="node-port node-port-in" :class="`node-port-${inputPortForNode(node.type)}`" cx="0" :cy="nodeHeight / 2" r="7" :aria-label="t('workflow.inputPort', { port: inputPortForNode(node.type) || '' })" @pointerdown.stop @pointerup.stop="!controlsLocked && finishConnection(node, $event)"><title>{{ t('workflow.inputPort', { port: inputPortForNode(node.type) || '' }) }}</title></circle><text v-if="inputPortForNode(node.type)" class="node-port-label" x="12" :y="nodeHeight / 2 + 3" aria-hidden="true">{{ inputPortForNode(node.type) }}</text>
                <circle v-if="outputPortForNode(node.type)" class="node-port node-port-out" :class="`node-port-${outputPortForNode(node.type)}`" :cx="nodeWidth" :cy="nodeHeight / 2" r="7" :aria-label="t('workflow.outputPort', { port: outputPortForNode(node.type) || '' })" @pointerdown.stop="!controlsLocked && startConnection(node, $event)"><title>{{ t('workflow.outputPort', { port: outputPortForNode(node.type) || '' }) }}</title></circle><text v-if="outputPortForNode(node.type)" class="node-port-label" :x="nodeWidth - 12" :y="nodeHeight / 2 + 3" aria-hidden="true">{{ outputPortForNode(node.type) }}</text>
                <text v-if="nodeIssue(node)" class="node-error-mark" x="176" y="24" aria-hidden="true">!</text>
              </g>
            </svg>
          </div>
          <div v-if="!store.draft.nodes.length" class="workflow-empty"><strong>{{ t('workflow.emptyCanvas') }}</strong><span>{{ t('workflow.emptyCanvasHelp') }}</span></div>
        </div>

        <aside class="workflow-inspector" aria-labelledby="workflow-inspector-title">
          <div class="inspector-topline"><span class="section-kicker">{{ t('workflow.inspectorKicker') }}</span><span class="workflow-dirty-state">{{ store.dirty ? t('workflow.unsaved') : t('workflow.synced') }}</span></div>
          <h3 id="workflow-inspector-title">{{ selected ? nodeLabel(selected) : t('workflow.selectNode') }}</h3>
          <template v-if="selected">
            <p class="inspector-type">{{ nodeDescription(selected) }}</p>
            <div class="workflow-field"><span>{{ t('workflow.nodeId') }}</span><code>{{ selected.id }}</code></div>
            <button class="primary-button inspector-edit" type="button" :disabled="controlsLocked" @click="openNode(selected)">{{ t('workflow.editSettings') }}</button>
            <button v-if="selectedKind?.removable" class="text-button inspector-delete" type="button" :disabled="controlsLocked" @click="removeSelected">{{ deleteLabel() }}</button>
            <p v-if="selectedKind?.isSource" class="inspector-help">{{ t('workflow.inputHelp', { name: props.audioName }) }}</p>
          </template>
          <div v-else class="inspector-empty"><span aria-hidden="true">↖</span><p>{{ t('workflow.inspectorEmpty') }}</p></div>
          <dl class="workflow-inspector-meta"><div><dt>{{ t('workflow.workflowVersion') }}</dt><dd>{{ numberLabel(store.document?.workflow_revision) }}</dd></div><div><dt>{{ t('workflow.documentRevision') }}</dt><dd>{{ numberLabel(store.document?.revision) }}</dd></div><div><dt>{{ t('workflow.currentAudio') }}</dt><dd>{{ props.audioName }}</dd></div></dl>
        </aside>
      </div>

      <ul v-if="store.validationIssues.length" class="workflow-issues" aria-live="polite"><li v-for="(issue, index) in store.validationIssues" :key="`${issue.code}-${index}`"><button type="button" :disabled="!issue.node_id" @click="issue.node_id && (store.selectNode(issue.node_id), openNode(store.draft.nodes.find((node) => node.id === issue.node_id)!))">{{ issue.node_id ? t('workflow.nodeReference', { id: issue.node_id }) : t('workflow.workflow') }}</button><span>{{ workflowIssueLabel(issue) }}</span></li></ul>

      <section class="workflow-run-panel" aria-labelledby="workflow-run-title">
        <div class="workflow-run-heading"><div><span class="section-kicker">{{ t('workflow.runKicker') }}</span><h3 id="workflow-run-title">{{ t('workflow.runTitle') }}</h3><p>{{ t('workflow.snapshotHelp') }}</p></div><div class="workflow-run-actions"><button v-if="store.activeRun" class="secondary-button danger-button" type="button" :disabled="store.actionLoading" @click="cancelWorkflow">{{ store.actionLoading ? t('workflow.cancelling') : t('workflow.cancelRun') }}</button><button v-else class="primary-button" type="button" :disabled="!canRun" @click="runWorkflow">{{ store.actionLoading ? t('workflow.starting') : store.dirty ? t('workflow.saveToRun') : t('workflow.runWorkflow') }}</button></div></div>
        <div class="workflow-run-summary"><span class="run-status" :class="`is-${store.run?.status || 'idle'}`">{{ runStatusLabel(store.run?.status) }}</span><span v-if="store.run">{{ t('workflow.runSummary', { id: store.run.id, version: numberLabel(store.run.workflow_revision), date: dateLabel(store.run.created_at), snapshot: store.runMatchesDraft ? t('workflow.snapshotMatches') : t('workflow.snapshotHistorical') }) }}</span><span v-else>{{ t('workflow.noRuns') }}</span></div>
        <div v-if="store.runLoading" class="workflow-loading small" role="status">{{ t('workflow.loadingRun') }}</div>
        <div v-else-if="store.runError" class="workflow-run-error" role="alert">{{ store.runError }}</div>
        <p v-if="store.run?.error" class="workflow-run-error" role="alert">{{ workflowErrorLabel(store.run.error) }}</p>
        <div v-if="store.run" class="workflow-run-nodes">
          <article v-for="node in store.run.nodes" :key="node.node_id" class="workflow-run-node">
            <div><strong>{{ node.node_id }}</strong><span class="run-node-status" :class="`is-${node.status}`">{{ nodeStatusLabel(node.status) }}</span></div>
            <p v-if="node.error">{{ workflowErrorLabel(node.error) }}</p>
            <p v-else-if="node.artifact">{{ t('workflow.snapshotArtifact', { port: node.artifact.port.toUpperCase(), type: node.artifact.media_type, size: sizeLabel(node.artifact.size_bytes) }) }}</p>
            <p v-else>{{ t('workflow.noNodeArtifact') }}</p>
            <template v-if="node.artifact && node.status === 'completed'">
              <p class="workflow-output-state" :class="`is-${outputCheckState(node.node_id)}`">{{ outputCheckState(node.node_id) === 'checking' ? t('workflow.artifactChecking') : outputCheckState(node.node_id) === 'missing' ? t('workflow.artifactMissing') : outputCheckState(node.node_id) === 'failed' ? t('workflow.artifactCheckFailed') : t('workflow.artifactAvailable') }}</p>
              <button v-if="canDownloadOutput(node.node_id)" class="secondary-button compact-button" type="button" @click="download(node.node_id)">{{ t('workflow.download', { filename: node.artifact?.filename || '' }) }}</button>
              <button v-else-if="outputCheckState(node.node_id) === 'failed'" class="secondary-button compact-button" type="button" @click="retryOutputChecks">{{ t('workflow.retryChecks') }}</button>
            </template>
          </article>
        </div>
        <div v-if="historyOpen" class="workflow-history"><div class="workflow-history-heading"><h4>{{ t('workflow.history') }}</h4><button class="text-button" type="button" :disabled="store.historyLoading" @click="store.loadHistory()">{{ store.historyLoading ? t('workflow.refreshing') : t('workflow.refresh') }}</button></div><p v-if="store.historyError" class="workflow-run-error" role="alert">{{ store.historyError }}</p><div v-if="!store.history.length && !store.historyLoading" class="workflow-history-empty">{{ t('workflow.historyEmpty') }}</div><button v-for="item in store.history" :key="item.id" class="workflow-history-row" :class="{ 'is-active': store.run?.id === item.id }" type="button" :disabled="store.runLoading || !!store.activeRun" @click="selectHistory(item.id)"><span><strong>{{ item.id }}</strong><small>{{ dateLabel(item.created_at) }} · {{ t('workflow.version', { version: numberLabel(item.workflow_revision) }) }}</small></span><span class="run-status" :class="`is-${item.status}`">{{ runStatusLabel(item.status) }}</span></button></div>
      </section>
    </template>

    <div v-if="settingsOpen && selected" class="modal-backdrop" role="presentation" @click.self="closeSettings">
      <section class="config-modal workflow-settings-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-settings-title">
        <div class="config-modal-header"><div><span class="section-kicker">{{ nodeSettingsKicker(selected) }}</span><h3 id="workflow-settings-title">{{ nodeSettingsTitle(selected) }}</h3><p>{{ t('workflow.settingsSubtitle') }}</p></div><button class="modal-close" type="button" :aria-label="t('workflow.closeSettings')" @click="closeSettings">×</button></div>
        <form class="config-modal-form" @submit.prevent="commitSettings">
          <p v-if="selectedKind?.isSource" class="inspector-help">{{ t('workflow.inputSettingsHelp', { name: props.audioName }) }}</p>
          <div v-if="genericFields.length" class="modal-field-grid">
            <label v-for="field in genericFields" :key="field.key" class="config-field" :class="{ 'modal-wide-field': field.type === 'json' }">
              <span>{{ field.label }}<template v-if="field.required"> *</template></span>
              <select v-if="field.type === 'boolean'" :value="String(fieldValue(field))" :disabled="controlsLocked" @change="setFieldValue(field, ($event.target as HTMLSelectElement).value === 'true')"><option value="true">{{ t('workflow.yes') }}</option><option value="false">{{ t('workflow.no') }}</option></select>
              <select v-else-if="field.type === 'select'" :value="String(fieldValue(field))" :disabled="controlsLocked" @change="setFieldValue(field, ($event.target as HTMLSelectElement).value)"><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></select>
              <textarea v-else-if="field.type === 'json'" :value="jsonFieldValue(field)" rows="4" :disabled="controlsLocked" @change="setJsonFieldValue(field, $event)"></textarea>
              <input v-else :value="fieldValue(field)" :type="field.type === 'integer' || field.type === 'number' ? 'number' : 'text'" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :minlength="field.minLength ?? undefined" :maxlength="field.maxLength ?? undefined" :pattern="field.pattern ?? undefined" :step="field.type === 'integer' ? 1 : 'any'" :disabled="controlsLocked" @input="setFieldValue(field, ($event.target as HTMLInputElement).value)" />
              <small v-if="field.description">{{ field.description }}</small>
            </label>
          </div>
          <p v-if="settingsError" class="settings-feedback settings-error" role="alert">{{ settingsError }}</p>
          <div class="config-modal-footer"><span>{{ store.dirty ? t('workflow.hasUnsaved') : t('workflow.nodeSynced') }}</span><div><button class="secondary-button" type="button" @click="closeSettings">{{ t('workflow.cancel') }}</button><button class="primary-button" type="submit" :disabled="controlsLocked">{{ t('workflow.applySettings') }}</button></div></div>
        </form>
      </section>
    </div>
  </section>
</template>
