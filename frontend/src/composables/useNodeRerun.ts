import { computed, onUnmounted, ref, watch } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { displayError, message, t } from '../i18n'
import { useWorkflowStore, workflowErrorLabel } from '../stores/workflow'
import { baseRunReason } from './workflowExecution'
import { randomRequestId } from './useBatchManifest'
export interface RerunIssue { code: string; message: string; node_id?: string | null; restart_node_ids: string[] }
export interface RerunPlan { valid: boolean; execute_node_ids: string[]; reuse_node_ids: string[]; skipped_node_ids: string[]; issues: RerunIssue[] }
export function useNodeRerun(options: { blocked: () => boolean; context: () => string }) {
  const store = useWorkflowStore()
  const target = ref<{ projectId: string; audioId: string; sourceRunId: string; fromNodeId: string; revision: number } | null>(null)
  const plan = ref<RerunPlan | null>(null)
  const planning = ref(false)
  const submitting = ref(false)
  const error = ref('')
  let generation = 0
  let controller: AbortController | null = null
  const requests = new Map<string, string>()
  const reason = computed(() => {
    if (options.blocked()) return t('batch.busy')
    const base = baseRunReason(store)
    if (base) return base
    if (store.batchSubmitting || store.activeRun) return t('batch.busy')
    const source = store.run
    if (!source) return t('workflow.rerunChooseSource')
    if (['pending', 'running', 'cancelling'].includes(source.status)) return t('workflow.rerunSourceActive')
    const bound = store.boundAudioIds
    if (bound !== null ? !bound.includes(source.audio_id) : (store.boundAudioId || store.audioId) !== source.audio_id) return t('workflow.rerunAudioUnbound')
    return ''
  })
  function close() { if (submitting.value) return; generation++; controller?.abort(); controller = null; target.value = null; plan.value = null; planning.value = false; error.value = '' }
  async function open(nodeId: string) {
    if (submitting.value) return
    close()
    const source = store.run
    target.value = { projectId: store.projectId || '', audioId: source?.audio_id || '', sourceRunId: source?.id || '', fromNodeId: nodeId, revision: store.document?.workflow_revision || 0 }
    if (reason.value) { error.value = reason.value; return }
    const ticket = ++generation; const context = options.context(); const request = new AbortController(); controller = request
    planning.value = true
    try {
      const selected = target.value!
      const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(selected.projectId)}/audios/${encodeURIComponent(selected.audioId)}/workflow-runs/plan`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ workflow_revision: selected.revision, rerun: { source_run_id: selected.sourceRunId, from_node_id: selected.fromNodeId } }), signal: request.signal })
      if (!response.ok) throw await parseApiError(response)
      const value = await response.json() as RerunPlan
      const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(id => typeof id === 'string')
      if (!value || typeof value.valid !== 'boolean' || !ids(value.execute_node_ids) || !ids(value.reuse_node_ids) || !ids(value.skipped_node_ids) || !Array.isArray(value.issues) || value.issues.some(issue => typeof issue.code !== 'string' || typeof issue.message !== 'string' || !ids(issue.restart_node_ids))) throw message('workflow.rerunInvalidPlan')
      if (ticket === generation && context === options.context()) plan.value = value
    } catch (failure) { if (ticket === generation && context === options.context()) error.value = displayError(failure) }
    finally { if (ticket === generation) planning.value = false }
  }
  async function submit() {
    if (!target.value || !plan.value?.valid || submitting.value || planning.value) return
    if (reason.value) { error.value = reason.value; return }
    const selected = target.value
    const signature = JSON.stringify(selected)
    let requestId = requests.get(signature)
    if (!requestId) { requestId = randomRequestId(); requests.set(signature, requestId) }
    submitting.value = true; error.value = ''
    try {
      const run = await store.startRun({ audioId: selected.audioId, sourceRunId: selected.sourceRunId, fromNodeId: selected.fromNodeId, requestId, revision: selected.revision })
      if (run) { requests.delete(signature); submitting.value = false; close() }
      else error.value = store.error || t('workflow.startFailed')
    } catch (failure) { error.value = displayError(failure) }
    finally { submitting.value = false }
  }
  watch(options.context, close)
  onUnmounted(() => { generation++; controller?.abort() })
  return { target, plan, planning, submitting, error, reason, open, close, submit, issueLabel: (issue: RerunIssue) => workflowErrorLabel(issue) }
}
