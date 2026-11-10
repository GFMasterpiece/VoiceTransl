import { afterEach, describe, expect, it, vi } from 'vitest'
import { batchPendingStorageKey, restoreBatchPending, batchError, batchStatus, batchTerminal, useBatches, type BatchSummary } from './useBatches'
import { setLocale } from '../i18n'

const response = (payload: unknown, status = 200) => ({ ok: status < 400, status, json: async () => structuredClone(payload) }) as Response
const summary = (changes: Partial<BatchSummary> = {}): BatchSummary => ({ id: 'b1', project_id: 'p1', status: 'queued', workflow_revision: 7, total: 1,
  created_at: '2026-11-06T00:00:00Z', started_at: null, finished_at: null, cancel_requested: false, retry_of: null,
  counts: { queued: 1, running: 0, completed: 0, partial: 0, failed: 0, cancelled: 0, interrupted: 0 }, error: null, ...changes })
const batchItem = { index: 0, sequence: 1, audio_id: 'a1', audio_name: 'voice.wav', source_language: 'ja', origin: null, status: 'queued', run_id: null, started_at: null, finished_at: null, error: null }
const page = (items: unknown[], total = items.length, offset = 0) => ({ items, total, limit: 20, offset })
const sessions: ReturnType<typeof useBatches>[] = []
function setup(override?: (path: string, options?: RequestInit) => Promise<Response> | undefined) {
  const fetcher = vi.fn((input: string, options?: RequestInit) => {
    const path = String(input)
    const custom = override?.(path, options)
    if (custom) return custom
    if (path.includes('/items?')) return Promise.resolve(response(page([batchItem])))
    if (path.includes('/batches?')) return Promise.resolve(response(page([summary()])))
    if (options?.method === 'POST') return Promise.resolve(response(summary(), 202))
    return Promise.resolve(response({ ...summary(), workflow: { schema_version: '4.0', nodes: [], edges: [] } }))
  })
  vi.stubGlobal('fetch', fetcher)
  const batches = useBatches('p1'); sessions.push(batches)
  return { batches, fetcher }
}
async function settle() { for (let index = 0; index < 25; index++) await Promise.resolve() }
afterEach(() => { for (const session of sessions) session.dispose(); sessions.length = 0; sessionStorage.clear(); vi.useRealTimers(); vi.unstubAllGlobals(); setLocale('zh-CN') })

