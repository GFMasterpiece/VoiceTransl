<script setup lang="ts">
import { t, locale, message, hasMessage } from '../i18n'
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import {
  workflowErrorLabel, workflowIssueLabel,
  WORKFLOW_NODE_HEIGHT, WORKFLOW_NODE_PORT_Y, WORKFLOW_NODE_FIELDS_Y, workflowNodeHeight,
  WORKFLOW_NODE_WIDTH, WORKFLOW_NODE_ROW_GAP, WORKFLOW_RESOURCE_HEIGHT, WORKFLOW_PREVIEW_HEIGHT, workflowFieldsHeight,
  executionSignature,
  inputPortForNode as registeredInputPortForNode,
  isTerminalNode,
  nodeLabel as registeredNodeLabel,
  outputPortForNode as registeredOutputPortForNode,
  useWorkflowStore,
  type WorkflowNode,
} from '../stores/workflow'

import NodeConfigFields from './NodeConfigFields.vue'
import NodeResourceInput from './NodeResourceInput.vue'
import NodePreview from './NodePreview.vue'
import { measureNodeContent, type NodeContentMinimum } from '../workflow/measureNode'
import type { Audio, Project } from '../stores/projects'

const clipId = useId()
const props = defineProps<{ projectId: string; audioId: string | null; audioName: string; project?: Project; audios?: readonly Audio[] }>()
const emit = defineEmits<{ selectAudio: [id: string]; uploaded: [audio: Audio] }>()
const resourceUploads = ref<Record<string, boolean>>({})
const resourceBusy = computed(() => Object.values(resourceUploads.value).some(Boolean))
const store = useWorkflowStore()
watch(resourceBusy, (busy) => { store.resourceUploading = busy }, { flush: 'sync' })
const canvas = ref<HTMLDivElement | null>(null)
const stage = ref<SVGSVGElement | null>(null)
const drag = ref<{ nodeId: string; startX: number; startY: number; x: number; y: number } | null>(null)
const resizing = ref<{ nodeId: string; axis: 'right' | 'bottom' | 'corner'; startX: number; startY: number; width: number; height: number } | null>(null)
const panning = ref<{ startX: number; startY: number; x: number; y: number } | null>(null)
const connectFrom = ref<string | null>(null)
const gesturePointerId = ref<number | null>(null)
const historyOpen = ref(false)
const discardPrompt = ref(false)
let pendingClose: (() => void) | null = null
const selectedKind = computed(() => selected.value && registry.value ? registry.value.kind(selected.value.type) : null)
const selected = computed(() => store.selectedNode)
const currentInput = computed(() => store.draft.nodes.find((node) => registry.value?.kind(node.type)?.isSource))
const controlsLocked = computed(() => store.saving || store.validating || !!store.activeRun || store.actionLoading || resourceBusy.value)
const canValidateAndSave = computed(() => store.dirty && !controlsLocked.value && !store.loading)
const canRun = computed(() => !store.dirty && !controlsLocked.value && !!store.document && !!(store.boundAudioId || store.audioId))

const minZoom = .01
function nodeWidth(node: WorkflowNode) { return store.getNodeSize(node).width }
function nodeHeight(node: WorkflowNode) { return store.getNodeSize(node).height }
const portY = WORKFLOW_NODE_PORT_Y
const registry = computed(() => store.registry)
const nodeKinds = computed(() => registry.value?.kinds || [])
const connectPointer = ref<{ x: number; y: number } | null>(null)
const connectPointerId = ref<number | null>(null)
function outputPortForNode(type: string) { return registry.value ? registeredOutputPortForNode(type, registry.value) : null }
function inputPortForNode(type: string) { return registry.value ? registeredInputPortForNode(type, registry.value) : null }
function portLabel(id: string | null) { return id ? registry.value?.port(id)?.label || id : '' }
function nodeLabel(node: WorkflowNode) { return registry.value ? registeredNodeLabel(node, registry.value) : node.type }
const inputAudioName = computed(() => store.boundAudioId
  ? props.audios?.find((audio) => audio.id === store.boundAudioId)?.source.original_name || t('workflow.resourceMissing')
  : props.audioName)
const runByNode = computed(() => new Map((store.run?.nodes || []).map((node) => [node.node_id, node])))

function kind(node: WorkflowNode) { return registry.value?.kind(node.type) }
function fieldsHeight(node: WorkflowNode) { return registry.value ? workflowFieldsHeight(node.type, registry.value) : 0 }

