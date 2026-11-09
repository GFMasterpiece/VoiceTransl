import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import catalog from './modularCapabilities.fixture.json'
import { useWorkflowStore } from '../stores/workflow'
import { emptySettings } from './fixtures'

const response = (value: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(value) }) as Response
afterEach(() => { useWorkflowStore().stop(); vi.unstubAllGlobals() })
describe('current workflow persistence', () => {
  it.each(['1.0', undefined, '3.0'])('rejects workflow schema %s without inventing or writing a replacement graph', async version => {
    setActivePinia(createPinia())
    const document = { schema_version: version, project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '', ...structuredClone(catalog.workflow.default_graph) }
    const before = JSON.stringify(document)
    const fetcher = vi.fn(async (input: unknown) => response(String(input).endsWith('/capabilities') ? catalog : String(input).endsWith('/settings') ? emptySettings() : document))
    vi.stubGlobal('fetch', fetcher)
    const store = useWorkflowStore(); store.beginDraftEditing(); await store.load('p1', null)
    expect(store.document).toBeNull(); expect(store.error).not.toBe('')
    expect(store.draft).toEqual({ nodes: [], edges: [] })
    expect(await store.save()).toBeNull()
    expect(JSON.stringify(document)).toBe(before)
    expect(fetcher.mock.calls.every(([, init]: any[]) => !init?.method)).toBe(true)
  })
  it.each(['1.0', undefined])('rejects a run or snapshot using schema %s without changing the editable graph', async version => {
    setActivePinia(createPinia())
    const document = { schema_version: '2.0', project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '', ...structuredClone(catalog.workflow.default_graph) }
    const oldRun = { schema_version: version, id: 'old', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed', created_at: '', finished_at: null, error: null, nodes: [], workflow: { ...document, schema_version: version } }
    vi.stubGlobal('fetch', vi.fn(async (input: unknown) => {
      const path = String(input)
      return response(path.endsWith('/capabilities') ? catalog : path.endsWith('/settings') ? emptySettings() : path.endsWith('/workflow') ? document : path.endsWith('/old') ? oldRun : { items: [], total: 0, limit: 30, offset: 0 })
    }))
    const store = useWorkflowStore(); store.beginDraftEditing(); await store.load('p1', 'a1')
    const before = JSON.stringify(store.draft); await store.loadRun('old')
    expect(store.run).toBeNull(); expect(store.runError).not.toBe('')
    expect(JSON.stringify(store.draft)).toBe(before)
  })

})
