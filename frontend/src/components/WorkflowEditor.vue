<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  WORKFLOW_NODE_HEIGHT,
  WORKFLOW_NODE_WIDTH,
  executionSignature,
  inputPortForNode as registeredInputPortForNode,
  isTerminalNode,
  nodeLabel as registeredNodeLabel,
  outputPortForNode as registeredOutputPortForNode,
  readTextOutputConfig,
  useWorkflowStore,
  validateNodeConfig as registeredValidateNodeConfig,
  type TextOutputFormat,
  type WorkflowNode,
} from '../stores/workflow'

const props = defineProps<{ projectId: string; audioId: string; audioName: string }>()
const store = useWorkflowStore()
const canvas = ref<HTMLDivElement | null>(null)
const stage = ref<SVGSVGElement | null>(null)
const drag = ref<{ nodeId: string; startX: number; startY: number; x: number; y: number } | null>(null)
const panning = ref<{ startX: number; startY: number; x: number; y: number } | null>(null)
const connectFrom = ref<string | null>(null)
const pointerTarget = ref<string | null>(null)
const settingsOpen = ref(false)
const historyOpen = ref(false)
const discardPrompt = ref(false)
let pendingClose: (() => void) | null = null
const filenameDraft = ref('')
const textFormatDraft = ref<TextOutputFormat>('txt')
const genericDraft = ref<Record<string, unknown>>({})
const settingsError = ref('')
const selectedKind = computed(() => selected.value && registry.value ? registry.value.kind(selected.value.type) : null)
const dedicatedOutputKinds = new Set(['audio_output', 'text_output'])
const genericFields = computed(() => selectedKind.value && !dedicatedOutputKinds.has(selectedKind.value.configKind) ? selectedKind.value.fields : [])
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
    if (!value || typeof value !== 'object' || Array.isArray(value)) { settingsError.value = '此字段必须是 JSON 对象。'; return }
    genericDraft.value = { ...genericDraft.value, [field.key]: value }
    settingsError.value = ''
  } catch { settingsError.value = '此字段不是有效 JSON。' }
}
const selected = computed(() => store.selectedNode)
const currentInput = computed(() => store.draft.nodes.find((node) => node.type === 'audio_input'))
const connectionSources = computed(() => store.draft.nodes.filter((node) => !!outputPortForNode(node.type)))
const connectionTargets = computed(() => store.draft.nodes.filter((node) => !!inputPortForNode(node.type)))
const connectionSource = computed(() => selected.value && outputPortForNode(selected.value.type) ? selected.value : currentInput.value)
const controlsLocked = computed(() => store.saving || store.validating || !!store.activeRun)
const canValidateAndSave = computed(() => store.dirty && !store.saving && !store.loading && !store.validating && !store.activeRun)
const canRun = computed(() => !store.dirty && !store.saving && !!store.document && !store.activeRun && !store.actionLoading)

