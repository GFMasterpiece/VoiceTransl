import { backendIdentity, DEFAULT_API_BASE_URL } from '../backendConnection'
import { computed, ref, shallowRef } from 'vue'
import { API_BASE_URL, parseApiError, type ApiError } from '../api'
import { displayError, hasMessage, message, t } from '../i18n'
import type { SourceOrigin } from '../stores/projects'
import { providerErrorLabel } from '../workflow/providerErrors'
import type { WorkflowSnapshot } from '../stores/workflow'
import { randomRequestId } from './useBatchManifest'

export interface BatchCounts { queued: number; running: number; completed: number; partial: number; failed: number; cancelled: number; interrupted: number }
export interface BatchSummary {
  id: string; project_id: string; status: string; workflow_revision: number; created_at: string
  started_at: string | null; finished_at: string | null; cancel_requested: boolean; retry_of: string | null
  total: number; counts: BatchCounts; error: { type: string; message: string } | null
}
export interface BatchDetail extends BatchSummary { workflow: WorkflowSnapshot }
export interface BatchItem {
  sequence?: number | null
  index: number; audio_id: string; audio_name: string; source_language: string; origin: SourceOrigin | null
  status: string; run_id: string | null; started_at: string | null; finished_at: string | null; error: { type: string; message: string } | null
}
export interface BatchPage<T> { items: T[]; total: number; limit: number; offset: number }
export const batchActive = (status: string) => ['queued', 'running', 'cancelling'].includes(status)
export const batchTerminal = (status: string) => ['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(status)
export const batchDone = (batch: BatchSummary) => batch.total - batch.counts.queued - batch.counts.running
export function batchStatus(status: string) { return hasMessage(`batch.status_${status}`) ? t(`batch.status_${status}`) : t('batch.status_unknown', { status }) }
export function batchError(reason: unknown) {
  const value = reason as { code?: string; type?: string; message?: unknown }
  const code = value?.code || value?.type
  return code && hasMessage(`batch.error_${code}`) ? t(`batch.error_${code}`)
    : value && typeof value.message === 'string' ? providerErrorLabel({ ...value, message: value.message }) || (reason instanceof Error ? displayError(reason) : value.message) : displayError(reason)
}
function readSummary(value: unknown): BatchSummary {
  const batch = value as BatchSummary
  if (!batch || typeof batch.id !== 'string' || typeof batch.project_id !== 'string' || typeof batch.status !== 'string'
    || !Number.isInteger(batch.workflow_revision) || !Number.isInteger(batch.total) || batch.total < 1
    || typeof batch.created_at !== 'string' || !batch.counts
    || !['queued', 'running', 'completed', 'partial', 'failed', 'cancelled', 'interrupted'].every((key) => Number.isInteger(batch.counts[key as keyof BatchCounts]) && batch.counts[key as keyof BatchCounts] >= 0)
    || Object.values(batch.counts).reduce((sum, count) => sum + count, 0) !== batch.total
    || !validError(batch.error)) throw message('batch.invalidResponse')
  return batch
}
function validError(value: unknown) { return value === null || (!!value && typeof (value as { type: unknown }).type === 'string' && typeof (value as { message: unknown }).message === 'string') }
function validOrigin(value: unknown) { return value === null || (!!value && ['root_id', 'root_name', 'relative_path'].every((key) => typeof (value as Record<string, unknown>)[key] === 'string')) }
function readItem(value: unknown): BatchItem {
  const item = value as BatchItem
  if (!item || !Number.isInteger(item.index) || item.index < 0 || typeof item.audio_id !== 'string' || typeof item.audio_name !== 'string'
    || (item.sequence != null && (!Number.isInteger(item.sequence) || item.sequence < 1))
    || typeof item.source_language !== 'string' || typeof item.status !== 'string' || !validOrigin(item.origin) || !validError(item.error)
    || (item.run_id !== null && typeof item.run_id !== 'string')) throw message('batch.invalidResponse')
  return item
}
function readPage<T>(value: unknown, reader: (row: unknown) => T): BatchPage<T> {
  const page = value as BatchPage<unknown>
  if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0
    || !Number.isInteger(page.limit) || page.limit < 1 || !Number.isInteger(page.offset) || page.offset < 0) throw message('batch.invalidResponse')
  return { ...page, items: page.items.map(reader) }
}
interface PendingRequest { path: string; body: Record<string, unknown> }
function validPending(value: unknown): value is PendingRequest {
  const request = value as PendingRequest
  if (!request || typeof request.path !== 'string' || !request.body || typeof request.body.request_id !== 'string'
    || !request.body.request_id.length || request.body.request_id.length > 128) return false
  if (request.path === '') return Number.isInteger(request.body.workflow_revision) && Number(request.body.workflow_revision) >= 0
    && Array.isArray(request.body.audio_ids) && request.body.audio_ids.length > 0
    && request.body.audio_ids.every((id) => typeof id === 'string' && !!id) && new Set(request.body.audio_ids).size === request.body.audio_ids.length
  return /^\/[A-Za-z0-9_-]+\/retry$/.test(request.path) && typeof request.body.include_cancelled === 'boolean'
}

