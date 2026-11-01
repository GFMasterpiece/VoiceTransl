import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowPanels from './components/WorkflowPanels.vue'
import { setLocale, t } from './i18n'
import { useWorkflowStore, type WorkflowArtifact, type WorkflowDocument, type WorkflowNodeRun, type WorkflowRun } from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { runRecords } from './workflow/runRecords'

const graph = {
  nodes: [
    { id: 'input', type: 'input_extension', config: {}, position: { x: 0, y: 0 } },
    { id: 'output', type: 'copy_extension', config: {}, position: { x: 300, y: 0 } },
  ],
  edges: [{ id: 'edge', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
}
const documentBody: WorkflowDocument = { ...graph, schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 2, updated_at: '2026-10-28T00:00:00Z' }
const catalog = readCapabilities({
  schema_version: '1.0',
  workflow: {
    schema_version: '1.3',
    data_types: [{ id: 'audio', label: 'Audio', parent_type_id: null, capabilities: [] }],
    ports: [{ id: 'audio', label: 'Audio', type_id: 'audio', capability_requirements: [] }],
    nodes: [
      { type: 'input_extension', label: 'Input', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, required_inbound: 0, removable: false, max_instances: 1, config: { kind: 'none', fields: [] } },
      { type: 'copy_extension', label: 'Copy', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, max_instances: null, config: { kind: 'none', fields: [] } },
      { type: 'preview_extension', label: 'Preview', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, max_instances: null, config: { kind: 'none', fields: [] }, presentation: { preview: { renderer: 'audio' } } },
    ],
    constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: graph,
  },
  asr: { providers: [{ id: 'mock', label: 'Mock' }] },
  export: { formats: [{ id: 'srt', label: 'SRT' }], naming_modes: [{ id: 'language', label: 'Language' }] },
})
const artifact = (nodeId = 'output', runId = 'run-1'): WorkflowArtifact => ({
  artifact_id: `artifact-${nodeId}`, port: 'audio', filename: `${nodeId}.wav`, media_type: 'audio/wav', size_bytes: 1200, sha256: 'hash',
  download_url: `/api/v1/projects/p1/audios/a1/workflow-runs/${runId}/outputs/${nodeId}/download`,
})
const nodeRecord = (nodeId = 'output', type = 'copy_extension'): WorkflowNodeRun => ({
  node_id: nodeId, node_type: type, config: {}, status: 'completed', started_at: null, finished_at: null, artifact: artifact(nodeId), error: null,
})
const runBody = (overrides: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: 'run-1', project_id: 'p1', audio_id: 'a1', workflow_revision: 2, status: 'completed',
  created_at: '2026-10-28T00:00:00Z', started_at: null, finished_at: null, cancel_requested: false, error: null,
  workflow: structuredClone(graph), nodes: [nodeRecord()], ...overrides,
})
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(body) }) as Response
async function settle() { for (let i = 0; i < 30; i++) await Promise.resolve(); await nextTick() }
let app: App | undefined
let root: HTMLDivElement | undefined
let store: ReturnType<typeof useWorkflowStore> | undefined
function mount(panel: 'run' | 'artifacts' | 'logs' = 'run') {
  const pinia = createPinia(); setActivePinia(pinia)
  store = useWorkflowStore(pinia)
  store.capabilities = structuredClone(catalog)
  store.projectId = 'p1'; store.audioId = 'a1'; store.document = structuredClone(documentBody)
  store.discardDraft(); store.beginDraftEditing()
  const props = reactive<{ panel: 'run' | 'artifacts' | 'logs' | null }>({ panel })
  const close = vi.fn(() => { props.panel = null })
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(WorkflowPanels, { ...props, onClose: close }) }).use(pinia)
  app.mount(root)
  return { props, close, store }
}
function button(label: string) { return [...root!.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === label) }
afterEach(() => {
  app?.unmount(); store?.stop(); root?.remove(); app = undefined; store = undefined; root = undefined
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); setLocale('zh-CN')
})

