<script setup lang="ts">
import { computed } from 'vue'
import { hasMessage, locale, message, t } from '../i18n'
import { resolveArtifactUrl, useWorkflowStore, workflowErrorLabel, type WorkflowNodeRun } from '../stores/workflow'
import { runRecords } from '../workflow/runRecords'
import CanvasDialog from './CanvasDialog.vue'

const props = defineProps<{ panel: 'run' | 'artifacts' | 'logs' | null }>()
const emit = defineEmits<{ close: [] }>()
const store = useWorkflowStore()
const title = computed(() => t(`workflow.${props.panel === 'artifacts' ? 'panelArtifacts' : props.panel === 'logs' ? 'panelLogs' : 'panelRun'}`))
const controlsLocked = computed(() => store.saving || store.validating || !!store.activeRun || store.actionLoading || store.resourceUploading)
const canRun = computed(() => !store.dirty && !controlsLocked.value && !store.loading && !!store.document && !!(store.boundAudioId || store.audioId))
const artifacts = computed(() => store.run?.nodes.filter((node) => node.artifact) || [])
const records = computed(() => store.run ? runRecords(store.run) : [])

function statusLabel(status: string | undefined, node = false) {
  if (!status) return t('workflow.notRun')
  const key = `workflow.${node ? 'nodeStatus' : 'runStatus'}_${status}`
  return hasMessage(key) ? t(key) : status
}
function numberLabel(value: number) { return new Intl.NumberFormat(locale.value).format(value) }
function dateLabel(value: string | null | undefined) {
  if (!value || Number.isNaN(Date.parse(value))) return t('workflow.notRecorded')
  return new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'medium', hour12: false }).format(new Date(value))
}
function sizeLabel(bytes: number) {
  const megabytes = bytes >= 1024 * 1024
  const value = megabytes ? bytes / 1024 / 1024 : Math.max(1, Math.round(bytes / 1024))
  return `${new Intl.NumberFormat(locale.value, { maximumFractionDigits: megabytes ? 1 : 0 }).format(value)} ${megabytes ? 'MB' : 'KB'}`
}
const summary = computed(() => store.run ? t('workflow.runSummary', {
  id: store.run.id, version: numberLabel(store.run.workflow_revision), date: dateLabel(store.run.created_at),
  snapshot: store.runMatchesDraft ? t('workflow.snapshotMatches') : t('workflow.snapshotHistorical'),
}) : t('workflow.noRuns'))
async function startRun() {
  if (store.dirty) { store.setError(message('workflow.saveFirst')); return }
  if (!canRun.value) return
  try { await store.startRun() } catch { /* The store retains the actionable error. */ }
}
async function cancelRun() {
  try { await store.cancelRun() } catch { /* The store retains the actionable error. */ }
}
function outputState(nodeId: string) { return store.outputChecks[nodeId]?.state || 'checking' }
function offersDownload(node: WorkflowNodeRun) {
  const definition = store.registry?.kind(node.node_type)
  return !definition?.isSource && !definition?.preview && !node.preview && node.status === 'completed'
}
function canDownload(node: WorkflowNodeRun) {
  const url = store.outputs[node.node_id]?.download_url
  return offersDownload(node) && outputState(node.node_id) === 'available' && !!url && !!resolveArtifactUrl(url)
}
function artifactStateLabel(nodeId: string) {
  const state = outputState(nodeId)
  return t(`workflow.${state === 'checking' ? 'artifactChecking' : state === 'missing' ? 'artifactMissing' : state === 'failed' ? 'artifactCheckFailed' : 'artifactAvailable'}`)
}
async function retryOutputs() {
  const run = store.run
  if (run) await store.loadOutputs(run.project_id, run.audio_id, run.id)
}
</script>

