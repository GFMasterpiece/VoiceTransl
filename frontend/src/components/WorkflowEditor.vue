<script setup lang="ts">
import { t, locale, message, hasMessage } from '../i18n'
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import {
  workflowIssueLabel,
  WORKFLOW_NODE_HEIGHT, WORKFLOW_NODE_PORT_Y, WORKFLOW_NODE_FIELDS_Y, workflowNodeHeight,
  WORKFLOW_NODE_WIDTH, WORKFLOW_NODE_ROW_GAP, WORKFLOW_RESOURCE_HEIGHT, WORKFLOW_PREVIEW_HEIGHT, workflowFieldsHeight,
  executionSignature,
  inputPortForNode as registeredInputPortForNode,
  nodeLabel as registeredNodeLabel,
  outputPortForNode as registeredOutputPortForNode,
  useWorkflowStore,
  type WorkflowNode,
} from '../stores/workflow'

import NodeConfigFields from './NodeConfigFields.vue'
import CanvasDialog from './CanvasDialog.vue'
import WorkflowPanels from './WorkflowPanels.vue'
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
type PortDirection = 'input' | 'output'
const connectionStart = ref<{ nodeId: string; direction: PortDirection } | null>(null)
const gesturePointerId = ref<number | null>(null)
const activePanel = ref<'run' | 'artifacts' | 'logs' | null>(null)
const contextMenu = ref<{ x: number; y: number; world: { x: number; y: number }; nodeId?: string } | null>(null)
const menuElement = ref<HTMLElement | null>(null)
const menuSearch = ref('')
let menuTrigger: Element | null = null
const discardPrompt = ref(false)
let pendingClose: (() => void) | null = null
const controlsLocked = computed(() => store.saving || store.validating || !!store.activeRun || store.actionLoading || store.resourceUploading || resourceBusy.value)
const canValidateAndSave = computed(() => store.dirty && !controlsLocked.value && !store.loading)

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