describe('batch REST state', () => {
  it('reuses a request ID and frozen payload after network failure, including repeated create calls', async () => {
    let failures = 1
    const { batches, fetcher } = setup((path, options) => path.endsWith('/batches') && options?.method === 'POST' && failures-- > 0 ? Promise.reject(new TypeError('Failed to fetch')) : undefined)
    await batches.create(7, ['a1'])
    expect(batches.pending.value).not.toBeNull()
    await batches.create(7, ['a1'])
    const posts = fetcher.mock.calls.filter(([path, options]) => path.endsWith('/batches') && options?.method === 'POST')
    expect(posts).toHaveLength(2)
    expect(posts[1]![1]!.body).toBe(posts[0]![1]!.body)
    expect(batches.pending.value).toBeNull()
    expect(batches.detail.value?.workflow_revision).toBe(7)
  })

  it('restores an unconfirmed request after disposal and refresh without creating a new request ID', async () => {
    const { batches, fetcher } = setup((path, options) => path.endsWith('/batches') && options?.method === 'POST' ? Promise.reject(new TypeError('Failed to fetch')) : undefined)
    await batches.create(7, ['a1'])
    const body = batches.pending.value?.body
    batches.dispose()
    const recovered = useBatches('p1'); sessions.push(recovered)
    expect(recovered.pending.value?.body).toEqual(body)
    await recovered.retryPending()
    expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'POST').map(([, options]) => JSON.parse(options!.body as string))).toEqual([body, body])
  })

  it('restores structurally valid pending selections without duplicating the server count limit', () => {
    setup()
    const ids = Array.from({ length: 10001 }, (_, index) => `a${index}`)
    sessionStorage.setItem(batchPendingStorageKey('p1'), JSON.stringify({ path: '', body: { request_id: 'original-key', workflow_revision: 7, audio_ids: ids } }))
    const recovered = useBatches('p1'); sessions.push(recovered)
    expect(recovered.pending.value?.body.audio_ids).toEqual(ids)
  })

  it('ignores a stale activity request that completes after a successful batch creation', async () => {
    let release!: (value: Response) => void
    let initial = true
    const { batches } = setup((path) => path.includes('limit=1&offset=0') && initial ? (initial = false, new Promise((resolve) => { release = resolve })) : undefined)
    const before = batches.refreshActivity()
    await batches.create(7, ['a1'])
    release(response(page([])))
    await before
    expect(batches.active.value).toBe(true)
  })

  it('does not replace an unconfirmed request with a new workflow or audio payload', async () => {
    const { batches, fetcher } = setup((path, options) => path.endsWith('/batches') && options?.method === 'POST' ? Promise.reject(new TypeError('Failed to fetch')) : undefined)
    await batches.create(7, ['a1'])
    await batches.create(8, ['a2'])
    expect(fetcher.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
    expect(batches.pending.value?.body.workflow_revision).toBe(7)
    setLocale('en')
    expect(batches.error.value).toContain('unconfirmed')
  })

  it('keeps confirmed submission success when history or detail loading fails', async () => {
    const { batches } = setup((path, options) => !options?.method && path.includes('/batches') ? Promise.reject(new TypeError('Failed to fetch')) : undefined)
    const result = await batches.create(7, ['a1'])
    expect(result?.id).toBe('b1')
    expect(batches.pending.value).toBeNull()
    expect(batches.active.value).toBe(true)
    expect(batches.error.value).not.toBe('')
  })

  it.each([undefined, null, 0, -1, 1.5])('rejects batch items without a current-schema positive sequence: %s', async sequence => {
    const { batches } = setup(path => path.includes('/items?') ? Promise.resolve(response(page([{ ...batchItem, sequence }]))) : undefined)
    await batches.select('b1')
    expect(batches.items.value.items).toEqual([])
    expect(batches.error.value).not.toBe('')
  })
  it('clears the uncertain request on explicit client rejection and translates known error codes', async () => {
    const { batches } = setup((path, options) => path.endsWith('/batches') && options?.method === 'POST' ? Promise.resolve(response({ error: { code: 'WORKFLOW_REVISION_CONFLICT', message: 'old revision' } }, 409)) : undefined)
    await batches.create(7, ['a1'])
    expect(batches.pending.value).toBeNull()
    expect(batches.error.value).toContain('版本')
    setLocale('en'); expect(batches.error.value).toContain('revision')
  })

  it('recovers paginated history and items and ignores out-of-order details', async () => {
    let release!: (value: Response) => void
    const { batches, fetcher } = setup((path) => {
      if (path.endsWith('/b1')) return new Promise((resolve) => { release = resolve })
      if (path.endsWith('/b2')) return Promise.resolve(response({ ...summary({ id: 'b2' }), workflow: { schema_version: '4.0', nodes: [], edges: [] } }))
      if (path.endsWith('offset=20')) return Promise.resolve(response(page(path.includes('/items?') ? [batchItem] : [summary({ id: 'b2' })], 25, 20)))
      return undefined
    })
    await batches.loadHistory(20)
    expect(batches.history.value.offset).toBe(20)
    expect(batches.history.value.total).toBe(25)
    const older = batches.select('b1')
    await batches.select('b2', 20)
    release(response({ ...summary(), workflow: { schema_version: '4.0', nodes: [], edges: [] } })); await older
    expect(batches.detail.value?.id).toBe('b2')
    expect(batches.items.value.offset).toBe(20)
    expect(fetcher.mock.calls.some(([path]) => path.endsWith('/items?limit=20&offset=20'))).toBe(true)
  })

  it('continues lightweight polling when closed and releases activity when the server finishes', async () => {
    vi.useFakeTimers()
    let completed = false
    const { batches, fetcher } = setup((path) => path.includes('/batches?') ? Promise.resolve(response(page([completed ? summary({ status: 'completed', counts: { queued: 0, running: 0, completed: 1, partial: 0, failed: 0, cancelled: 0, interrupted: 0 } }) : summary()]))) : undefined)
    batches.setVisible(true); await settle()
    batches.setVisible(false)
    const before = fetcher.mock.calls.length
    completed = true
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetcher.mock.calls.slice(before).map(([path]) => path)).toEqual(['/api/v1/projects/p1/batches?limit=1&offset=0'])
    expect(batches.active.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cancels and retries through batch endpoints, preserving the old snapshot on retry', async () => {
    const { batches, fetcher } = setup()
    await batches.cancel('b1')
    await batches.retry('b1', true)
    const posts = fetcher.mock.calls.filter(([, options]) => options?.method === 'POST')
    expect(posts.map(([path]) => path)).toEqual(['/api/v1/projects/p1/batches/b1/cancel', '/api/v1/projects/p1/batches/b1/retry'])
    expect(posts[0]![1]!.body).toBeUndefined()
    expect(JSON.parse(posts[1]![1]!.body as string)).toEqual({ request_id: expect.any(String), include_cancelled: true })
    expect(batches.download('b1')).toBe('/api/v1/projects/p1/batches/b1/download')
  })

  it('keeps unfamiliar states and original diagnostics without enabling terminal actions', async () => {
    const { batches } = setup((path) => path.includes('/batches?') ? Promise.resolve(response(page([summary({ status: 'future_state' })]))) : undefined)
    await batches.loadHistory()
    expect(batches.history.value.items[0]!.status).toBe('future_state')
    expect(batchStatus('future_state')).toContain('future_state')
    expect(batchTerminal('future_state')).toBe(false)
    expect(batchError({ type: 'FUTURE', message: 'Original extension diagnostic' })).toBe('Original extension diagnostic')
  })

  it('rejects inconsistent counts and responses belonging to another project', async () => {
    const { batches } = setup((path) => path.includes('/batches?') ? Promise.resolve(response(page([summary({ project_id: 'p2' })]))) : undefined)
    await batches.loadHistory()
    expect(batches.history.value.items).toHaveLength(0)
    expect(batches.error.value).not.toBe('')
  })
})