const nodeWidth = WORKFLOW_NODE_WIDTH
const nodeHeight = WORKFLOW_NODE_HEIGHT
const registry = computed(() => store.registry)
const nodeKinds = computed(() => registry.value?.kinds || [])
function outputPortForNode(type: string) { return registry.value ? registeredOutputPortForNode(type, registry.value) : null }
function inputPortForNode(type: string) { return registry.value ? registeredInputPortForNode(type, registry.value) : null }
function nodeLabel(node: WorkflowNode) { return registry.value ? registeredNodeLabel(node, registry.value) : node.type }
function validateNodeConfig(node: Pick<WorkflowNode, 'id' | 'type' | 'config'>) { return registry.value ? registeredValidateNodeConfig(node, registry.value) : [{ code: 'CAPABILITIES_UNAVAILABLE', message: '能力目录不可用' }] }
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
  const bend = Math.max(48, Math.abs(tx - sx) * .45)
  return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`
}

function canvasPoint(event: PointerEvent) {
  const bounds = stage.value?.getBoundingClientRect()
  if (!bounds) return { x: 0, y: 0 }
  return { x: (event.clientX - bounds.left - store.pan.x) / store.zoom, y: (event.clientY - bounds.top - store.pan.y) / store.zoom }
}

function openNode(node: WorkflowNode) {
  if (controlsLocked.value) return
  store.selectNode(node.id)
  filenameDraft.value = typeof node.config.filename_stem === 'string' ? node.config.filename_stem : 'audio-copy'
  textFormatDraft.value = readTextOutputConfig(node.config).format
  const defaults = registry.value?.createDefaultConfig(node.type) || {}
  genericDraft.value = { ...defaults, ...node.config }
  settingsError.value = ''
  settingsOpen.value = true
  void nextTick(() => {
    const target = document.getElementById(node.type === 'audio_output' || node.type === 'text_output' ? 'workflow-filename-stem' : 'workflow-config-provider')
    const focusTarget = target || document.querySelector<HTMLElement>('.workflow-settings-modal select, .workflow-settings-modal input, .workflow-settings-modal button')
    focusTarget?.focus()
  })
}

function closeSettings() {
  if (store.saving) return
  settingsOpen.value = false
}

function commitSettings() {
  if (!selected.value || store.saving || store.activeRun) return
  if (selected.value.type === 'audio_output' || selected.value.type === 'text_output') {
    const stem = filenameDraft.value.trim()
    const reserved = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(stem)
    if (!stem || stem === '.' || stem === '..' || stem.endsWith('.') || reserved || stem.includes(String.fromCharCode(92)) || /[\/<>:"|?*\u0000-\u001f\u007f]/.test(stem) || stem.length > 128) {
      settingsError.value = '输出文件名不能为空、不能是系统保留名称，且不能包含路径分隔符或危险字符。'
      return
    }
    store.updateNode(selected.value.id, {
      config: selected.value.type === 'text_output'
        ? { filename_stem: stem, format: textFormatDraft.value }
        : { filename_stem: stem },
    })
  } else {
    const config = { ...genericDraft.value }
    const issues = validateNodeConfig({ id: selected.value.id, type: selected.value.type, config })
    if (issues.length) {
      settingsError.value = issues[0].message
      return
    }
    store.updateNode(selected.value.id, { config })
  }
  settingsOpen.value = false
}

function removeSelected() {
  if (!selected.value || selected.value.type === 'audio_input' || registry.value?.kind(selected.value.type)?.removable === false) return
  if (!window.confirm(`删除节点“${nodeLabel(selected.value)}”？相关连线也会删除。`)) return
  store.removeNode(selected.value.id)
  settingsOpen.value = false
}

function onNodePointerDown(event: PointerEvent, node: WorkflowNode) {
  if (store.activeRun) return
  if ((event.target as Element).closest('button')) return
  event.preventDefault()
  const point = canvasPoint(event)
  drag.value = { nodeId: node.id, startX: point.x, startY: point.y, x: node.position.x, y: node.position.y }
  store.selectNode(node.id)
  ;(event.currentTarget as SVGGElement).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (store.activeRun) return
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
  if (connectFrom.value && pointerTarget.value) store.connect(connectFrom.value, pointerTarget.value)
  drag.value = null
  panning.value = null
  connectFrom.value = null
  pointerTarget.value = null
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

function startConnection(node: WorkflowNode) {
  if (!outputPortForNode(node.type) || store.activeRun) return
  connectFrom.value = node.id
  store.error = ''
}

function finishConnection(node: WorkflowNode) {
  const source = connectFrom.value
  if (!source || !inputPortForNode(node.type)) {
    connectFrom.value = null
    return
  }
  // Clear the pending source before the click bubbles to the SVG pointerup.
  connectFrom.value = null
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
      store.error = '草稿在校验期间已修改，请重新校验并保存。'
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
    store.error = '请先保存当前工作流。'
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
    settingsError.value = ''
  }
})

export type CloseRequestResult = 'completed' | 'pending' | 'blocked'
function requestClose(action: () => void): CloseRequestResult {
  if (store.activeRun) {
    store.error = '节点运行尚未结束，请等待完成或取消后再离开。'
    return 'blocked'
  }
  if (store.saving || store.validating) {
    store.error = '节点工作流正在校验或保存，请完成后再离开。'
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
  if (event.key === 'Delete' && node.type !== 'audio_input' && registry.value?.kind(node.type)?.removable !== false && !store.activeRun) {
    event.preventDefault()
    if (window.confirm(`删除节点“${nodeLabel(node)}”？`)) store.removeNode(node.id)
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
  const label = registry.value?.label(node.type) || node.type
  if (node.type === 'audio_input') return label
  if (node.type === 'preprocess' || node.type === 'asr') return `Mock ${label}`
  if (node.type === 'text_output') return `${readTextOutputConfig(node.config).format.toUpperCase()} 文本输出`
  return label
}

function nodeKicker(node: WorkflowNode) {
  if (node.type === 'audio_input') return 'AUDIO INPUT'
  if (node.type === 'preprocess') return 'PREPROCESS'
  if (node.type === 'asr') return 'SPEECH TO TEXT'
  if (node.type === 'text_output') return 'TEXT OUTPUT'
  return 'AUDIO OUTPUT'
}

function configFieldLabel(field: { type: string; options: readonly { value: string; label: string }[] }, value: unknown) {
  if (field.type === 'select') return field.options.find((option) => option.value === value)?.label || String(value)
  return String(value)
}

function nodeMeta(node: WorkflowNode) {
  if (node.type === 'audio_input') return '绑定当前音频'
  if (node.type === 'preprocess' || node.type === 'asr') {
    const fields = registry.value?.kind(node.type)?.fields || []
    const values = fields
      .map((field) => ({ field, value: node.config[field.key] === undefined ? field.default : node.config[field.key] }))
      .filter((entry) => entry.value !== null && entry.value !== undefined && entry.value !== '')
      .slice(0, 2)
      .map((entry) => configFieldLabel(entry.field, entry.value))
    const summary = values.join(' · ') || 'Mock'
    return node.type === 'preprocess' ? `${summary} · 原字节直通` : summary
  }
  if (node.type === 'text_output') {
    const config = readTextOutputConfig(node.config)
    return `${config.format.toUpperCase()} · ${config.filename_stem}`
  }
  return `文件名 · ${node.config.filename_stem || 'audio-copy'}`
}

function runStatusLabel(status?: string) {
  return ({ pending: '排队中', running: '执行中', completed: '已完成', partial: '部分成功', failed: '失败', cancelling: '取消中', cancelled: '已取消', interrupted: '已中断' } as Record<string, string>)[status || ''] || status || '尚未运行'
}

function nodeStatusLabel(status: string) {
  return ({ pending: '等待', running: '执行中', completed: '完成', failed: '失败', blocked: '被阻塞', cancelled: '已取消', interrupted: '已中断', draft: '草稿' } as Record<string, string>)[status] || status
}

function sizeLabel(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function dateLabel(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录'
}

function connectFromLabel() {
  const node = store.draft.nodes.find((item) => item.id === connectFrom.value)
  const port = node ? outputPortForNode(node.type) : null
  return node && port ? `从「${nodeLabel(node)}」的 ${port} 端口选择同类型入端口完成连线` : ''
}

defineExpose({ requestClose })

onMounted(() => { void store.load(props.projectId, props.audioId) })
onUnmounted(() => {
  store.stop()
  pendingClose = null
})
</script>

<template>
  <section class="node-workflow" aria-labelledby="node-workflow-title" :aria-busy="store.loading || store.saving ? 'true' : 'false'">
    <header class="node-workflow-heading">
      <div>
        <span class="section-kicker">NODE WORKFLOW / MVP</span>
        <h2 id="node-workflow-title">节点工作流 <span class="mvp-badge">音频分支</span></h2>
        <p>支持音频直出，以及固定的 Mock 预处理、Mock 语音识别验证示例。Mock 不执行真实识别或媒体转换；节点按直接上游和 typed 端口连接，源文件始终保留。</p>
      </div>
      <div class="node-workflow-actions">
        <button class="secondary-button" type="button" :disabled="!!store.activeRun" @click="historyOpen = !historyOpen">{{ historyOpen ? '收起历史' : `运行历史 · ${store.history.length}` }}</button>
      </div>
    </header>

    <div v-if="store.capabilitiesError || store.error" class="alert node-workflow-alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ store.capabilitiesError || store.error }}</span><button type="button" aria-label="关闭节点工作流提示" @click="store.capabilitiesError = ''; store.error = ''">×</button><button v-if="store.capabilitiesError" class="secondary-button compact-button" type="button" @click="reloadCapabilities">重试能力目录</button></div>
    <div v-if="store.notice" class="workflow-notice" role="status">{{ store.notice }}</div>
        <div v-if="discardPrompt" class="workflow-discard" role="alertdialog" aria-labelledby="discard-title"><div><strong id="discard-title">放弃未保存的节点工作流修改？</strong><p>离开节点工作流后，画布和节点设置修改将丢失。已启动的任务不受影响。</p></div><div><button class="secondary-button" type="button" @click="discardPrompt = false">继续编辑</button><button class="primary-button" type="button" @click="discardAndClose">放弃修改</button></div></div>

    <div v-if="store.loading" class="workflow-loading" role="status">正在读取项目节点工作流…</div>
    <template v-else>
      <div class="workflow-mode-note"><span class="mode-dot" aria-hidden="true"></span><strong>ASR 与文本输出</strong><span>输入绑定当前音频：{{ props.audioName }}。端口必须同类型连接；新增节点不会静默替换原有连线。</span></div>
      <div class="workflow-toolbar" role="toolbar" aria-label="节点工作流工具">
        <button v-for="kind in nodeKinds" :key="kind.type" class="secondary-button compact-button" type="button" :disabled="store.saving || !!store.activeRun || (kind.type === 'audio_input' && !!currentInput) || (kind.maxInstances !== null && store.draft.nodes.filter((node) => node.type === kind.type).length >= kind.maxInstances)" @click="store.addNode(kind.type)">＋ {{ kind.addLabel }}</button>
        <span class="toolbar-rule" aria-hidden="true"></span>
        <button class="secondary-button compact-button" type="button" :disabled="!connectionSource || !connectionTargets.length || !!store.activeRun" @click="connectionSource && startConnection(connectionSource)">连线模式</button>
        <span v-if="connectFrom" class="connection-hint" role="status">{{ connectFromLabel() }}</span>
        <span class="toolbar-spacer"></span>
        <button class="secondary-button compact-button" type="button" :disabled="!canValidateAndSave" @click="validateAndSave">{{ store.saving ? '保存中…' : store.validating ? '校验中…' : '校验并保存' }}</button>
        <button class="secondary-button compact-button" type="button" aria-label="缩小画布" :disabled="store.zoom <= .5" @click="setZoom(store.zoom - .1)">−</button>
        <span class="zoom-value" aria-live="polite">{{ Math.round(store.zoom * 100) }}%</span>
        <button class="secondary-button compact-button" type="button" aria-label="放大画布" :disabled="store.zoom >= 1.8" @click="setZoom(store.zoom + .1)">+</button>
        <button class="secondary-button compact-button" type="button" @click="fitCanvas">适配</button>
        <button class="secondary-button compact-button" type="button" @click="store.pan = { x: 0, y: 0 }">重置视图</button>
      </div>

      <div class="workflow-main">
        <div ref="canvas" class="workflow-canvas" @wheel="onWheel">
          <div class="canvas-caption"><span>拖动空白处平移 · Ctrl/⌘ + 滚轮缩放 · 点击源节点右端口开始，再点同类型目标左端口完成连线</span><span>版本 {{ store.document?.workflow_revision ?? '—' }}</span></div>
          <div class="workflow-stage" :style="{ transform: `translate(${store.pan.x}px, ${store.pan.y}px) scale(${store.zoom})` }">
            <svg ref="stage" class="workflow-svg" :viewBox="'0 0 900 480'" width="900" height="480" role="group" aria-label="音频输入、预处理、语音识别与音频/文本输出的节点画布" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp">
              <defs><pattern id="workflow-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="oklch(91% .016 82)" stroke-width="1" /></pattern></defs>
              <rect width="900" height="480" fill="url(#workflow-grid)" />
              <path v-for="edge in store.draft.edges" :key="edge.id" class="workflow-edge-hit" :d="edgePath(edge)" @pointerdown.stop @click.stop="!controlsLocked && removeEdge(edge.id)" />
              <path v-for="edge in store.draft.edges" :key="`${edge.id}-line`" class="workflow-edge" :class="{ 'is-run': store.runMatchesDraft && store.run?.status === 'completed' }" :d="edgePath(edge)" aria-hidden="true" />
              <g v-for="node in store.draft.nodes" :key="node.id" class="workflow-node" :class="[`is-${node.type}`, `is-state-${nodeStatus(node)}`, { 'is-selected': store.selectedNodeId === node.id, 'is-invalid': !!nodeIssue(node) }]" :transform="nodeTransform(node)" role="button" tabindex="0" :aria-label="`${nodeLabel(node)}，${nodeKindLabel(node)}，${nodeStatusLabel(nodeStatus(node))}`" :aria-pressed="store.selectedNodeId === node.id ? 'true' : 'false'" @pointerdown.stop="onNodePointerDown($event, node)" @dblclick.stop="openNode(node)" @click.stop="store.selectNode(node.id)" @keydown="keyboardSelect($event, node)">
                <rect class="node-card" :width="nodeWidth" :height="nodeHeight" rx="16" />
                <rect class="node-accent" width="5" :height="nodeHeight" rx="2.5" />
                <text class="node-kicker" x="22" y="27">{{ nodeKicker(node) }}</text>
                <text class="node-name" x="22" y="52">{{ nodeLabel(node) }}</text>
                <text class="node-meta" x="22" y="72">{{ nodeMeta(node) }}</text>
                <rect :width="nodeWidth" :height="nodeHeight" fill="transparent" @pointerenter="pointerTarget = node.id" @pointerleave="pointerTarget = null" />
                <circle v-if="inputPortForNode(node.type)" class="node-port node-port-in" :class="`node-port-${inputPortForNode(node.type)}`" cx="0" :cy="nodeHeight / 2" r="7" :aria-label="`输入端口 ${inputPortForNode(node.type)}`" @pointerenter="pointerTarget = node.id" @pointerleave="pointerTarget = null" @click.stop="!controlsLocked && finishConnection(node)"><title>{{ inputPortForNode(node.type) }} 输入端口</title></circle><text v-if="inputPortForNode(node.type)" class="node-port-label" x="12" :y="nodeHeight / 2 + 3" aria-hidden="true">{{ inputPortForNode(node.type) }}</text>
                <circle v-if="outputPortForNode(node.type)" class="node-port node-port-out" :class="`node-port-${outputPortForNode(node.type)}`" :cx="nodeWidth" :cy="nodeHeight / 2" r="7" :aria-label="`输出端口 ${outputPortForNode(node.type)}`" @pointerenter="pointerTarget = node.id" @pointerleave="pointerTarget = null" @click.stop="!controlsLocked && startConnection(node)"><title>{{ outputPortForNode(node.type) }} 输出端口</title></circle><text v-if="outputPortForNode(node.type)" class="node-port-label" :x="nodeWidth - 12" :y="nodeHeight / 2 + 3" aria-hidden="true">{{ outputPortForNode(node.type) }}</text>
                <text v-if="nodeIssue(node)" class="node-error-mark" x="176" y="24" aria-hidden="true">!</text>
              </g>
            </svg>
          </div>
          <div v-if="!store.draft.nodes.length" class="workflow-empty"><strong>画布为空</strong><span>添加一个输入节点和至少一个输出节点开始。</span></div>
        </div>

        <aside class="workflow-inspector" aria-labelledby="workflow-inspector-title">
          <div class="inspector-topline"><span class="section-kicker">INSPECTOR</span><span class="workflow-dirty-state">{{ store.dirty ? '未保存' : '已同步' }}</span></div>
          <h3 id="workflow-inspector-title">{{ selected ? nodeLabel(selected) : '选择一个节点' }}</h3>
          <template v-if="selected">
            <p class="inspector-type">{{ selected.type === 'audio_input' ? '音频输入 · 只绑定当前焦点音频' : selected.type === 'preprocess' ? '音频预处理 · 每个节点生成独立产物，源文件始终保留' : selected.type === 'asr' ? '语音识别 · 输出独立 transcript.json，语言为节点独立设置' : selected.type === 'text_output' ? '文本输出 · 从直接上游 transcript 导出一种文本格式' : '音频输出 · 复制直接上游字节与媒体类型' }}</p>
            <div class="workflow-field"><span>节点 ID</span><code>{{ selected.id }}</code></div>
            <button class="primary-button inspector-edit" type="button" :disabled="controlsLocked" @click="openNode(selected)">编辑节点设置</button>
            <button v-if="selected.type !== 'audio_input'" class="text-button inspector-delete" type="button" :disabled="controlsLocked" @click="removeSelected">删除{{ selected.type === 'preprocess' ? '预处理' : selected.type === 'asr' ? 'ASR' : '输出' }}节点</button>
          <p v-if="selected.type === 'audio_input'" class="inspector-help">输入只使用当前音频 {{ props.audioName }}，不提供路径输入。</p>
            <p v-else-if="selected.type === 'preprocess'" class="inspector-help">Mock 是固定示例，只复制上游原字节以验证链路，不执行真实预处理或媒体转换。源文件始终保留，不会被删除或覆盖。</p>
            <p v-else-if="selected.type === 'asr'" class="inspector-help">Mock 是固定示例，不执行真实语音识别。语言只用于此节点的示例配置。</p>
            <p v-else-if="selected.type === 'text_output'" class="inspector-help">TXT 可无时间戳；SRT、VTT、LRC 需要完整有效时间范围。缺失时间戳只会让该文本输出失败，不影响音频分支。</p>
          </template>
          <div v-else class="inspector-empty"><span aria-hidden="true">↖</span><p>选择画布中的节点，查看设置和执行状态。</p></div>
          <dl class="workflow-inspector-meta"><div><dt>工作流版本</dt><dd>{{ store.document?.workflow_revision ?? '—' }}</dd></div><div><dt>文档修订</dt><dd>{{ store.document?.revision ?? '—' }}</dd></div><div><dt>当前音频</dt><dd>{{ props.audioName }}</dd></div></dl>
        </aside>
      </div>

      <ul v-if="store.validationIssues.length" class="workflow-issues" aria-live="polite"><li v-for="(issue, index) in store.validationIssues" :key="`${issue.code}-${index}`"><button type="button" :disabled="!issue.node_id" @click="issue.node_id && (store.selectNode(issue.node_id), openNode(store.draft.nodes.find((node) => node.id === issue.node_id)!))">{{ issue.node_id ? `节点 ${issue.node_id}` : '工作流' }}</button><span>{{ issue.message }}</span></li></ul>

      <section class="workflow-run-panel" aria-labelledby="workflow-run-title">
        <div class="workflow-run-heading"><div><span class="section-kicker">RUN &amp; HISTORY</span><h3 id="workflow-run-title">运行与产物</h3><p>任务启动时固定工作流快照；编辑不会改变已启动或历史运行。</p></div><div class="workflow-run-actions"><button v-if="store.activeRun" class="secondary-button danger-button" type="button" :disabled="store.actionLoading" @click="cancelWorkflow">{{ store.actionLoading ? '提交取消…' : '取消运行' }}</button><button v-else class="primary-button" type="button" :disabled="!canRun" @click="runWorkflow">{{ store.actionLoading ? '正在启动…' : store.dirty ? '保存后可运行' : '运行节点工作流' }}</button></div></div>
        <div class="workflow-run-summary"><span class="run-status" :class="`is-${store.run?.status || 'idle'}`">{{ runStatusLabel(store.run?.status) }}</span><span v-if="store.run">运行 {{ store.run.id }} · 版本 {{ store.run.workflow_revision }} · 创建 {{ dateLabel(store.run.created_at) }} · {{ store.runMatchesDraft ? '快照与当前画布一致' : '历史快照，不映射当前画布状态' }}</span><span v-else>尚无运行。保存后使用当前音频创建音频分支运行。</span></div>
        <div v-if="store.runLoading" class="workflow-loading small" role="status">正在读取运行详情…</div>
        <div v-else-if="store.runError" class="workflow-run-error" role="alert">{{ store.runError }}</div>
        <div v-if="store.run" class="workflow-run-nodes">
          <article v-for="node in store.run.nodes" :key="node.node_id" class="workflow-run-node">
            <div><strong>{{ node.node_id }}</strong><span class="run-node-status" :class="`is-${node.status}`">{{ nodeStatusLabel(node.status) }}</span></div>
            <p v-if="node.error">{{ node.error.message }}</p>
            <p v-else-if="node.artifact">快照产物 · {{ node.artifact.port.toUpperCase() }} · {{ node.artifact.media_type }} · {{ sizeLabel(node.artifact.size_bytes) }}</p>
            <p v-else>尚无节点产物。</p>
            <template v-if="node.node_type !== 'audio_input' && node.status === 'completed'">
              <p class="workflow-output-state" :class="`is-${outputCheckState(node.node_id)}`">{{ outputCheckState(node.node_id) === 'checking' ? '正在检查产物文件…' : outputCheckState(node.node_id) === 'missing' ? '产物缺失，不可下载。' : outputCheckState(node.node_id) === 'failed' ? '产物检查失败，可重试。' : '产物可用。' }}</p>
              <button v-if="canDownloadOutput(node.node_id)" class="secondary-button compact-button" type="button" @click="download(node.node_id)">下载 {{ node.artifact?.filename }}</button>
              <button v-else-if="outputCheckState(node.node_id) === 'failed'" class="secondary-button compact-button" type="button" @click="retryOutputChecks">重试检查</button>
            </template>
          </article>
        </div>
        <div v-if="historyOpen" class="workflow-history"><div class="workflow-history-heading"><h4>运行历史</h4><button class="text-button" type="button" :disabled="store.historyLoading" @click="store.loadHistory()">{{ store.historyLoading ? '刷新中…' : '刷新' }}</button></div><p v-if="store.historyError" class="workflow-run-error" role="alert">{{ store.historyError }}</p><div v-if="!store.history.length && !store.historyLoading" class="workflow-history-empty">历史记录会保留在此项目音频下，旧运行不会被新轮询覆盖。</div><button v-for="item in store.history" :key="item.id" class="workflow-history-row" :class="{ 'is-active': store.run?.id === item.id }" type="button" :disabled="store.runLoading || !!store.activeRun" @click="selectHistory(item.id)"><span><strong>{{ item.id }}</strong><small>{{ dateLabel(item.created_at) }} · 版本 {{ item.workflow_revision }}</small></span><span class="run-status" :class="`is-${item.status}`">{{ runStatusLabel(item.status) }}</span></button></div>
      </section>
    </template>

    <div v-if="settingsOpen && selected" class="modal-backdrop" role="presentation" @click.self="closeSettings">
      <section class="config-modal workflow-settings-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-settings-title">
        <div class="config-modal-header"><div><span class="section-kicker">{{ selected.type === 'audio_input' ? 'INPUT NODE' : selected.type === 'preprocess' ? 'PREPROCESS NODE' : selected.type === 'asr' ? 'ASR NODE' : selected.type === 'text_output' ? 'TEXT OUTPUT NODE' : 'OUTPUT NODE' }}</span><h3 id="workflow-settings-title">{{ selected.type === 'audio_input' ? '音频输入设置' : selected.type === 'preprocess' ? '音频预处理设置' : selected.type === 'asr' ? '语音识别设置' : selected.type === 'text_output' ? '文本输出设置' : '音频输出设置' }}</h3><p>只修改此节点的下次保存内容；运行中的任务使用固定快照。</p></div><button class="modal-close" type="button" aria-label="关闭节点设置" @click="closeSettings">×</button></div>
        <form class="config-modal-form" @submit.prevent="commitSettings">
          <label v-if="selected.type === 'audio_output' || selected.type === 'text_output'" class="config-field workflow-wide-field" for="workflow-filename-stem"><span>输出文件名（不含扩展名）</span><input id="workflow-filename-stem" v-model="filenameDraft" type="text" maxlength="128" :disabled="controlsLocked" :placeholder="selected.type === 'text_output' ? 'transcript' : 'audio-copy'" /><small v-if="selected.type === 'text_output'">最多 128 个字符；后端按 {{ textFormatDraft.toUpperCase() }} 格式补扩展名。这里不是本机路径。</small><small v-else>最多 128 个字符；后端生成安全文件名并按直接上游的媒体类型补扩展名。这里不是本机路径。</small></label>
          <label v-if="selected.type === 'text_output'" class="config-field workflow-wide-field" for="workflow-text-format"><span>文本格式</span><select id="workflow-text-format" v-model="textFormatDraft" :disabled="controlsLocked"><option value="txt">TXT · 纯识别原文</option><option value="srt">SRT · 字幕</option><option value="vtt">VTT · 字幕</option><option value="lrc">LRC · 歌词</option></select><small>TXT 允许片段没有时间戳；SRT、VTT、LRC 需要完整时间范围。每个文本输出节点只生成一种格式。</small></label>
          <div v-if="selected.type === 'preprocess' || selected.type === 'asr'" class="workflow-preprocess-note"><strong>Mock 固定示例</strong><p>{{ selected.type === 'preprocess' ? '只复制上游原字节以验证链路，不执行真实预处理或媒体转换。' : '不执行真实语音识别；provider 与语言仅用于示例配置。' }}</p></div>
          <p v-if="selected.type === 'audio_input'" class="inspector-help">输入节点只绑定当前焦点音频「{{ props.audioName }}」，不接受路径输入。</p>
            <div v-if="genericFields.length" class="modal-field-grid">
            <label v-for="field in genericFields" :key="field.key" class="config-field" :class="{ 'modal-wide-field': field.type === 'json' }">
              <span>{{ field.label }}<template v-if="field.required"> *</template></span>
              <select v-if="field.type === 'boolean'" :value="String(fieldValue(field))" :disabled="controlsLocked" @change="setFieldValue(field, ($event.target as HTMLSelectElement).value === 'true')"><option value="true">是</option><option value="false">否</option></select>
              <select v-else-if="field.type === 'select'" :value="String(fieldValue(field))" :disabled="controlsLocked" @change="setFieldValue(field, ($event.target as HTMLSelectElement).value)"><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></select>
              <textarea v-else-if="field.type === 'json'" :value="jsonFieldValue(field)" rows="4" :disabled="controlsLocked" @change="setJsonFieldValue(field, $event)"></textarea>
              <input v-else :value="fieldValue(field)" :type="field.type === 'integer' || field.type === 'number' ? 'number' : 'text'" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :step="field.type === 'integer' ? 1 : 'any'" :disabled="controlsLocked" @input="setFieldValue(field, ($event.target as HTMLInputElement).value)" />
              <small>字段由服务端能力目录定义。</small>
            </label>
          </div>
          <p v-if="settingsError" class="settings-feedback settings-error" role="alert">{{ settingsError }}</p>
          <div class="config-modal-footer"><span>{{ store.dirty ? '有未保存修改' : '当前节点已同步' }}</span><div><button class="secondary-button" type="button" @click="closeSettings">取消</button><button class="primary-button" type="submit" :disabled="controlsLocked">应用节点设置</button></div></div>
        </form>
      </section>
    </div>
  </section>
</template>
