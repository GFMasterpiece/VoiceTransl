<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  WORKFLOW_NODE_HEIGHT,
  WORKFLOW_NODE_WIDTH,
  useWorkflowStore,
  nodeLabel,
  type WorkflowNode,
} from '../stores/workflow'

const props = defineProps<{ projectId: string; audioId: string; audioName: string }>()
const emit = defineEmits<{ 'show-pipeline': [] }>()
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
const settingsError = ref('')
const selected = computed(() => store.selectedNode)
const panels = computed(() => store.draft.nodes.filter((node) => node.type === 'audio_output'))
const currentInput = computed(() => store.draft.nodes.find((node) => node.type === 'audio_input'))
const canSave = computed(() => store.dirty && !store.saving && !store.loading)
const canValidateAndSave = computed(() => canSave.value && !store.validating)
const canRun = computed(() => !store.dirty && !store.saving && !!store.document && !store.activeRun && !store.actionLoading)

const nodeWidth = WORKFLOW_NODE_WIDTH
const nodeHeight = WORKFLOW_NODE_HEIGHT
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
  if (store.activeRun) return
  store.selectNode(node.id)
  filenameDraft.value = typeof node.config.filename_stem === 'string' ? node.config.filename_stem : 'audio-copy'
  settingsError.value = ''
  settingsOpen.value = true
  void nextTick(() => document.getElementById('workflow-filename-stem')?.focus())
}

function closeSettings() {
  if (store.saving) return
  settingsOpen.value = false
}

function commitSettings() {
  if (!selected.value || store.saving) return
  if (selected.value.type === 'audio_output') {
    const stem = filenameDraft.value.trim()
    if (!stem || stem === '.' || stem === '..' || /[\\/\u0000-\u001f]/.test(stem)) {
      settingsError.value = '输出文件名不能为空，且不能包含路径分隔符或控制字符。'
      return
    }
    store.updateNode(selected.value.id, { config: { filename_stem: stem } })
  }
  settingsOpen.value = false
}

function removeSelected() {
  if (!selected.value || selected.value.type === 'audio_input') return
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
  if ((event.target as Element).closest('.workflow-node') || event.button !== 0) return
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
  const horizontalPadding = 48
  const verticalPadding = 72
  const candidate = Math.min(
    1.2,
    (bounds.width - horizontalPadding) / width,
    (bounds.height - verticalPadding) / height,
  )
  store.zoom = Math.max(.5, Math.floor(candidate * 100) / 100)
  store.pan = {
    x: (bounds.width - width * store.zoom) / 2 - minX * store.zoom,
    y: (bounds.height - height * store.zoom) / 2 - minY * store.zoom,
  }
}

function startConnection(node: WorkflowNode) {
  if (node.type !== 'audio_input' || store.activeRun) return
  connectFrom.value = node.id
}

function finishConnection(node: WorkflowNode) {
  if (!connectFrom.value || node.type !== 'audio_output') return
  store.connect(connectFrom.value, node.id)
  connectFrom.value = null
}

function removeEdge(edgeId: string) {
  if (store.activeRun) return
  store.disconnect(edgeId)
}