function previewRecord(node: WorkflowNode) { return store.run?.nodes.find((record) => record.node_id === node.id && record.node_type === node.type) }
async function bindResource(node: WorkflowNode, audioId: string | null) {
  if (controlsLocked.value) return
  const resource = kind(node)?.resourceInput
  if (!resource) return
  const config = { ...node.config }
  if (audioId) config[resource.configKey] = audioId
  else delete config[resource.configKey]
  store.updateNode(node.id, { config })
  if (audioId) await store.setAudioContext(audioId)
}
async function uploadedResource(node: WorkflowNode, audio: Audio) {
  // The resource component releases its upload lock before publishing success.
  emit('uploaded', audio)
  await bindResource(node, audio.id)
}
watch(() => props.audioId, (id) => { void store.setAudioContext(id) })
watch(() => store.audioId, (id) => { if (id && id !== props.audioId) emit('selectAudio', id) })
function guardUnload(event: BeforeUnloadEvent) {
  if (resourceBusy.value || store.dirty) { event.preventDefault(); event.returnValue = '' }
}

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
  const sx = source.position.x + nodeWidth(source)
  const sy = source.position.y + portY
  const tx = target.position.x
  const ty = target.position.y + portY
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
    source.position.x + nodeWidth(source),
    source.position.y + portY,
    connectPointer.value.x,
    connectPointer.value.y,
  )
})

// Thin out world-grid lines at low zoom while keeping the same world origin.
const gridStyle = computed(() => {
  const step = 24 * store.zoom
  const spacing = step * 2 ** Math.max(0, Math.ceil(Math.log2(8 / step)))
  return { backgroundSize: `${spacing}px ${spacing}px`, backgroundPosition: `${store.pan.x}px ${store.pan.y}px` }
})

function beginPointer(event: PointerEvent, capture: boolean) {
  gesturePointerId.value = Number.isFinite(event.pointerId) ? event.pointerId : null
  if (capture && gesturePointerId.value !== null) stage.value?.setPointerCapture?.(event.pointerId)
}
function clearGestures() {
  const pointerId = gesturePointerId.value
  gesturePointerId.value = null
  drag.value = null
  resizing.value = null
  panning.value = null
  cancelConnection()
  if (pointerId !== null && stage.value?.hasPointerCapture?.(pointerId)) stage.value.releasePointerCapture(pointerId)
}
watch(controlsLocked, (locked) => { if (locked) clearGestures() })

function canvasPoint(event: PointerEvent) {
  const bounds = stage.value?.getBoundingClientRect()
  if (!bounds) return { x: 0, y: 0 }
  return { x: (event.clientX - bounds.left - store.pan.x) / store.zoom, y: (event.clientY - bounds.top - store.pan.y) / store.zoom }
}

function focusNode(node: WorkflowNode) {
  store.selectNode(node.id)
  void nextTick(() => {
    const group = [...(stage.value?.querySelectorAll<SVGGElement>('.workflow-node') || [])].find((element) => element.dataset.nodeId === node.id)
    group?.querySelector<HTMLElement>('input, select, textarea')?.focus()
  })
}
function editNode(node: WorkflowNode, config: Record<string, unknown>) {
  if (controlsLocked.value) return
  store.updateNode(node.id, { config })
}

function removeSelected() {
  if (controlsLocked.value || !selected.value || registry.value?.kind(selected.value.type)?.removable === false) return
  if (!window.confirm(t('workflow.deleteConfirm', { name: nodeLabel(selected.value) }))) return
  store.removeNode(selected.value.id)
}