describe('canvas run panels', () => {
  it('retains save-first and resource-busy guards and starts only the saved graph', async () => {
    const { store } = mount()
    const start = vi.spyOn(store, 'startRun').mockResolvedValue(null)
    await settle()
    expect(button(t('workflow.runWorkflow'))?.disabled).toBe(false)
    button(t('workflow.runWorkflow'))!.click(); await settle()
    expect(start).toHaveBeenCalledTimes(1)
    store.resourceUploading = true; await settle()
    expect(button(t('workflow.runWorkflow'))?.disabled).toBe(true)
    store.resourceUploading = false; store.draft.nodes[1].config = { changed: true }; await settle()
    expect(button(t('workflow.saveToRun'))?.disabled).toBe(true)
    expect(root!.textContent).toContain(t('workflow.saveFirst'))
    expect(start).toHaveBeenCalledTimes(1)
  })

  it('closes and reopens panels without clearing the draft, selected run or polling ownership', async () => {
    const { props, store, close } = mount()
    store.draft.nodes[1].position.x = 640
    store.run = runBody({ status: 'running' })
    const stop = vi.spyOn(store, 'stop')
    const leave = vi.spyOn(store, 'leaveEditor')
    const cancel = vi.spyOn(store, 'cancelRun').mockResolvedValue(undefined)
    await settle()
    expect(button(t('workflow.cancelRun'))).toBeTruthy()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await settle()
    expect(close).toHaveBeenCalledTimes(1)
    expect(root!.querySelector('[role="dialog"]')).toBeNull()
    expect(store.run?.id).toBe('run-1')
    expect(store.draft.nodes[1].position.x).toBe(640)
    expect(stop).not.toHaveBeenCalled(); expect(leave).not.toHaveBeenCalled(); expect(cancel).not.toHaveBeenCalled()
    props.panel = 'run'; await settle()
    expect(button(t('workflow.cancelRun'))).toBeTruthy()
  })

  it('keeps an actual active run polling while every panel is closed', async () => {
    vi.useFakeTimers()
    const { props, store } = mount()
    const running = runBody({ status: 'running', nodes: [{ ...nodeRecord(), status: 'running', artifact: null }] })
    const fetcher = vi.fn((_path: string, init?: RequestInit) => Promise.resolve(response(init?.method === 'POST' ? running : { ...running, status: 'completed' })))
    vi.stubGlobal('fetch', fetcher)
    await store.startRun(); await settle()
    props.panel = null; await settle()
    expect(root!.querySelector('[role="dialog"]')).toBeNull()
    await vi.advanceTimersByTimeAsync(1800); await settle()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(store.run?.id).toBe('run-1')
    expect(store.run?.status).toBe('completed')
  })

  it('selects historical run details without replacing the current graph and disables history during active runs', async () => {
    const { props, store } = mount()
    const historical = runBody({ id: 'run-old', workflow_revision: 1, workflow: { nodes: [{ ...graph.nodes[1], id: 'old-output' }], edges: [] }, nodes: [{ ...nodeRecord('old-output'), artifact: artifact('old-output', 'run-old') }] })
    store.history = [{ id: 'run-old', workflow_revision: 1, status: 'completed', created_at: historical.created_at, finished_at: null, error: null }]
    vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(response(path.endsWith('/outputs/old-output') ? artifact('old-output', 'run-old') : historical))))
    await settle()
    root!.querySelector<HTMLButtonElement>('.workflow-history-row')!.click(); await settle()
    expect(store.run?.id).toBe('run-old')
    expect(store.draft.nodes.map((node) => node.id)).toEqual(['input', 'output'])
    expect(root!.textContent).toContain(t('workflow.snapshotHistorical'))
    props.panel = 'artifacts'; await settle()
    expect(root!.textContent).toContain('run-old')
    expect(root!.textContent).toContain('old-output.wav')
    props.panel = 'run'; store.run = runBody({ status: 'running' }); await settle()
    expect(root!.querySelector<HTMLButtonElement>('.workflow-history-row')?.disabled).toBe(true)
  })

  it('reports history refresh failures without discarding the selected run or graph', async () => {
    const { store } = mount()
    store.run = runBody()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('history unavailable')))
    await settle()
    button(t('workflow.refresh'))!.click(); await settle()
    expect(store.historyError).toBeTruthy()
    expect(root!.querySelector('[role="alert"]')?.textContent).toContain(store.historyError)
    expect(store.run?.id).toBe('run-1')
    expect(store.draft.nodes.map((node) => node.id)).toEqual(['input', 'output'])
  })

  it('keeps metadata failures retryable and only downloads verified available output files', async () => {
    const { store } = mount('artifacts')
    store.run = runBody()
    store.outputChecks = { output: { state: 'missing' } }
    await settle()
    expect(root!.textContent).toContain(t('workflow.artifactMissing'))
    expect(button(t('workflow.download', { filename: 'output.wav' }))).toBeUndefined()
    store.outputChecks.output = { state: 'checking' }; await settle()
    expect(root!.textContent).toContain(t('workflow.artifactChecking'))
    store.outputChecks.output = { state: 'failed' }; await settle()
    expect(root!.textContent).toContain(t('workflow.artifactCheckFailed'))
    const fetcher = vi.fn().mockResolvedValue(response(artifact()))
    vi.stubGlobal('fetch', fetcher)
    button(t('workflow.retryChecks'))!.click(); await settle()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(root!.textContent).toContain(t('workflow.artifactAvailable'))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    button(t('workflow.download', { filename: 'output.wav' }))!.click(); await settle()
    expect(click).toHaveBeenCalledTimes(1)
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).href).toContain('/workflow-runs/run-1/outputs/output/download')
  })

  it('uses catalog source and preview metadata to suppress fake downloads, including unsafe URLs', async () => {
    const { store } = mount('artifacts')
    store.run = runBody({ nodes: [nodeRecord('source', 'input_extension'), nodeRecord('preview', 'preview_extension'), nodeRecord('output')] })
    for (const node of store.run.nodes) { store.outputChecks[node.node_id] = { state: 'available' }; store.outputs[node.node_id] = artifact(node.node_id) }
    store.outputs.output!.download_url = 'javascript:alert(1)'
    await settle()
    expect([...root!.querySelectorAll('button')].some((item) => item.textContent?.includes('下载'))).toBe(false)
    expect(root!.textContent).toContain(t('workflow.panelNoDownload'))
  })

  it('preserves a historical unknown node artifact when the backend verifies a safe download URL', async () => {
    const { store } = mount('artifacts')
    store.run = runBody({ nodes: [
      nodeRecord('legacy-output', 'historical_extension'),
      { ...nodeRecord('legacy-preview', 'historical_preview'), preview: { renderer: 'audio', content_url: '/api/v1/preview', media_type: 'audio/wav' } },
    ] })
    store.outputChecks['legacy-output'] = { state: 'available' }
    store.outputs['legacy-output'] = artifact('legacy-output')
    store.outputChecks['legacy-preview'] = { state: 'available' }
    store.outputs['legacy-preview'] = artifact('legacy-preview')
    await settle()
    expect(button(t('workflow.download', { filename: 'legacy-output.wav' }))).toBeTruthy()
    expect(button(t('workflow.download', { filename: 'legacy-preview.wav' }))).toBeUndefined()
  })

  it('renders recorded times and real errors without inventing start, finish or cancellation times, and switches language in place', async () => {
    const { store } = mount('logs')
    const error = '<script>real failure</script>'
    store.run = runBody({ status: 'failed', cancel_requested: true, error: { type: 'EXTENSION_ERROR', message: error }, nodes: [{ ...nodeRecord(), status: 'failed', artifact: null, error: { type: 'NODE_EXTENSION_ERROR', message: '真实节点错误' } }] })
    await settle()
    expect(root!.textContent).toContain('查看所选运行及节点的时间、状态和错误')
    expect(root!.querySelector('[data-record="run-started"] time')?.textContent).toBe(t('workflow.notRecorded'))
    expect(root!.querySelector('[data-record="node:output:finished"] time')?.hasAttribute('datetime')).toBe(false)
    expect(root!.querySelector('[data-record="run-cancel"] time')?.textContent).toBe(t('workflow.notRecorded'))
    expect(root!.textContent).toContain(error)
    expect(root!.textContent).toContain('真实节点错误')
    expect(root!.querySelector('script')).toBeNull()
    const before = JSON.stringify(store.run)
    setLocale('en'); await settle()
    expect(root!.textContent).toContain('Missing times are shown as not recorded')
    expect(root!.textContent).toContain('Started time')
    expect(root!.textContent).toContain('真实节点错误')
    expect(JSON.stringify(store.run)).toBe(before)
  })
})

describe('persisted run record ordering', () => {
  it('orders real timestamps chronologically, keeps untimed facts stable, and does not mutate the snapshot', () => {
    const run = runBody({ nodes: [
      { ...nodeRecord('second'), started_at: '2026-10-28T00:00:02Z', finished_at: '2026-10-28T00:00:03Z' },
      { ...nodeRecord('first'), started_at: '2026-10-28T00:00:01Z', finished_at: null },
    ] })
    const snapshot = JSON.stringify(run)
    const records = runRecords(run)
    expect(records.slice(0, 4).map((record) => record.id)).toEqual(['run-created', 'node:first:started', 'node:second:started', 'node:second:finished'])
    expect(records.filter((record) => !record.timestamp).map((record) => record.id)).toEqual(['run-started', 'run-finished', 'run-status', 'node:second:status', 'node:first:finished', 'node:first:status'])
    expect(JSON.stringify(run)).toBe(snapshot)
  })
})