async function validateAndSave() {
  settingsOpen.value = false
  try {
    const issues = await store.validate()
    if (issues.length || store.error) return
    await store.save()
  } catch {
    // Keep the current draft and node context; the store exposes the API error.
  }
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

function closeEditor() {
  requestClose(() => emit('show-pipeline'))
}

function requestClose(action: () => void) {
  if (store.activeRun) {
    store.error = '节点运行尚未结束，请等待完成或取消后再离开。'
    return
  }
  if (store.saving || store.validating) {
    store.error = '节点工作流正在校验或保存，请完成后再离开。'
    return
  }
  if (store.dirty) {
    pendingClose = action
    discardPrompt.value = true
    return
  }
  action()
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
  if (event.key === 'Delete' && node.type === 'audio_output') {
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
  return node ? `从「${nodeLabel(node)}」选择输出节点完成连线` : ''
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
        <h2 id="node-workflow-title">节点工作流 <span class="mvp-badge">首版</span></h2>
        <p>当前音频直通复制到独立输出文件。仅开放音频输入与音频输出；ASR、对齐、翻译和视频节点尚未迁入，不会把未实现节点伪装成可运行。</p>
      </div>
      <div class="node-workflow-actions">
        <button class="secondary-button" type="button" :disabled="!!store.activeRun" @click="closeEditor">返回五阶段工作台</button>
        <button class="secondary-button" type="button" @click="historyOpen = !historyOpen">{{ historyOpen ? '收起历史' : `运行历史 · ${store.history.length}` }}</button>
      </div>
    </header>

    <div v-if="store.error" class="alert node-workflow-alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ store.error }}</span><button type="button" aria-label="关闭节点工作流提示" @click="store.error = ''">×</button></div>
    <div v-if="store.notice" class="workflow-notice" role="status">{{ store.notice }}</div>
        <div v-if="discardPrompt" class="workflow-discard" role="alertdialog" aria-labelledby="discard-title"><div><strong id="discard-title">放弃未保存的节点工作流修改？</strong><p>离开节点工作流后，画布和节点设置修改将丢失。已启动的任务不受影响。</p></div><div><button class="secondary-button" type="button" @click="discardPrompt = false">继续编辑</button><button class="primary-button" type="button" @click="discardAndClose">放弃修改</button></div></div>

    <div v-if="store.loading" class="workflow-loading" role="status">正在读取项目节点工作流…</div>
    <template v-else>
      <div class="workflow-mode-note"><span class="mode-dot" aria-hidden="true"></span><strong>直通模式</strong><span>输入节点只绑定当前焦点音频：{{ props.audioName }}。输出文件由后端复制生成，前端不构造本地文件或下载地址。</span></div>
      <div class="workflow-toolbar" role="toolbar" aria-label="节点工作流工具">
        <button class="secondary-button compact-button" type="button" :disabled="store.saving || !!store.activeRun || !!currentInput" @click="store.addNode('audio_input')">＋ 输入节点</button>
        <button class="secondary-button compact-button" type="button" :disabled="store.saving || !!store.activeRun" @click="store.addNode('audio_output')">＋ 输出节点</button>
        <span class="toolbar-rule" aria-hidden="true"></span>
        <button class="secondary-button compact-button" type="button" :disabled="!currentInput || !panels.length" @click="startConnection(currentInput!)">连线模式</button>
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
          <div class="canvas-caption"><span>拖动空白处平移 · Ctrl/⌘ + 滚轮缩放 · 选择节点后设置</span><span>版本 {{ store.document?.workflow_revision ?? '—' }}</span></div>
          <div class="workflow-stage" :style="{ transform: `translate(${store.pan.x}px, ${store.pan.y}px) scale(${store.zoom})` }">
            <svg ref="stage" class="workflow-svg" :viewBox="'0 0 900 480'" width="900" height="480" role="group" aria-label="音频输入到音频输出的节点画布" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp">
              <defs><pattern id="workflow-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M 24 0 L 0 0 0 24" fill="none" stroke="oklch(91% .016 82)" stroke-width="1" /></pattern></defs>
              <rect width="900" height="480" fill="url(#workflow-grid)" />
              <path v-for="edge in store.draft.edges" :key="edge.id" class="workflow-edge-hit" :d="edgePath(edge)" @pointerdown.stop @click.stop="!store.saving && removeEdge(edge.id)" />
              <path v-for="edge in store.draft.edges" :key="`${edge.id}-line`" class="workflow-edge" :class="{ 'is-run': store.runMatchesDraft && store.run?.status === 'completed' }" :d="edgePath(edge)" aria-hidden="true" />
              <g v-for="node in store.draft.nodes" :key="node.id" class="workflow-node" :class="[`is-${node.type}`, `is-state-${nodeStatus(node)}`, { 'is-selected': store.selectedNodeId === node.id, 'is-invalid': !!nodeIssue(node) }]" :transform="nodeTransform(node)" role="button" tabindex="0" :aria-label="`${nodeLabel(node)}，${node.type === 'audio_input' ? '音频输入' : '音频输出'}，${nodeStatusLabel(nodeStatus(node))}`" :aria-pressed="store.selectedNodeId === node.id ? 'true' : 'false'" @pointerdown.stop="onNodePointerDown($event, node)" @dblclick.stop="openNode(node)" @click.stop="store.selectNode(node.id)" @keydown="keyboardSelect($event, node)">
                <rect class="node-card" :width="nodeWidth" :height="nodeHeight" rx="16" />
                <rect class="node-accent" width="5" :height="nodeHeight" rx="2.5" />
                <text class="node-kicker" x="22" y="27">{{ node.type === 'audio_input' ? 'AUDIO INPUT' : 'AUDIO OUTPUT' }}</text>
                <text class="node-name" x="22" y="52">{{ nodeLabel(node) }}</text>
                <text class="node-meta" x="22" y="72">{{ node.type === 'audio_input' ? '绑定当前音频' : `文件名 · ${node.config.filename_stem || 'audio-copy'}` }}</text>
                <rect :width="nodeWidth" :height="nodeHeight" fill="transparent" @pointerenter="pointerTarget = node.id" @pointerleave="pointerTarget = null" />
                <circle class="node-port" cx="0" :cy="nodeHeight / 2" r="7" aria-hidden="true" />
                <circle class="node-port" :cx="nodeWidth" :cy="nodeHeight / 2" r="7" aria-hidden="true" @pointerenter="pointerTarget = node.id" @pointerleave="pointerTarget = null" @click.stop="!store.saving && finishConnection(node)" />
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
            <p class="inspector-type">{{ selected.type === 'audio_input' ? '音频输入 · 只绑定当前焦点音频' : '音频输出 · 由后端复制生成' }}</p>
            <div class="workflow-field"><span>节点 ID</span><code>{{ selected.id }}</code></div>
            <button class="primary-button inspector-edit" type="button" :disabled="store.saving || !!store.activeRun" @click="openNode(selected)">编辑节点设置</button>
            <button v-if="selected.type === 'audio_output'" class="text-button inspector-delete" type="button" :disabled="store.saving || !!store.activeRun" @click="removeSelected">删除输出节点</button>
            <p v-if="selected.type === 'audio_input'" class="inspector-help">输入只使用当前音频 {{ props.audioName }}，不提供路径输入。</p>
          </template>
          <div v-else class="inspector-empty"><span aria-hidden="true">↖</span><p>选择画布中的输入或输出节点，查看设置和执行状态。</p></div>
          <dl class="workflow-inspector-meta"><div><dt>工作流版本</dt><dd>{{ store.document?.workflow_revision ?? '—' }}</dd></div><div><dt>文档修订</dt><dd>{{ store.document?.revision ?? '—' }}</dd></div><div><dt>当前音频</dt><dd>{{ props.audioName }}</dd></div></dl>
        </aside>
      </div>

      <ul v-if="store.validationIssues.length" class="workflow-issues" aria-live="polite"><li v-for="(issue, index) in store.validationIssues" :key="`${issue.code}-${index}`"><button type="button" :disabled="!issue.node_id" @click="issue.node_id && (store.selectNode(issue.node_id), openNode(store.draft.nodes.find((node) => node.id === issue.node_id)!))">{{ issue.node_id ? `节点 ${issue.node_id}` : '工作流' }}</button><span>{{ issue.message }}</span></li></ul>

      <section class="workflow-run-panel" aria-labelledby="workflow-run-title">
        <div class="workflow-run-heading"><div><span class="section-kicker">RUN &amp; HISTORY</span><h3 id="workflow-run-title">运行与产物</h3><p>任务启动时固定工作流快照；编辑不会改变已启动或历史运行。</p></div><div class="workflow-run-actions"><button v-if="store.activeRun" class="secondary-button danger-button" type="button" :disabled="store.actionLoading" @click="cancelWorkflow">{{ store.actionLoading ? '提交取消…' : '取消运行' }}</button><button v-else class="primary-button" type="button" :disabled="!canRun" @click="runWorkflow">{{ store.actionLoading ? '正在启动…' : store.dirty ? '保存后可运行' : '运行节点工作流' }}</button></div></div>
        <div class="workflow-run-summary"><span class="run-status" :class="`is-${store.run?.status || 'idle'}`">{{ runStatusLabel(store.run?.status) }}</span><span v-if="store.run">运行 {{ store.run.id }} · 版本 {{ store.run.workflow_revision }} · 创建 {{ dateLabel(store.run.created_at) }} · {{ store.runMatchesDraft ? '快照与当前画布一致' : '历史快照，不映射当前画布状态' }}</span><span v-else>尚无运行。保存后使用当前音频创建一个直通运行。</span></div>
        <div v-if="store.runLoading" class="workflow-loading small" role="status">正在读取运行详情…</div>
        <div v-else-if="store.runError" class="workflow-run-error" role="alert">{{ store.runError }}</div>
        <div v-if="store.run" class="workflow-run-nodes">
          <article v-for="node in store.run.nodes" :key="node.node_id" class="workflow-run-node">
            <div><strong>{{ node.node_id }}</strong><span class="run-node-status" :class="`is-${node.status}`">{{ nodeStatusLabel(node.status) }}</span></div>
            <p v-if="node.error">{{ node.error.message }}</p>
            <p v-else-if="node.artifact">快照产物 · {{ sizeLabel(node.artifact.size_bytes) }}</p>
            <p v-else>尚无节点产物。</p>
            <template v-if="node.node_type === 'audio_output' && node.status === 'completed'">
              <p class="workflow-output-state" :class="`is-${outputCheckState(node.node_id)}`">{{ outputCheckState(node.node_id) === 'checking' ? '正在检查产物文件…' : outputCheckState(node.node_id) === 'missing' ? '产物缺失，不可下载。' : outputCheckState(node.node_id) === 'failed' ? '产物检查失败，可重试。' : '产物可用。' }}</p>
              <button v-if="canDownloadOutput(node.node_id)" class="secondary-button compact-button" type="button" @click="download(node.node_id)">下载 {{ node.artifact?.filename }}</button>
              <button v-else-if="outputCheckState(node.node_id) === 'failed'" class="secondary-button compact-button" type="button" @click="retryOutputChecks">重试检查</button>
            </template>
          </article>
        </div>
        <div v-if="historyOpen" class="workflow-history"><div class="workflow-history-heading"><h4>运行历史</h4><button class="text-button" type="button" :disabled="store.historyLoading" @click="store.loadHistory()">{{ store.historyLoading ? '刷新中…' : '刷新' }}</button></div><p v-if="store.historyError" class="workflow-run-error" role="alert">{{ store.historyError }}</p><div v-if="!store.history.length && !store.historyLoading" class="workflow-history-empty">历史记录会保留在此项目音频下，旧运行不会被新轮询覆盖。</div><button v-for="item in store.history" :key="item.id" class="workflow-history-row" :class="{ 'is-active': store.run?.id === item.id }" type="button" :disabled="!!store.activeRun && store.run?.id !== item.id" @click="selectHistory(item.id)"><span><strong>{{ item.id }}</strong><small>{{ dateLabel(item.created_at) }} · 版本 {{ item.workflow_revision }}</small></span><span class="run-status" :class="`is-${item.status}`">{{ runStatusLabel(item.status) }}</span></button></div>
      </section>
    </template>

    <div v-if="settingsOpen && selected" class="modal-backdrop" role="presentation" @click.self="closeSettings">
      <section class="config-modal workflow-settings-modal" role="dialog" aria-modal="true" aria-labelledby="workflow-settings-title">
        <div class="config-modal-header"><div><span class="section-kicker">{{ selected.type === 'audio_input' ? 'INPUT NODE' : 'OUTPUT NODE' }}</span><h3 id="workflow-settings-title">{{ selected.type === 'audio_input' ? '音频输入设置' : '音频输出设置' }}</h3><p>只修改此节点的下次保存内容；运行中的任务使用固定快照。</p></div><button class="modal-close" type="button" aria-label="关闭节点设置" @click="closeSettings">×</button></div>
        <form class="config-modal-form" @submit.prevent="commitSettings">
          <label v-if="selected.type === 'audio_output'" class="config-field workflow-wide-field" for="workflow-filename-stem"><span>输出文件名（不含扩展名）</span><input id="workflow-filename-stem" v-model="filenameDraft" type="text" maxlength="120" :disabled="store.saving || !!store.activeRun" placeholder="audio-copy" /><small>后端生成安全文件名并补上源音频扩展名；这里不是本机路径。</small></label>
          <p v-if="selected.type === 'audio_input'" class="inspector-help">输入节点只绑定当前焦点音频「{{ props.audioName }}」，不接受路径输入。</p>
          <p v-if="settingsError" class="settings-feedback settings-error" role="alert">{{ settingsError }}</p>
          <div class="config-modal-footer"><span>{{ store.dirty ? '有未保存修改' : '当前节点已同步' }}</span><div><button class="secondary-button" type="button" @click="closeSettings">取消</button><button class="primary-button" type="submit" :disabled="store.saving || !!store.activeRun">应用节点设置</button></div></div>
        </form>
      </section>
    </div>
  </section>
</template>