const contentMinimums = ref<Record<string, NodeContentMinimum>>({})
let measurementFrame: number | null = null
let measurementDisposed = false
let mutationObserver: MutationObserver | null = null
let resizeObserver: ResizeObserver | null = null
const observedBodies = new Set<Element>()
const measuredNodes = new Map<string, WorkflowNode>()
function contentMinimum(node: WorkflowNode): NodeContentMinimum {
  return contentMinimums.value[node.id] || {
    width: WORKFLOW_NODE_WIDTH,
    height: registry.value ? workflowNodeHeight(node.type, registry.value) : WORKFLOW_NODE_HEIGHT,
    fields: fieldsHeight(node), resource: kind(node)?.resourceInput ? WORKFLOW_RESOURCE_HEIGHT : 0,
    preview: kind(node)?.preview ? WORKFLOW_PREVIEW_HEIGHT : 0,
  }
}
function measureContent() {
  measurementFrame = null
  if (!stage.value || !registry.value) return
  const ids = new Set(store.draft.nodes.map((node) => node.id))
  for (const id of Object.keys(contentMinimums.value)) if (!ids.has(id)) {
    delete contentMinimums.value[id]
    delete store.nodeMinimums[id]
    measuredNodes.delete(id)
  }
  for (const body of observedBodies) if (!stage.value.contains(body)) {
    resizeObserver?.unobserve(body)
    observedBodies.delete(body)
  }
  for (const group of stage.value.querySelectorAll<SVGGElement>('.workflow-node')) {
    const node = store.draft.nodes.find((item) => item.id === group.dataset.nodeId)
    if (!node) continue
    // Start from catalog minima every time, never from a previous user size or measurement.
    const minimum = measureNodeContent(group, {
      width: WORKFLOW_NODE_WIDTH, height: workflowNodeHeight(node.type, registry.value),
      fields: fieldsHeight(node), resource: kind(node)?.resourceInput ? WORKFLOW_RESOURCE_HEIGHT : 0,
      preview: kind(node)?.preview ? WORKFLOW_PREVIEW_HEIGHT : 0,
    })
    const firstMeasurement = measuredNodes.get(node.id) !== node
    measuredNodes.set(node.id, node)
    if (JSON.stringify(contentMinimums.value[node.id]) !== JSON.stringify(minimum)) contentMinimums.value = { ...contentMinimums.value, [node.id]: minimum }
    store.setNodeMinimum(node.id, minimum)
    // A newly inserted kind may reveal a larger intrinsic width on its first
    // paint. Finish placing that new node only; never reflow saved/user nodes.
    if (firstMeasurement && !store.document?.nodes.some((item) => item.id === node.id) && !controlsLocked.value) {
      const size = store.getNodeSize(node)
      let y = node.position.y
      while (true) {
        const collisions = store.draft.nodes.filter((other) => {
          if (other.id === node.id) return false
          const otherSize = store.getNodeSize(other)
          return node.position.x < other.position.x + otherSize.width && node.position.x + size.width > other.position.x
            && y < other.position.y + otherSize.height + WORKFLOW_NODE_ROW_GAP && y + size.height + WORKFLOW_NODE_ROW_GAP > other.position.y
        })
        if (!collisions.length) break
        y = Math.max(...collisions.map((other) => other.position.y + store.getNodeSize(other).height)) + WORKFLOW_NODE_ROW_GAP
      }
      if (y !== node.position.y) store.updateNode(node.id, { position: { ...node.position, y } })
    }
    const body = group.querySelector('.node-body')
    if (body && resizeObserver && !observedBodies.has(body)) { resizeObserver.observe(body); observedBodies.add(body) }
  }
}
function scheduleMeasurement() {
  if (measurementDisposed) return
  if (measurementFrame === null) measurementFrame = requestAnimationFrame(measureContent)
}
watch(stage, (element) => {
  mutationObserver?.disconnect(); resizeObserver?.disconnect(); observedBodies.clear()
  if (!element) return
  mutationObserver = new MutationObserver(scheduleMeasurement)
  mutationObserver.observe(element, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'aria-invalid', 'disabled'] })
  resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(scheduleMeasurement)
  scheduleMeasurement()
}, { flush: 'post' })
watch(locale, scheduleMeasurement)
function bodyStyle(node: WorkflowNode) {
  const minimum = contentMinimum(node)
  return { '--node-fields-min': `${minimum.fields}px`, '--node-resource-min': `${minimum.resource}px`, '--node-preview-min': `${minimum.preview}px` }
}
function headerPath(node: WorkflowNode) { return `M 14 0 H ${nodeWidth(node) - 14} Q ${nodeWidth(node)} 0 ${nodeWidth(node)} 14 V 30 H 0 V 14 Q 0 0 14 0 Z` }
function portClipWidth(node: WorkflowNode) { return inputPortForNode(node.type) && outputPortForNode(node.type) ? (nodeWidth(node) - 24) / 2 : nodeWidth(node) - 20 }
function startResize(event: PointerEvent, node: WorkflowNode, axis: 'right' | 'bottom' | 'corner') {
  if (controlsLocked.value || event.button !== 0 || event.isPrimary === false) return
  event.preventDefault()
  clearGestures()
  const point = canvasPoint(event)
  resizing.value = { nodeId: node.id, axis, startX: point.x, startY: point.y, ...store.getNodeSize(node) }
  store.selectNode(node.id)
  beginPointer(event, true)
}
function setNodeSize(node: WorkflowNode, width: number, height: number) {
  if (controlsLocked.value) return
  const minimum = store.getNodeSize({ ...node, size: null })
  store.updateNode(node.id, { size: { width: Math.min(1_000_000, Math.max(minimum.width, Math.round(width))), height: Math.min(1_000_000, Math.max(minimum.height, Math.round(height))) } })
}
function resizeKey(event: KeyboardEvent, node: WorkflowNode, axis: 'right' | 'bottom' | 'corner') {
  event.stopPropagation()
  if (controlsLocked.value) return
  if (event.key === 'Home') { event.preventDefault(); store.updateNode(node.id, { size: null }); return }
  const step = event.shiftKey ? 50 : 10
  const size = store.getNodeSize(node)
  const dx = axis !== 'bottom' ? (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0) : 0
  const dy = axis !== 'right' ? (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0) : 0
  if (dx || dy) { event.preventDefault(); setNodeSize(node, size.width + dx, size.height + dy) }
}