describe('pending requests isolated by actual backend', () => {
  const request = { path: '', body: { request_id: 'do-not-duplicate', workflow_revision: 7, audio_ids: ['a1'] } }
  it('does not restore prior-schema requests on the default or custom deployment', () => {
    sessionStorage.setItem('voicetransl.batch.pending:p1', JSON.stringify(request))
    sessionStorage.setItem(batchPendingStorageKey('p1').replace('.batch.v4.pending:', '.batch.pending:'), JSON.stringify(request))
    expect(restoreBatchPending('p1', 'https://api-b.test/voice/api/v1')).toBeNull()
    expect(sessionStorage.getItem('voicetransl.batch.pending:p1')).not.toBeNull()
    expect(restoreBatchPending('p1')).toBeNull()
    expect(sessionStorage.getItem('voicetransl.batch.pending:p1')).not.toBeNull()
    expect(sessionStorage.getItem(batchPendingStorageKey('p1'))).toBeNull()
    expect(restoreBatchPending('p1', 'https://api-b.test/voice/api/v1')).toBeNull()
  })
  it('keeps same-project requests separate by API origin and prefix while sharing equivalent identities', () => {
    const a = 'https://API.test:443/voice/api/v1'; const b = 'https://api.test/other/api/v1'; const c = 'https://another.test/voice/api/v1'
    sessionStorage.setItem(batchPendingStorageKey('p1', a), JSON.stringify(request))
    expect(restoreBatchPending('p1', a.toLowerCase())).toEqual(request)
    expect(restoreBatchPending('p1', b)).toBeNull(); expect(restoreBatchPending('p1', c)).toBeNull(); expect(restoreBatchPending('p2', a)).toBeNull()
  })
  it('does not touch prior-schema requests even if storage is unavailable', () => {
    sessionStorage.setItem('voicetransl.batch.pending:p1', JSON.stringify(request))
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(restoreBatchPending('p1')).toBeNull()
    expect(sessionStorage.getItem('voicetransl.batch.pending:p1')).not.toBeNull(); write.mockRestore()
  })
})


it('reconstructs ZIP and mutation endpoints under a custom startup API prefix without reading legacy pending requests', async () => {
  localStorage.setItem('voicetransl.backendAddress', 'https://new-api.test/voice/api/v1')
  sessionStorage.setItem('voicetransl.batch.pending:p1', JSON.stringify({ path: '', body: { request_id: 'old-service-key', workflow_revision: 7, audio_ids: ['a1'] } }))
  vi.resetModules(); const isolated = await import('./useBatches')
  const session = isolated.useBatches('p1'); sessions.push(session)
  expect(session.pending.value).toBeNull()
  expect(session.download('b1')).toBe('https://new-api.test/voice/api/v1/projects/p1/batches/b1/download')
  const fetcher = vi.fn((_path: string) => Promise.reject(new TypeError('lost response'))); vi.stubGlobal('fetch', fetcher)
  await session.create(7, ['a1'])
  expect(fetcher.mock.calls[0]![0]).toBe('https://new-api.test/voice/api/v1/projects/p1/batches')
  expect(session.pending.value!.body.request_id).not.toBe('old-service-key')
  expect(sessionStorage.getItem('voicetransl.batch.pending:p1')).not.toBeNull()
})