export function batchPendingStorageKey(projectId: string, apiBase = API_BASE_URL) { return `voicetransl.batch.pending:${encodeURIComponent(backendIdentity(apiBase))}:${encodeURIComponent(projectId)}` }
export function restoreBatchPending(projectId: string, apiBase = API_BASE_URL): PendingRequest | null {
  try {
    const key = batchPendingStorageKey(projectId, apiBase)
    const scoped = sessionStorage.getItem(key)
    if (scoped !== null) { const value: unknown = JSON.parse(scoped); return validPending(value) ? value : null }
    if (backendIdentity(apiBase) !== backendIdentity(DEFAULT_API_BASE_URL)) return null
    const legacyKey = `voicetransl.batch.pending:${projectId}`
    const legacy: unknown = JSON.parse(sessionStorage.getItem(legacyKey) || 'null')
    if (!validPending(legacy)) return null
    try { sessionStorage.setItem(key, JSON.stringify(legacy)); sessionStorage.removeItem(legacyKey) } catch { /* Keep the old request available if migration cannot be persisted. */ }
    return legacy
  } catch { return null }
}

export function useBatches(projectId: string) {
  const history = shallowRef<BatchPage<BatchSummary>>({ items: [], total: 0, limit: 20, offset: 0 })
  const detail = shallowRef<BatchDetail | null>(null)
  const items = shallowRef<BatchPage<BatchItem>>({ items: [], total: 0, limit: 20, offset: 0 })
  const loading = ref(false)
  const detailLoading = ref(false)
  const submitting = ref(false)
  const errorSource = shallowRef<unknown>('')
  const error = computed(() => batchError(errorSource.value))
  const storageKey = batchPendingStorageKey(projectId)
  const pending = shallowRef<PendingRequest | null>(restoreBatchPending(projectId))
  const pendingStored = ref(true)
  function setPending(value: PendingRequest | null) {
    pending.value = value
    try {
      if (value) sessionStorage.setItem(storageKey, JSON.stringify(value)); else {
        sessionStorage.removeItem(storageKey)
        if (backendIdentity(API_BASE_URL) === backendIdentity(DEFAULT_API_BASE_URL)) sessionStorage.removeItem(`voicetransl.batch.pending:${projectId}`)
      }
      pendingStored.value = true
    } catch { pendingStored.value = false }
  }
  const active = ref(false)
  let visible = false
  let alive = true
  let historySequence = 0
  let detailSequence = 0
  let activitySequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const controllers = new Set<AbortController>()
  const base = `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/batches`

  async function request(path: string, options?: RequestInit) {
    const controller = new AbortController(); controllers.add(controller)
    try {
      const response = await fetch(`${base}${path}`, { ...options, signal: controller.signal })
      if (!response.ok) throw await parseApiError(response)
      return await response.json()
    } finally { controllers.delete(controller) }
  }
  function schedule() {
    if (timer) clearTimeout(timer)
    timer = undefined
    if (alive && (active.value || (visible && detail.value && batchActive(detail.value.status)))) {
      timer = setTimeout(() => { void (visible ? refresh() : refreshActivity()) }, visible ? 2000 : 5000)
    }
  }
  async function loadHistory(offset = history.value.offset) {
    if (!alive) return
    const sequence = ++historySequence
    const activityToken = offset === 0 ? ++activitySequence : activitySequence
    loading.value = true
    try {
      const page = readPage(await request(`?limit=${history.value.limit}&offset=${offset}`), readSummary)
      if (page.items.some((batch) => batch.project_id !== projectId)) throw message('batch.invalidResponse')
      if (!alive || sequence !== historySequence) return
      history.value = page
      // The newest page contains any active batch because the server permits one per project.
      if (offset === 0 && activityToken === activitySequence) active.value = page.items.some((batch) => batchActive(batch.status))
    } catch (reason) { if (alive && sequence === historySequence) errorSource.value = reason }
    finally { if (alive && sequence === historySequence) loading.value = false }
  }
  async function select(id: string, offset = 0) {
    if (!alive) return
    const sequence = ++detailSequence
    detailLoading.value = true
    try {
      const [rawDetail, rawItems] = await Promise.all([
        request(`/${encodeURIComponent(id)}`), request(`/${encodeURIComponent(id)}/items?limit=${items.value.limit}&offset=${offset}`),
      ])
      const batch = readSummary(rawDetail) as BatchDetail
      if (batch.id !== id || batch.project_id !== projectId || !batch.workflow || !Array.isArray(batch.workflow.nodes) || !Array.isArray(batch.workflow.edges)) throw message('batch.invalidResponse')
      const page = readPage(rawItems, readItem)
      if (!alive || sequence !== detailSequence) return
      detail.value = batch; items.value = page
    } catch (reason) { if (alive && sequence === detailSequence) errorSource.value = reason }
    finally { if (alive && sequence === detailSequence) detailLoading.value = false }
  }
  async function refresh() {
    if (!alive) return
    errorSource.value = ''
    await Promise.all([loadHistory(), detail.value ? select(detail.value.id, items.value.offset) : Promise.resolve(),
      history.value.offset > 0 ? refreshActivity() : Promise.resolve()])
    schedule()
  }
  async function refreshActivity() {
    const sequence = ++activitySequence
    try {
      const page = readPage(await request('?limit=1&offset=0'), readSummary)
      if (page.items.some((batch) => batch.project_id !== projectId)) throw message('batch.invalidResponse')
      if (alive && sequence === activitySequence) active.value = page.items.some((batch) => batchActive(batch.status))
    } catch (reason) { if (alive && sequence === activitySequence) errorSource.value = reason }
    finally { schedule() }
  }
  async function mutate(path: string, body?: Record<string, unknown>, idempotent = false) {
    if (!alive || submitting.value) return null
    if (idempotent && pending.value && (pending.value.path !== path || JSON.stringify(pending.value.body) !== JSON.stringify(body))) {
      errorSource.value = message('batch.requestMismatch'); return null
    }
    if (idempotent) setPending({ path, body: body! })
    ++activitySequence
    submitting.value = true; errorSource.value = ''
    let result: BatchSummary | null = null
    try {
      result = readSummary(await request(path, { method: 'POST', ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) }))
      if (result.project_id !== projectId) { result = null; throw message('batch.invalidResponse') }
      if (!alive) return null
      if (idempotent) setPending(null)
      ++activitySequence
      active.value = batchActive(result.status)
    } catch (reason) {
      if (alive) {
        errorSource.value = reason
        // Only an explicit client rejection proves the request did not create a batch.
        const status = (reason as ApiError)?.status
        if (idempotent && status >= 400 && status < 500) setPending(null)
      }
    } finally { if (alive) submitting.value = false }
    if (result && alive) { await loadHistory(0); await select(result.id); schedule() }
    return result
  }
  function create(revision: number, audioIds: string[]) {
    if (pending.value?.path === '' && pending.value.body.workflow_revision === revision && JSON.stringify(pending.value.body.audio_ids) === JSON.stringify(audioIds)) return retryPending()
    const body = { workflow_revision: revision, audio_ids: [...audioIds], request_id: randomRequestId() }
    return mutate('', body, true)
  }
  function retry(batchId: string, includeCancelled: boolean) {
    if (pending.value?.path === `/${encodeURIComponent(batchId)}/retry` && pending.value.body.include_cancelled === includeCancelled) return retryPending()
    return mutate(`/${encodeURIComponent(batchId)}/retry`, { request_id: randomRequestId(), include_cancelled: includeCancelled }, true)
  }
  function retryPending() { return pending.value ? mutate(pending.value.path, pending.value.body, true) : Promise.resolve(null) }
  function cancel(batchId: string) { return mutate(`/${encodeURIComponent(batchId)}/cancel`) }
  function download(batchId: string) { return `${base}/${encodeURIComponent(batchId)}/download` }
  function setVisible(value: boolean) { visible = value; if (timer) clearTimeout(timer); if (value) void refresh(); else schedule() }
  function dispose() { alive = false; visible = false; if (timer) clearTimeout(timer); for (const controller of controllers) controller.abort(); controllers.clear(); historySequence++; detailSequence++ }
  return { history, detail, items, loading, detailLoading, submitting, error, pending, pendingStored, active, loadHistory, select, refresh, refreshActivity, create, retry, retryPending, cancel, download, setVisible, dispose }
}
export type Batches = ReturnType<typeof useBatches>