function onNodePointerDown(event: PointerEvent, node: WorkflowNode) {
  if (controlsLocked.value || event.button !== 0 || event.isPrimary === false) return
  const target = event.target as Element
  if (target.closest('button, input, select, textarea, foreignObject, .node-resize-handle') || target.closest('.node-port')) return
  event.preventDefault()
  const point = canvasPoint(event)
  drag.value = { nodeId: node.id, startX: point.x, startY: point.y, x: node.position.x, y: node.position.y }
  store.selectNode(node.id)
  beginPointer(event, true)
}

function onPointerMove(event: PointerEvent) {
  if (controlsLocked.value || (gesturePointerId.value !== null && gesturePointerId.value !== event.pointerId)) return
  if (resizing.value) {
    const point = canvasPoint(event)
    const resize = resizing.value
    const node = store.draft.nodes.find((item) => item.id === resize.nodeId)
    if (node) setNodeSize(node, resize.width + (resize.axis !== 'bottom' ? point.x - resize.startX : 0), resize.height + (resize.axis !== 'right' ? point.y - resize.startY : 0))
    return
  }
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
  if (gesturePointerId.value !== null && gesturePointerId.value !== event.pointerId) return
  clearGestures()
}

function startPan(event: PointerEvent) {
  if ((event.target as Element).closest('.workflow-node') || event.button !== 0 || controlsLocked.value || event.isPrimary === false) return
  event.preventDefault()
  panning.value = { startX: event.clientX, startY: event.clientY, x: store.pan.x, y: store.pan.y }
  beginPointer(event, true)
}

function onWheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) return
  event.preventDefault()
  const direction = event.deltaY > 0 ? -.1 : .1
  setZoom(store.zoom + direction)
}

function setZoom(value: number) {
  store.zoom = Math.min(1.8, Math.max(minZoom, Math.round(value * 10) / 10))
}

function fitCanvas() {
  const nodes = store.draft.nodes
  if (!nodes.length) return
  const minX = Math.min(...nodes.map((node) => node.position.x))
  const minY = Math.min(...nodes.map((node) => node.position.y))
  const maxX = Math.max(...nodes.map((node) => node.position.x + nodeWidth(node)))
  const maxY = Math.max(...nodes.map((node) => node.position.y + nodeHeight(node)))
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const bounds = canvas.value?.getBoundingClientRect()
  if (!bounds || bounds.width <= 0 || bounds.height <= 0) return
  const candidate = Math.min(1.2, (bounds.width - 48) / width, (bounds.height - 72) / height)
  store.zoom = Math.max(minZoom, Math.floor(candidate * 100) / 100)
  store.pan = {
    x: (bounds.width - width * store.zoom) / 2 - minX * store.zoom,
    y: (bounds.height - height * store.zoom) / 2 - minY * store.zoom,
  }
}

function startConnection(node: WorkflowNode, event: PointerEvent) {
  if (!outputPortForNode(node.type) || controlsLocked.value || event.button !== 0 || event.isPrimary === false) return
  event.preventDefault()
  beginPointer(event, false)
  connectFrom.value = node.id
  connectPointerId.value = Number.isFinite(event.pointerId) ? event.pointerId : null
  connectPointer.value = canvasPoint(event)
  store.setError('')
}