<template>
  <CanvasDialog :open="!!panel" :title="title" @close="emit('close')">
    <div class="workflow-panel-content" :data-panel="panel">
      <template v-if="panel === 'run'">
        <section class="workflow-run-panel">
          <div class="workflow-run-heading">
            <div><span class="section-kicker">{{ t('workflow.runKicker') }}</span><h3>{{ t('workflow.runTitle') }}</h3><p>{{ t('workflow.snapshotHelp') }}</p></div>
            <div class="workflow-run-actions">
              <button v-if="store.activeRun" class="secondary-button danger-button" type="button" :disabled="store.actionLoading" @click="cancelRun">{{ store.actionLoading ? t('workflow.cancelling') : t('workflow.cancelRun') }}</button>
              <button v-else class="primary-button" type="button" :disabled="!canRun" @click="startRun">{{ store.actionLoading ? t('workflow.starting') : store.dirty ? t('workflow.saveToRun') : t('workflow.runWorkflow') }}</button>
            </div>
          </div>
          <p v-if="store.dirty" class="inspector-help">{{ t('workflow.saveFirst') }}</p>
          <p v-if="store.error" class="workflow-run-error" role="alert">{{ store.error }}</p>
          <div class="workflow-run-summary"><span class="run-status" :class="`is-${store.run?.status || 'idle'}`">{{ statusLabel(store.run?.status) }}</span><span>{{ summary }}</span></div>
          <div v-if="store.runLoading" class="workflow-loading small" role="status">{{ t('workflow.loadingRun') }}</div>
          <p v-if="store.runError" class="workflow-run-error" role="alert">{{ store.runError }}</p>
          <p v-if="store.run?.error" class="workflow-run-error" role="alert">{{ workflowErrorLabel(store.run.error) }}</p>
          <div v-if="store.run" class="workflow-run-nodes">
            <article v-for="node in store.run.nodes" :key="node.node_id" class="workflow-run-node">
              <div><strong>{{ node.node_id }}</strong><span class="run-node-status" :class="`is-${node.status}`">{{ statusLabel(node.status, true) }}</span></div>
              <p v-if="node.error">{{ workflowErrorLabel(node.error) }}</p>
            </article>
          </div>
          <div class="workflow-history">
            <div class="workflow-history-heading"><h4>{{ t('workflow.history') }}</h4><button class="text-button" type="button" :disabled="store.historyLoading" @click="store.loadHistory()">{{ store.historyLoading ? t('workflow.refreshing') : t('workflow.refresh') }}</button></div>
            <p v-if="store.historyError" class="workflow-run-error" role="alert">{{ store.historyError }}</p>
            <div v-if="!store.history.length && !store.historyLoading" class="workflow-history-empty">{{ t('workflow.historyEmpty') }}</div>
            <button v-for="item in store.history" :key="item.id" class="workflow-history-row" :class="{ 'is-active': store.run?.id === item.id }" type="button" :disabled="store.runLoading || !!store.activeRun" @click="store.loadRun(item.id)"><span><strong>{{ item.id }}</strong><small>{{ dateLabel(item.created_at) }} · {{ t('workflow.version', { version: numberLabel(item.workflow_revision) }) }}</small></span><span class="run-status" :class="`is-${item.status}`">{{ statusLabel(item.status) }}</span></button>
          </div>
        </section>
      </template>

      <template v-else-if="panel === 'artifacts'">
        <p class="inspector-help">{{ t('workflow.panelArtifactsHelp') }}</p>
        <p class="workflow-panel-meta">{{ summary }}</p>
        <p v-if="store.error" class="workflow-run-error" role="alert">{{ store.error }}</p>
        <p v-if="!artifacts.length" class="workflow-panel-empty">{{ store.run ? t('workflow.panelArtifactEmpty') : t('workflow.noRuns') }}</p>
        <div class="workflow-run-nodes">
          <article v-for="node in artifacts" :key="node.node_id" class="workflow-run-node">
            <div><strong>{{ node.node_id }}</strong><span class="run-node-status" :class="`is-${node.status}`">{{ statusLabel(node.status, true) }}</span></div>
            <p>{{ node.artifact!.filename }}</p>
            <p>{{ t('workflow.snapshotArtifact', { port: node.artifact!.port.toUpperCase(), type: node.artifact!.media_type, size: sizeLabel(node.artifact!.size_bytes) }) }}</p>
            <p v-if="node.error">{{ workflowErrorLabel(node.error) }}</p>
            <template v-if="offersDownload(node)">
              <p class="workflow-output-state" :class="`is-${outputState(node.node_id)}`">{{ artifactStateLabel(node.node_id) }}</p>
              <button v-if="canDownload(node)" class="secondary-button compact-button" type="button" @click="store.downloadArtifact(node.node_id)">{{ t('workflow.download', { filename: store.outputs[node.node_id]?.filename || node.artifact!.filename }) }}</button>
              <button v-else-if="outputState(node.node_id) === 'failed'" class="secondary-button compact-button" type="button" @click="retryOutputs">{{ t('workflow.retryChecks') }}</button>
              <p v-else-if="outputState(node.node_id) === 'available'" class="inspector-help">{{ t('workflow.panelNoDownload') }}</p>
            </template>
            <p v-else class="inspector-help">{{ t('workflow.panelNoDownload') }}</p>
          </article>
        </div>
      </template>

      <template v-else-if="panel === 'logs'">
        <p class="inspector-help">{{ t('workflow.panelLogsHelp') }}</p>
        <p class="workflow-panel-meta">{{ summary }}</p>
        <p v-if="!store.run" class="workflow-panel-empty">{{ t('workflow.noRuns') }}</p>
        <ol v-else class="workflow-records">
          <li v-for="record in records" :key="record.id" class="workflow-record" :data-record="record.id">
            <time class="workflow-record-time" :datetime="record.timestamp || undefined">{{ dateLabel(record.timestamp) }}</time>
            <div><strong>{{ record.nodeId || t('workflow.panelRunScope') }}</strong><span>{{ t(`workflow.${record.labelKey}`) }}<template v-if="record.status"> · {{ statusLabel(record.status, !!record.nodeId) }}</template></span><p v-if="record.error" class="workflow-record-error">{{ workflowErrorLabel(record.error) }}</p></div>
          </li>
        </ol>
      </template>
    </div>
  </CanvasDialog>
</template>