const filteredKinds = computed(() => nodeKinds.value.filter((entry) => `${entry.label} ${entry.addLabel} ${entry.type}`.toLocaleLowerCase().includes(menuSearch.value.trim().toLocaleLowerCase())))
function addDisabledReason(type: string) {
  if (controlsLocked.value) return t('workflow.leaveSaving')
  const definition = registry.value?.kind(type)
  if (!definition) return t('workflow.capUnavailable')
  if (store.draft.nodes.length >= (registry.value?.capabilities.constraints.maxNodes || 0)) return t('workflow.graphNodeLimit')
  if (definition.isSource && store.draft.nodes.filter((node) => registry.value?.kind(node.type)?.isSource).length >= (registry.value?.capabilities.constraints.maxSources ?? 0)) return t('workflow.inputCount', { p0: registry.value?.capabilities.constraints.maxSources ?? 0 })
  if (definition.maxInstances !== null && store.draft.nodes.filter((node) => node.type === type).length >= definition.maxInstances) return t('workflow.nodeInstanceLimit')
  return ''
}
function closeMenu(restore = false) {
  contextMenu.value = null; menuSearch.value = ''
  if (restore && menuTrigger?.isConnected && 'focus' in menuTrigger) (menuTrigger as HTMLElement).focus()
  menuTrigger = null
}
async function showMenu(x: number, y: number, world: { x: number; y: number }, nodeId?: string, trigger?: Element) {
  if (controlsLocked.value || activePanel.value) return
  clearGestures()
  menuTrigger = trigger || stage.value
  menuSearch.value = ''
  if (nodeId) store.selectNode(nodeId)
  contextMenu.value = { x, y, world, nodeId }
  await nextTick()
  if (!contextMenu.value || !menuElement.value) return
  const bounds = menuElement.value.getBoundingClientRect()
  contextMenu.value.x = Math.max(8, Math.min(x, window.innerWidth - (bounds.width || 264) - 8))
  contextMenu.value.y = Math.max(8, Math.min(y, window.innerHeight - (bounds.height || 320) - 8))
  ;(menuElement.value.querySelector<HTMLElement>('input, button:not([disabled])') || menuElement.value).focus()
}
function canvasContextMenu(event: MouseEvent) {
  const target = event.target as Element
  if (target.closest('input, textarea, select, audio, video, button, [contenteditable="true"]')) return
  event.preventDefault()
  const group = target.closest<SVGGElement>('.workflow-node')
  void showMenu(event.clientX, event.clientY, canvasPoint(event as PointerEvent), group?.dataset.nodeId, group || stage.value || undefined)
}
function openAddMenu(event?: MouseEvent) {
  const bounds = stage.value?.getBoundingClientRect()
  if (!bounds) return
  const clientX = bounds.left + bounds.width / 2
  const clientY = bounds.top + bounds.height / 2
  const point = canvasPoint({ clientX, clientY } as PointerEvent)
  const trigger = event?.currentTarget as Element | undefined
  const anchor = trigger?.getBoundingClientRect()
  void showMenu(anchor?.left ?? clientX, anchor?.bottom ?? clientY, point, undefined, trigger)
}
function addFromMenu(type: string) {
  const menu = contextMenu.value
  if (!menu || addDisabledReason(type)) return
  store.addNode(type, menu.world)
  closeMenu(true)
}
function deleteFromMenu() {
  const node = store.draft.nodes.find((entry) => entry.id === contextMenu.value?.nodeId)
  if (!node || controlsLocked.value || registry.value?.kind(node.type)?.removable === false) return
  closeMenu(true)
  if (window.confirm(t('workflow.deleteConfirm', { name: nodeLabel(node) }))) store.removeNode(node.id)
}
function menuKey(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); closeMenu(true); return }
  const items = [...(menuElement.value?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') || [])]
  if (event.key === 'Enter' && event.target instanceof HTMLInputElement) { event.preventDefault(); items[0]?.click(); return }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || !items.length) return
  event.preventDefault()
  const current = items.indexOf(document.activeElement as HTMLButtonElement)
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
  items[index]?.focus()
}
function outsideMenu(event: PointerEvent) { if (contextMenu.value && (!(event.target instanceof Node) || !menuElement.value?.contains(event.target))) closeMenu() }
function canvasKeyboard(event: KeyboardEvent) {
  if ((event.target as Element).closest('.workflow-node, input, textarea, select, audio, video')) return
  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { event.preventDefault(); openAddMenu() }
}
watch(() => [store.pan.x, store.pan.y, store.zoom, controlsLocked.value, activePanel.value], () => { closeMenu() }, { flush: 'sync' })

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
  const start = connectionStart.value
  const pointer = connectPointer.value
  if (!start || !pointer) return null
  const node = store.draft.nodes.find((item) => item.id === start.nodeId)
  if (!node) return null
  return start.direction === 'output'
    ? connectionPath(node.position.x + nodeWidth(node), node.position.y + portY, pointer.x, pointer.y)
    : connectionPath(pointer.x, pointer.y, node.position.x, node.position.y + portY)
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
      if (Math.abs(y) > 1_000_000) store.setError(message('workflow.nodePlacementFailed'))
      else if (y !== node.position.y) store.updateNode(node.id, { position: { ...node.position, y } })
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
  if (connectionStart.value) {
    if (connectPointerId.value !== null && event.pointerId !== connectPointerId.value) return
    connectPointer.value = canvasPoint(event)
    return
  }
  if (drag.value) {
    const point = canvasPoint(event)
    const node = store.draft.nodes.find((item) => item.id === drag.value!.nodeId)
    if (!node) return
    node.position = {
      x: Math.max(-1_000_000, Math.min(1_000_000, Math.round(drag.value.x + point.x - drag.value.startX))),
      y: Math.max(-1_000_000, Math.min(1_000_000, Math.round(drag.value.y + point.y - drag.value.startY))),
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
  if (connectionStart.value && event.type === 'pointerup') finishConnection(event)
  else clearGestures()
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

function startConnection(node: WorkflowNode, direction: PortDirection, event: PointerEvent) {
  const port = direction === 'output' ? outputPortForNode(node.type) : inputPortForNode(node.type)
  if (!port || controlsLocked.value || event.button !== 0 || event.isPrimary === false) return
  if (gesturePointerId.value !== null && gesturePointerId.value !== event.pointerId) return
  event.preventDefault()
  clearGestures()
  beginPointer(event, false)
  connectionStart.value = { nodeId: node.id, direction }
  connectPointerId.value = Number.isFinite(event.pointerId) ? event.pointerId : null
  connectPointer.value = canvasPoint(event)
  store.setError('')
}

function cancelConnection() {
  gesturePointerId.value = null
  connectionStart.value = null
  connectPointer.value = null
  connectPointerId.value = null
}

function destinationPort(event: PointerEvent): { nodeId: string; direction: PortDirection } | null {
  // Touch and pen implicitly capture pointer events on the starting socket.
  // Hit-test viewport coordinates so releasing over the opposite socket still
  // resolves the actual destination rather than the captured event target.
  const hit = typeof document.elementFromPoint === 'function'
    ? document.elementFromPoint(event.clientX, event.clientY)
    : event.target instanceof Element ? event.target : null
  const socket = hit?.closest('.node-port')
  if (!socket || !stage.value?.contains(socket)) return null
  const nodeId = (socket.closest('.workflow-node') as SVGGElement | null)?.dataset.nodeId
  const direction = (socket as SVGElement).dataset.portDirection
  if (!nodeId || (direction !== 'input' && direction !== 'output')) return null
  return { nodeId, direction }
}

function finishConnection(event: PointerEvent) {
  if (connectPointerId.value !== null && event.pointerId !== connectPointerId.value) return
  const start = connectionStart.value
  const destination = destinationPort(event)
  clearGestures()
  if (!start || !destination || controlsLocked.value || start.direction === destination.direction) return
  const source = start.direction === 'output' ? start.nodeId : destination.nodeId
  const target = start.direction === 'input' ? start.nodeId : destination.nodeId
  store.connect(source, target)
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

export type CloseRequestResult = 'completed' | 'pending' | 'blocked'
function requestClose(action: () => void): CloseRequestResult {
  if (resourceBusy.value) { store.setError(message('workflow.resourceUploading')); return 'blocked' }
  if (store.activeRun) {
    store.setError(message('workflow.leaveActive'))
    return 'blocked'
  }
  if (store.saving || store.validating || store.actionLoading) {
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
  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
    if ((event.target as Element).closest('input, select, textarea, audio, video')) return
    event.preventDefault(); event.stopPropagation()
    const bounds = (event.currentTarget as SVGGElement).getBoundingClientRect()
    void showMenu(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2, node.position, node.id, event.currentTarget as Element)
    return
  }
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

function nodeKindLabel(node: WorkflowNode) {
  return registry.value?.label(node.type) || node.type
}

function onWindowPointerMove(event: PointerEvent) {
  if (event.target instanceof Node && stage.value?.contains(event.target)) return
  onPointerMove(event)
}
function onWindowPointerUp(event: PointerEvent) { onPointerUp(event) }

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

defineExpose({ requestClose })

onMounted(() => {
  void store.load(props.projectId, props.audioId)
  void document.fonts?.ready.then(scheduleMeasurement)
  window.addEventListener('pointerdown', outsideMenu, true)
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
  window.removeEventListener('pointerdown', outsideMenu, true)
  window.removeEventListener('beforeunload', guardUnload)
  window.removeEventListener('pointermove', onWindowPointerMove)
  window.removeEventListener('blur', clearGestures)
  window.removeEventListener('pointerup', onWindowPointerUp)
  window.removeEventListener('pointercancel', onWindowPointerUp)
})
</script>

<template>
  <section class="node-workflow" aria-labelledby="node-workflow-title" :aria-busy="store.loading || store.saving ? 'true' : 'false'">
    <h2 id="node-workflow-title" class="visually-hidden">{{ t('workflow.title') }}</h2>
    <div class="workflow-toolbar" role="toolbar" :aria-label="t('workflow.toolbar')">
      <div class="workflow-workspace-header"><slot name="workspace-header" /></div>
      <div class="workflow-toolbar-actions">
        <button class="secondary-button compact-button" type="button" data-add-node :disabled="controlsLocked || store.loading" @click="openAddMenu">＋ {{ t('workflow.addNode') }}</button>
        <button class="secondary-button compact-button" type="button" :disabled="!canValidateAndSave" @click="validateAndSave">{{ store.saving ? t('workflow.saving') : store.validating ? t('workflow.validating') : t('workflow.validateAndSave') }}</button>
        <span class="workflow-save-state" role="status">{{ store.dirty ? t('workflow.unsaved') : t('workflow.synced') }}</span>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomOut')" :disabled="store.zoom <= minZoom" @click="setZoom(store.zoom - .1)">−</button>
        <span class="zoom-value" aria-live="polite">{{ percentLabel(store.zoom) }}</span>
        <button class="secondary-button compact-button" type="button" :aria-label="t('workflow.zoomIn')" :disabled="store.zoom >= 1.8" @click="setZoom(store.zoom + .1)">+</button>
        <button class="secondary-button compact-button" type="button" @click="fitCanvas">{{ t('workflow.fit') }}</button>
        <button class="secondary-button compact-button" type="button" @click="store.pan = { x: 0, y: 0 }">{{ t('workflow.resetView') }}</button>
        <button v-for="panel in (['run', 'artifacts', 'logs'] as const)" :key="panel" class="secondary-button compact-button" type="button" :data-open-panel="panel" :aria-expanded="activePanel === panel" @click="activePanel = panel">{{ t(`workflow.${panel === 'run' ? 'panelRun' : panel === 'artifacts' ? 'panelArtifacts' : 'panelLogs'}`) }}</button>
      </div>
    </div>
    <div v-if="store.capabilitiesError || store.error" class="alert node-workflow-alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ store.capabilitiesError || store.error }}</span><button type="button" :aria-label="t('workflow.closeAlert')" @click="store.capabilitiesError = ''; store.error = ''">×</button><button v-if="store.capabilitiesError" class="secondary-button compact-button" type="button" @click="reloadCapabilities">{{ t('workflow.retryCapabilities') }}</button></div>
    <div v-if="store.notice" class="workflow-notice" role="status">{{ store.notice }}</div>
    <CanvasDialog :open="discardPrompt" :title="t('workflow.discardTitle')" @close="discardPrompt = false">
      <div class="workflow-discard"><p>{{ t('workflow.discardDescription') }}</p><div><button class="secondary-button" type="button" @click="discardPrompt = false">{{ t('workflow.continueEditing') }}</button><button class="primary-button" type="button" @click="discardAndClose">{{ t('workflow.discardChanges') }}</button></div></div>
    </CanvasDialog>

    <div v-if="store.loading" class="workflow-loading" role="status">{{ t('workflow.loading') }}</div>
    <template v-else>
      <div class="workflow-main">
        <div ref="canvas" class="workflow-canvas" :style="gridStyle" @wheel="onWheel" @contextmenu="canvasContextMenu">
          <div class="canvas-caption"><span>{{ t('workflow.canvasHelp') }}</span><span>{{ t('workflow.version', { version: numberLabel(store.document?.workflow_revision) }) }}</span></div>
            <svg ref="stage" class="workflow-svg" width="100%" height="100%" role="group" tabindex="0" :aria-label="t('workflow.canvasAria')" @keydown="canvasKeyboard" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @lostpointercapture="onPointerUp">

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
                <circle v-if="inputPortForNode(node.type)" class="node-port node-port-in" data-port-direction="input" :class="`node-port-${inputPortForNode(node.type)}`" cx="0" :cy="portY" r="6" :aria-label="t('workflow.inputPort', { port: inputPortForNode(node.type) || '' })" @pointerdown.stop="startConnection(node, 'input', $event)" @pointerup.stop="finishConnection($event)"><title>{{ t('workflow.inputPort', { port: inputPortForNode(node.type) || '' }) }}</title></circle><text v-if="inputPortForNode(node.type)" class="node-port-label node-port-label-in" :clip-path="`url(#${clipId}-${node.id}-port-in)`" x="10" :y="portY + 3" aria-hidden="true"><title>{{ portLabel(inputPortForNode(node.type)) }}</title>{{ portLabel(inputPortForNode(node.type)) }}</text>
                <circle v-if="outputPortForNode(node.type)" class="node-port node-port-out" data-port-direction="output" :class="`node-port-${outputPortForNode(node.type)}`" :cx="nodeWidth(node)" :cy="portY" r="6" :aria-label="t('workflow.outputPort', { port: outputPortForNode(node.type) || '' })" @pointerdown.stop="startConnection(node, 'output', $event)" @pointerup.stop="finishConnection($event)"><title>{{ t('workflow.outputPort', { port: outputPortForNode(node.type) || '' }) }}</title></circle><text v-if="outputPortForNode(node.type)" class="node-port-label node-port-label-out" :clip-path="`url(#${clipId}-${node.id}-port-out)`" :x="nodeWidth(node) - 10" :y="portY + 3" aria-hidden="true"><title>{{ portLabel(outputPortForNode(node.type)) }}</title>{{ portLabel(outputPortForNode(node.type)) }}</text>
                <text v-if="nodeIssue(node)" class="node-error-mark" :x="nodeWidth(node) - 28" y="20" aria-hidden="true">!</text>
                <rect class="node-resize-handle is-right" data-resize="right" :x="nodeWidth(node) - 4" y="58" width="8" :height="Math.max(0, nodeHeight(node) - 70)" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeWidth', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'right')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'right')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <rect class="node-resize-handle is-bottom" data-resize="bottom" x="8" :y="nodeHeight(node) - 4" :width="nodeWidth(node) - 20" height="8" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeHeight', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'bottom')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'bottom')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <rect class="node-resize-handle is-corner" data-resize="corner" :x="nodeWidth(node) - 16" :y="nodeHeight(node) - 16" width="24" height="24" rx="4" role="button" :tabindex="controlsLocked ? -1 : 0" :aria-disabled="controlsLocked" :aria-label="t('workflow.resizeNode', { name: nodeLabel(node) })" @pointerdown.stop="startResize($event, node, 'corner')" @click.stop @dblclick.stop @keydown.stop="resizeKey($event, node, 'corner')"><title>{{ t('workflow.resizeHelp') }}</title></rect>
                <path class="node-resize-mark" :d="`M ${nodeWidth(node) - 14} ${nodeHeight(node) - 8} l 6 -6 M ${nodeWidth(node) - 10} ${nodeHeight(node) - 8} l 2 -2`" />
              </g>
              </g>
            </svg>
          <div v-if="!store.draft.nodes.length" class="workflow-empty"><strong>{{ t('workflow.emptyCanvas') }}</strong><span>{{ t('workflow.emptyCanvasHelp') }}</span></div>
        </div>

      </div>

      <ul v-if="store.validationIssues.length" class="workflow-issues" aria-live="polite"><li v-for="(issue, index) in store.validationIssues" :key="`${issue.code}-${index}`"><button type="button" :disabled="!issue.node_id" @click="issue.node_id && (store.selectNode(issue.node_id), focusNode(store.draft.nodes.find((node) => node.id === issue.node_id)!))">{{ issue.node_id ? t('workflow.nodeReference', { id: issue.node_id }) : t('workflow.workflow') }}</button><span>{{ workflowIssueLabel(issue) }}</span></li></ul>

    </template>

    <div v-if="contextMenu" ref="menuElement" class="canvas-context-menu" role="menu" tabindex="-1" :aria-label="t('workflow.canvasMenu')" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @pointerdown.stop @contextmenu.prevent @keydown="menuKey">
      <template v-if="!contextMenu.nodeId">
        <label class="canvas-node-search"><span class="visually-hidden">{{ t('workflow.searchNodes') }}</span><input v-model="menuSearch" type="search" @contextmenu.stop :placeholder="t('workflow.searchNodes')" :aria-label="t('workflow.searchNodes')" /></label>
        <button v-for="entry in filteredKinds" :key="entry.type" role="menuitem" type="button" :data-add-kind="entry.type" :disabled="!!addDisabledReason(entry.type)" :title="addDisabledReason(entry.type) || entry.label" @click="addFromMenu(entry.type)"><span>＋ {{ entry.addLabel }}</span><small v-if="addDisabledReason(entry.type)">{{ addDisabledReason(entry.type) }}</small></button>
        <p v-if="!filteredKinds.length" role="status">{{ t('workflow.noMatchingNodes') }}</p>
      </template>
      <button v-else role="menuitem" type="button" data-delete-node :disabled="controlsLocked || registry?.kind(store.selectedNode?.type || '')?.removable === false" :title="registry?.kind(store.selectedNode?.type || '')?.removable === false ? t('workflow.nodeRequired') : t('workflow.deleteNode')" @click="deleteFromMenu">{{ t('workflow.deleteNode') }}<small v-if="registry?.kind(store.selectedNode?.type || '')?.removable === false">{{ t('workflow.nodeRequired') }}</small></button>
    </div>
    <WorkflowPanels :panel="activePanel" @close="activePanel = null" />
  </section>
</template>