function cancelConnection() {
  gesturePointerId.value = null
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
  if (!canRun.value) return
  try { await store.startRun() } catch { /* Store exposes the actionable failure. */ }
}

async function cancelWorkflow() {
  try { await store.cancelRun() } catch { /* Store exposes the actionable failure. */ }
}

function selectHistory(runId: string) {
  historyOpen.value = true
  void store.loadRun(runId)
}

export type CloseRequestResult = 'completed' | 'pending' | 'blocked'
function requestClose(action: () => void): CloseRequestResult {
  if (resourceBusy.value) { store.setError(message('workflow.resourceUploading')); return 'blocked' }
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
  if ((event.target as Element).closest('input, select, textarea, foreignObject')) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    focusNode(node)
  }
  if (event.key === 'Delete' && registry.value?.kind(node.type)?.removable !== false && !controlsLocked.value) {
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
  await store.loadOutputs(props.projectId, active.audio_id, active.id)
}

function nodeKindLabel(node: WorkflowNode) {
  return registry.value?.label(node.type) || node.type
}

function nodeKicker(node: WorkflowNode) {
  return node.type.replace(/_/g, ' ').toUpperCase()
}

function nodeDescription(node: WorkflowNode) {
  const definition = registry.value?.kind(node.type)
  if (definition?.isSource) return t('workflow.inputHelp', { name: inputAudioName.value })
  return t('workflow.genericDescription', { name: nodeLabel(node) })
}

function deleteLabel() { return t('workflow.deleteNode') }

function onWindowPointerMove(event: PointerEvent) {
  if (event.target instanceof Node && stage.value?.contains(event.target)) return
  onPointerMove(event)
}
function onWindowPointerUp(event: PointerEvent) { onPointerUp(event) }

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
  void document.fonts?.ready.then(scheduleMeasurement)
  window.addEventListener('beforeunload', guardUnload)
  window.addEventListener('pointermove', onWindowPointerMove)
  window.addEventListener('blur', clearGestures)
  window.addEventListener('pointerup', onWindowPointerUp)
  window.addEventListener('pointercancel', onWindowPointerUp)
})
onUnmounted(() => {
  measurementDisposed = true
  if (measurementFrame !== null) cancelAnimationFrame(measurementFrame)
  mutationObserver?.disconnect(); resizeObserver?.disconnect()
  store.resourceUploading = false
  store.stop()
  pendingClose = null
  clearGestures()
  window.removeEventListener('beforeunload', guardUnload)
  window.removeEventListener('pointermove', onWindowPointerMove)
  window.removeEventListener('blur', clearGestures)
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
      <div class="workflow-mode-note"><span class="mode-dot" aria-hidden="true"></span><strong>{{ t('workflow.asrTextOutput') }}</strong><span>{{ t('workflow.boundAudioHelp', { name: inputAudioName }) }}</span></div>
      <div class="workflow-toolbar" role="toolbar" :aria-label="t('workflow.toolbar')">
        <button v-for="kind in nodeKinds" :key="kind.type" class="secondary-button compact-button" type="button" :disabled="controlsLocked || (kind.isSource && !!currentInput) || store.draft.nodes.length >= (registry?.capabilities.constraints.maxNodes || 0) || (kind.maxInstances !== null && store.draft.nodes.filter((node) => node.type === kind.type).length >= kind.maxInstances)" @click="store.addNode(kind.type)">＋ {{ kind.addLabel }}</button>
        <span class="toolbar-rule" aria-hidden="true"></span>
        <span class="toolbar-spacer"></span>
        <button class="secondary-button compact-button" type="button" :disabled="!canValidateAndSave" @click="validateAndSave">{{ store.saving ? t('workflow.saving') : store.validating ? t('workflow.validating') : t('workflow.validateAndSave') }}</button>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomOut')" :disabled="store.zoom <= minZoom" @click="setZoom(store.zoom - .1)">−</button>
        <span class="zoom-value" aria-live="polite">{{ percentLabel(store.zoom) }}</span>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomIn')" :disabled="store.zoom >= 1.8" @click="setZoom(store.zoom + .1)">+</button>
        <button class="secondary-button compact-button" type="button" @click="fitCanvas">{{ t('workflow.fit') }}</button>
        <button class="secondary-button compact-button" type="button" @click="store.pan = { x: 0, y: 0 }">{{ t('workflow.resetView') }}</button>
      </div>

      <div class="workflow-main">
        <div ref="canvas" class="workflow-canvas" :style="gridStyle" @wheel="onWheel">
          <div class="canvas-caption"><span>{{ t('workflow.canvasHelp') }}</span><span>{{ t('workflow.version', { version: numberLabel(store.document?.workflow_revision) }) }}</span></div>
            <svg ref="stage" class="workflow-svg" width="100%" height="100%" role="group" :aria-label="t('workflow.canvasAria')" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @lostpointercapture="onPointerUp">

              <g class="workflow-content" :transform="`translate(${store.pan.x} ${store.pan.y}) scale(${store.zoom})`">
              <path v-for="edge in store.draft.edges" :key="edge.id" class="workflow-edge-hit" :d="edgePath(edge)" @pointerdown.stop @click.stop="!controlsLocked && removeEdge(edge.id)" />
              <path v-for="edge in store.draft.edges" :key="`${edge.id}-line`" class="workflow-edge" :class="{ 'is-run': store.runMatchesDraft && store.run?.status === 'completed' }" :d="edgePath(edge)" aria-hidden="true" />
              <path v-if="previewPath" class="workflow-edge-preview" :d="previewPath" aria-hidden="true" />
              <g v-for="node in store.draft.nodes" :key="node.id" class="workflow-node" :data-node-id="node.id" :class="[`is-${node.type}`, `is-state-${nodeStatus(node)}`, { 'is-selected': store.selectedNodeId === node.id, 'is-invalid': !!nodeIssue(node) }]" :transform="nodeTransform(node)" role="group" tabindex="0" :aria-label="t('workflow.nodeAria', { name: nodeLabel(node), kind: nodeKindLabel(node), status: nodeStatusLabel(nodeStatus(node)) })" @pointerdown.stop="onNodePointerDown($event, node)" @dblclick.stop="focusNode(node)" @click.stop="store.selectNode(node.id)" @keydown="keyboardSelect($event, node)">
                <defs>
                  <clipPath :id="`${clipId}-${node.id}-title`"><rect x="22" y="0" :width="nodeWidth(node) - 64" height="30" /></clipPath>
                  <clipPath :id="`${clipId}-${node.id}-port-in`"><rect x="10" y="32" :width="portClipWidth(node)" height="26" /></clipPath>
                  <clipPath :id="`${clipId}-${node.id}-port-out`"><rect :x="nodeWidth(node) - 10 - portClipWidth(node)" y="32" :width="portClipWidth(node)" height="26" /></clipPath>
                </defs>
                <rect class="node-card" :width="nodeWidth(node)" :height="nodeHeight(node)" rx="14" />
                <path class="node-header" :d="headerPath(node)" />
                <circle class="node-type-dot" cx="12" cy="15" r="3" />
                <text class="node-name" :clip-path="`url(#${clipId}-${node.id}-title)`" x="22" y="19"><title>{{ nodeLabel(node) }}</title>{{ nodeLabel(node) }}</text>
                <circle class="node-status-dot" :class="`is-${nodeStatus(node)}`" :cx="nodeWidth(node) - 12" cy="15" r="3"><title>{{ nodeStatusLabel(nodeStatus(node)) }}</title></circle>
                <foreignObject v-if="registry && (fieldsHeight(node) || kind(node)?.resourceInput || kind(node)?.preview)" @focusin="store.selectNode(node.id)" x="8" :y="WORKFLOW_NODE_FIELDS_Y" :width="nodeWidth(node) - 16" :height="nodeHeight(node) - WORKFLOW_NODE_FIELDS_Y - 10">
                  <div class="node-body" :style="bodyStyle(node)">
                    <div v-if="fieldsHeight(node)" class="node-body-fields"><NodeConfigFields :node="node" :registry="registry" :locked="controlsLocked" @update="editNode(node, $event)" /></div>
                    <div v-if="kind(node)?.resourceInput" class="node-body-resource">
                      <NodeResourceInput :project-id="projectId" :audios="audios || []" :value="node.config[kind(node)!.resourceInput!.configKey]" :source-language="project?.source_language || project?.source?.language || ''" :resource-type="kind(node)!.resourceInput!.resourceType" :locked="controlsLocked" @bind="bindResource(node, $event)" @uploaded="uploadedResource(node, $event)" @busy="resourceUploads[node.id] = $event" />
                    </div>
                    <div v-if="kind(node)?.preview" class="node-body-preview"><NodePreview :renderer="kind(node)!.preview!.renderer" :record="previewRecord(node)" :run-id="store.run?.id" :audio-id="store.run?.audio_id" :historical="!store.runMatchesDraft" /></div>
                  </div>
                </foreignObject>
                <text v-if="!fieldsHeight(node) && !kind(node)?.resourceInput && !kind(node)?.preview" class="node-meta" x="12" y="77"><title>{{ registry?.kind(node.type)?.isSource ? inputAudioName : nodeLabel(node) }}</title>{{ registry?.kind(node.type)?.isSource ? t('workflow.boundAudio') : nodeLabel(node) }}</text>
                <rect class="node-outline" :width="nodeWidth(node)" :height="nodeHeight(node)" rx="14" />
                <circle v-if="inputPortForNode(node.type)" class="node-port node-port-in" :class="`node-port-${inputPortForNode(node.type)}`" cx="0" :cy="portY" r="6" :aria-label="t('workflow.inputPort', { port: inputPortForNode(node.type) || '' })" @pointerdown.stop @pointerup.stop="!controlsLocked && finishConnection(node, $event)"><title>{{ t('workflow.inputPort', { port: inputPortForNode(node.type) || '' }) }}</title></circle><text v-if="inputPortForNode(node.type)" class="node-port-label node-port-label-in" :clip-path="`url(#${clipId}-${node.id}-port-in)`" x="10" :y="portY + 3" aria-hidden="true"><title>{{ portLabel(inputPortForNode(node.type)) }}</title>{{ portLabel(inputPortForNode(node.type)) }}</text>
                <circle v-if="outputPortForNode(node.type)" class="node-port node-port-out" :class="`node-port-${outputPortForNode(node.type)}`" :cx="nodeWidth(node)" :cy="portY" r="6" :aria-label="t('workflow.outputPort', { port: outputPortForNode(node.type) || '' })" @pointerdown.stop="!controlsLocked && startConnection(node, $event)"><title>{{ t('workflow.outputPort', { port: outputPortForNode(node.type) || '' }) }}</title></circle><text v-if="outputPortForNode(node.type)" class="node-port-label node-port-label-out" :clip-path="`url(#${clipId}-${node.id}-port-out)`" :x="nodeWidth(node) - 10" :y="portY + 3" aria-hidden="true"><title>{{ portLabel(outputPortForNode(node.type)) }}</title>{{ portLabel(outputPortForNode(node.type)) }}</text>
                <text v-if="nodeIssue(node)" class="node-error-mark" :x="nodeWidth(node) - 28" y="20" aria-hidden="true">!</text>
                <rect class="node-resize-handle is-right" data-resize="right" :x="nodeWidth(node) - 4" y="58" width="8" :height="Math.max(0, nodeHeight(node) - 70)" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeWidth', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'right')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'right')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <rect class="node-resize-handle is-bottom" data-resize="bottom" x="8" :y="nodeHeight(node) - 4" :width="nodeWidth(node) - 20" height="8" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeHeight', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'bottom')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'bottom')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <rect class="node-resize-handle is-corner" data-resize="corner" :x="nodeWidth(node) - 8" :y="nodeHeight(node) - 8" width="16" height="16" rx="4" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeNode', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'corner')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'corner')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <path class="node-resize-mark" :d="`M ${nodeWidth(node) - 9} ${nodeHeight(node) - 2} l 7 -7 M ${nodeWidth(node) - 4} ${nodeHeight(node) - 2} l 2 -2`" />
              </g>
              </g>
            </svg>
          <div v-if="!store.draft.nodes.length" class="workflow-empty"><strong>{{ t('workflow.emptyCanvas') }}</strong><span>{{ t('workflow.emptyCanvasHelp') }}</span></div>
        </div>

        <aside class="workflow-inspector" :class="selected ? `is-${selected.type}` : 'is-empty'" aria-labelledby="workflow-inspector-title">
          <div class="inspector-topline"><span class="section-kicker">{{ t('workflow.inspectorKicker') }}</span><span class="workflow-dirty-state">{{ store.dirty ? t('workflow.unsaved') : t('workflow.synced') }}</span></div>
          <div class="inspector-title">
            <span class="inspector-node-kind">{{ selected ? nodeKicker(selected) : t('workflow.selectNode') }}</span>
            <h3 id="workflow-inspector-title">{{ selected ? nodeLabel(selected) : t('workflow.selectNode') }}</h3>
            <code v-if="selected" class="inspector-type-id">{{ selected.type }}</code>
          </div>
          <template v-if="selected">
            <p class="inspector-type">{{ nodeDescription(selected) }}</p>
            <div class="workflow-field"><span>{{ t('workflow.nodeId') }}</span><code>{{ selected.id }}</code></div>
            <p class="inspector-help">{{ t('workflow.inlineEditHelp') }}</p>
            <div class="inspector-actions">
              <button v-if="selectedKind?.removable" class="text-button inspector-delete" type="button" :disabled="controlsLocked" @click="removeSelected">{{ deleteLabel() }}</button>
            </div>
            <p v-if="selectedKind?.isSource" class="inspector-help">{{ t('workflow.inputHelp', { name: inputAudioName }) }}</p>
          </template>
          <div v-else class="inspector-empty"><span aria-hidden="true">↖</span><p>{{ t('workflow.inspectorEmpty') }}</p></div>
          <dl class="workflow-inspector-meta"><div><dt>{{ t('workflow.workflowVersion') }}</dt><dd>{{ numberLabel(store.document?.workflow_revision) }}</dd></div><div><dt>{{ t('workflow.documentRevision') }}</dt><dd>{{ numberLabel(store.document?.revision) }}</dd></div><div><dt>{{ t('workflow.inputAudio') }}</dt><dd>{{ inputAudioName }}</dd></div><div><dt>{{ t('workflow.historyAudio') }}</dt><dd>{{ props.audioName }}</dd></div></dl>
        </aside>
      </div>

      <ul v-if="store.validationIssues.length" class="workflow-issues" aria-live="polite"><li v-for="(issue, index) in store.validationIssues" :key="`${issue.code}-${index}`"><button type="button" :disabled="!issue.node_id" @click="issue.node_id && (store.selectNode(issue.node_id), focusNode(store.draft.nodes.find((node) => node.id === issue.node_id)!))">{{ issue.node_id ? t('workflow.nodeReference', { id: issue.node_id }) : t('workflow.workflow') }}</button><span>{{ workflowIssueLabel(issue) }}</span></li></ul>

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
            <template v-if="node.artifact && node.status === 'completed' && !registry?.kind(node.node_type)?.preview">
              <p class="workflow-output-state" :class="`is-${outputCheckState(node.node_id)}`">{{ outputCheckState(node.node_id) === 'checking' ? t('workflow.artifactChecking') : outputCheckState(node.node_id) === 'missing' ? t('workflow.artifactMissing') : outputCheckState(node.node_id) === 'failed' ? t('workflow.artifactCheckFailed') : t('workflow.artifactAvailable') }}</p>
              <button v-if="canDownloadOutput(node.node_id)" class="secondary-button compact-button" type="button" @click="download(node.node_id)">{{ t('workflow.download', { filename: node.artifact?.filename || '' }) }}</button>
              <button v-else-if="outputCheckState(node.node_id) === 'failed'" class="secondary-button compact-button" type="button" @click="retryOutputChecks">{{ t('workflow.retryChecks') }}</button>
            </template>
          </article>
        </div>
        <div v-if="historyOpen" class="workflow-history"><div class="workflow-history-heading"><h4>{{ t('workflow.history') }}</h4><button class="text-button" type="button" :disabled="store.historyLoading" @click="store.loadHistory()">{{ store.historyLoading ? t('workflow.refreshing') : t('workflow.refresh') }}</button></div><p v-if="store.historyError" class="workflow-run-error" role="alert">{{ store.historyError }}</p><div v-if="!store.history.length && !store.historyLoading" class="workflow-history-empty">{{ t('workflow.historyEmpty') }}</div><button v-for="item in store.history" :key="item.id" class="workflow-history-row" :class="{ 'is-active': store.run?.id === item.id }" type="button" :disabled="store.runLoading || !!store.activeRun" @click="selectHistory(item.id)"><span><strong>{{ item.id }}</strong><small>{{ dateLabel(item.created_at) }} · {{ t('workflow.version', { version: numberLabel(item.workflow_revision) }) }}</small></span><span class="run-status" :class="`is-${item.status}`">{{ runStatusLabel(item.status) }}</span></button></div>
      </section>
    </template>

  </section>
</template>
