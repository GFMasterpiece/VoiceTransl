import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, reactive, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowEditor from './components/WorkflowEditor.vue'
import {
  WORKFLOW_NODE_HEIGHT,
  WORKFLOW_NODE_WIDTH,
  cloneDocument, createDefaultWorkflow, executionSignature, nextNodePosition, resolveArtifactUrl, useWorkflowStore,
  validateDraft, type WorkflowDocument, type WorkflowDocumentInput, type WorkflowRun, type WorkflowRunSummary,
} from './stores/workflow'

const input = { id: 'input', type: 'audio_input' as const, config: {}, position: { x: 80, y: 120 } }
const output = { id: 'output', type: 'audio_output' as const, config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }
const edge = { id: 'input-output', source: 'input', source_port: 'audio' as const, target: 'output', target_port: 'audio' as const }
const outputArtifact = {
  artifact_id: 'art-out', port: 'audio' as const, filename: 'audio-copy.wav',
  media_type: 'audio/wav', size_bytes: 12, sha256: 'a',
  download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/output/download',
}
const documentBody = (revision = 1, workflowRevision = 1): WorkflowDocument => ({
  schema_version: '1.0', project_id: 'p1', revision, workflow_revision: workflowRevision,
  updated_at: '2026-10-28T00:00:00Z', nodes: structuredClone([input, output]), edges: [structuredClone(edge)],
})

function runBody(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: 'run-1', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'pending',
    created_at: '2026-10-28T00:00:00Z', started_at: null, finished_at: null, cancel_requested: false,
    error: null, workflow: { nodes: structuredClone([input, output]), edges: [structuredClone(edge)] },
    nodes: [
      { node_id: 'input', node_type: 'audio_input', config: {}, status: 'pending', started_at: null, finished_at: null, artifact: null, error: null },
      { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'pending', started_at: null, finished_at: null, artifact: null, error: null },
    ],
    ...overrides,
  }
}

function summaryBody(overrides: Partial<WorkflowRunSummary> = {}): WorkflowRunSummary {
  const { workflow: _workflow, nodes: _nodes, ...summary } = runBody()
  return {
    id: summary.id,
    workflow_revision: summary.workflow_revision,
    status: summary.status,
    created_at: summary.created_at,
    finished_at: summary.finished_at,
    error: summary.error,
    ...overrides,
  }
}

const response = (payload: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => structuredClone(payload),
}) as Response
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
async function settle() { for (let index = 0; index < 30; index++) await Promise.resolve(); await nextTick() }

let app: VueApp | undefined
let root: HTMLDivElement | undefined
let fetchMock: ReturnType<typeof vi.fn>

type FetchOverride = (path: string, init?: RequestInit) => Promise<Response> | undefined

function setup(override?: FetchOverride, pinia = createPinia(), properties: Record<string, unknown> = {}) {
  setActivePinia(pinia)
  fetchMock = vi.fn((request: RequestInfo | URL, init?: RequestInit) => {
    const path = String(request)
    const custom = override?.(path, init)
    if (custom) return custom
    if (path.endsWith('/workflow')) return Promise.resolve(response(documentBody()))
    if (path.endsWith('/workflow/validate')) return Promise.resolve(response({ valid: true, errors: [] }))
    if (path.endsWith('/workflow-runs?limit=30&offset=0')) return Promise.resolve(response({ items: [], total: 0, limit: 30, offset: 0 }))
    if (path.endsWith('/workflow-runs')) return Promise.resolve(response(runBody(), 202))
    if (path.match(/\/workflow-runs\/[^/]+$/)) return Promise.resolve(response(runBody()))
    return Promise.resolve(response(documentBody()))
  })
  vi.stubGlobal('fetch', fetchMock)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(WorkflowEditor, { projectId: 'p1', audioId: 'a1', audioName: 'voice.wav', ...properties }).use(pinia)
  app.mount(root)
  return { pinia }
}

function button(text: string) {
  return Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.trim() === text)
}

function node(type: 'audio_input' | 'audio_output', index = 0) {
  return root!.querySelectorAll<SVGGElement>(`.workflow-node.is-${type}`)[index]
}

function putCalls() {
  return fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/workflow') && (init as RequestInit)?.method === 'PUT')
}

afterEach(() => {
  app?.unmount(); app = undefined
  root?.remove(); root = undefined
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('workflow validation and helpers', () => {
  it('validates graph requirements, detects a real cycle, and ignores layout and edge IDs in execution signatures', () => {
    expect(validateDraft({ nodes: [input, output], edges: [edge] })).toEqual([])
    const changedLayout = { nodes: [{ ...input, position: { x: 140, y: 160 } }, output], edges: [{ ...edge, id: 'renamed-edge' }] }
    expect(executionSignature({ nodes: [input, output], edges: [edge] })).toBe(executionSignature(changedLayout))
    const errors = validateDraft({ nodes: [input], edges: [] })
    expect(errors.map((issue) => issue.code)).toEqual(expect.arrayContaining(['OUTPUT_MISSING', 'ISOLATED_NODE']))
    expect(validateDraft({ nodes: [input, { ...output, config: {} }], edges: [edge] }).map((issue) => issue.code)).toContain('INVALID_FILENAME_STEM')
    const cyclic = validateDraft({ nodes: [input, output], edges: [edge, { ...edge, id: 'self-loop', source: 'output', target: 'output' }] })
    expect(cyclic.map((issue) => issue.code)).toEqual(expect.arrayContaining(['SELF_LOOP', 'CYCLE']))
  })

  it('places each new output below the existing output so default nodes do not overlap', () => {
    expect(nextNodePosition('audio_output', [input, output])).toEqual({ x: 420, y: 240 })
    expect(nextNodePosition('audio_output', [input, { ...output, position: { x: 420, y: 240 } }]))
      .toEqual({ x: 420, y: 360 })
  })

  it('deep-clones reactive drafts with JSON-safe data', () => {
    const draft = reactive(createDefaultWorkflow())
    const copy = cloneDocument(draft)
    expect(copy).toEqual(draft)
    expect(copy).not.toBe(draft)
    expect(copy.nodes[0]).not.toBe(draft.nodes[0])
  })

  it('resolves relative artifact URLs against the API origin and rejects unsupported schemes', () => {
    expect(resolveArtifactUrl('/api/v1/outputs/output/download', '/api/v1', 'https://ui.example'))
      .toBe('https://ui.example/api/v1/outputs/output/download')
    expect(resolveArtifactUrl('outputs/output/download', 'https://api.example/api/v1', 'https://ui.example'))
      .toBe('https://api.example/api/v1/outputs/output/download')
    expect(resolveArtifactUrl('javascript:alert(1)', '/api/v1', 'https://ui.example')).toBe('')
  })
})

describe('workflow editor interactions', () => {
  it('adds an output, edits its filename, validates and saves the expected payload', async () => {
    setup()
    await settle()
    expect(root!.querySelector('svg.workflow-svg')).not.toBeNull()
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(2)
    button('＋ 输出节点')!.click(); await settle()
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
    node('audio_output', 0)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    const filename = root!.querySelector<HTMLInputElement>('#workflow-filename-stem')!
    filename.value = 'chapter-1'
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    button('应用节点设置')!.click(); await settle()
    button('校验并保存')!.click(); await settle()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow/validate'))).toBe(true)
    const [path, init] = putCalls()[0]
    expect(String(path)).toContain('/projects/p1/workflow')
    const body = JSON.parse(String((init as RequestInit).body)) as { expected_revision: number; nodes: WorkflowDocumentInput['nodes'] }
    expect(body.expected_revision).toBe(1)
    expect(body.nodes.filter((item) => item.type === 'audio_output')).toHaveLength(2)
    expect(body.nodes.find((item) => item.id === 'output')?.config.filename_stem).toBe('chapter-1')
  })

  it('stacks repeatedly added outputs without covering each other', async () => {
    setup()
    await settle()
    button('＋ 输出节点')!.click(); await settle()
    button('＋ 输出节点')!.click(); await settle()
    const transforms = Array.from(root!.querySelectorAll<SVGGElement>('.workflow-node.is-audio_output'))
      .map((item) => item.getAttribute('transform'))
    expect(transforms).toEqual(['translate(420 120)', 'translate(420 240)', 'translate(420 360)'])
  })

  it('fits all nodes inside a narrow canvas using both dimensions', async () => {
    setup()
    await settle()
    button('＋ 输出节点')!.click(); await settle()
    const canvas = root!.querySelector<HTMLElement>('.workflow-canvas')!
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, top: 0, left: 0, right: 343, bottom: 390, width: 343, height: 390,
      toJSON: () => ({}),
    })
    button('适配')!.click(); await settle()
    const store = useWorkflowStore()
    const minX = Math.min(...store.draft.nodes.map((item) => item.position.x))
    const minY = Math.min(...store.draft.nodes.map((item) => item.position.y))
    const maxX = Math.max(...store.draft.nodes.map((item) => item.position.x + WORKFLOW_NODE_WIDTH))
    const maxY = Math.max(...store.draft.nodes.map((item) => item.position.y + WORKFLOW_NODE_HEIGHT))
    const left = minX * store.zoom + store.pan.x
    const right = maxX * store.zoom + store.pan.x
    const top = minY * store.zoom + store.pan.y
    const bottom = maxY * store.zoom + store.pan.y
    expect(store.zoom).toBeGreaterThanOrEqual(.5)
    expect(store.zoom).toBeLessThan(.6)
    expect(left).toBeGreaterThanOrEqual(0)
    expect(right).toBeLessThanOrEqual(343)
    expect(top).toBeGreaterThanOrEqual(0)
    expect(bottom).toBeLessThanOrEqual(390)
  })

  it('does not save after validation fails and keeps a locatable draft issue', async () => {
    setup((path) => path.endsWith('/workflow/validate')
      ? Promise.resolve(response({ valid: false, errors: [{ code: 'OUTPUT_INPUT_COUNT', message: '输出需要一个音频输入', node_id: 'output' }] }))
      : undefined)
    await settle()
    button('＋ 输出节点')!.click(); await settle()
    button('校验并保存')!.click(); await settle()
    expect(root!.querySelector('.workflow-issues')!.textContent).toContain('输出需要一个音频输入')
    expect(putCalls()).toHaveLength(0)
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
  })

  it('downloads only after the current output metadata request succeeds', async () => {
    const completed = runBody({
      status: 'completed', finished_at: '2026-10-28T00:00:01Z', nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: { ...outputArtifact, artifact_id: 'art-in', filename: 'voice.wav', download_url: null }, error: null },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'completed', started_at: null, finished_at: null, artifact: outputArtifact, error: null },
      ],
    })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(completed, 202))
      if (path.endsWith('/workflow-runs/run-1/outputs/output')) return Promise.resolve(response(outputArtifact))
      return undefined
    })
    await settle()
    button('运行节点工作流')!.click(); await settle()
    const post = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST')
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({ workflow_revision: 1 })
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/outputs/input'))).toBe(false)
    expect(root!.textContent).toContain('产物可用。')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const download = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('audio-copy.wav'))
    expect(download).toBeTruthy()
    download!.click(); await settle()
    const anchor = click.mock.instances[click.mock.instances.length - 1] as unknown as HTMLAnchorElement
    expect(anchor.href).toContain('/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/output/download')
  })

  it('keeps run snapshot metadata but disables download when output metadata is 404', async () => {
    const completed = runBody({
      status: 'completed', finished_at: '2026-10-28T00:00:01Z', nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'completed', started_at: null, finished_at: null, artifact: outputArtifact, error: null },
      ],
    })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(completed, 202))
      if (path.endsWith('/workflow-runs/run-1/outputs/output')) {
        return Promise.resolve(response({ error: { code: 'WORKFLOW_OUTPUT_NOT_FOUND', message: '节点产物文件不存在' } }, 404))
      }
      return undefined
    })
    await settle()
    button('运行节点工作流')!.click(); await settle()
    const store = useWorkflowStore()
    expect(store.run?.nodes[1].artifact?.filename).toBe('audio-copy.wav')
    expect(root!.textContent).toContain('快照产物')
    expect(root!.textContent).toContain('产物缺失，不可下载。')
    expect(root!.textContent).not.toContain('产物可用。')
    expect(root!.querySelector('.workflow-output-state')?.getAttribute('class')).toContain('is-missing')
    expect(Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).some((item) => item.textContent?.includes('audio-copy.wav'))).toBe(false)
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    await store.downloadArtifact('output')
    expect(click).not.toHaveBeenCalled()
    expect(store.error).toBe('产物缺失，不可下载。')
  })

  it('reports a metadata network failure as retryable and then recovers on retry', async () => {
    const completed = runBody({
      status: 'completed', finished_at: '2026-10-28T00:00:01Z', nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'completed', started_at: null, finished_at: null, artifact: outputArtifact, error: null },
      ],
    })
    let metadataRequests = 0
    setup((path, init) => {
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(completed, 202))
      if (path.endsWith('/workflow-runs/run-1/outputs/output')) {
        metadataRequests += 1
        return metadataRequests === 1
          ? Promise.reject(new TypeError('Failed to fetch'))
          : Promise.resolve(response(outputArtifact))
      }
      return undefined
    })
    await settle()
    button('运行节点工作流')!.click(); await settle()
    expect(root!.textContent).toContain('产物检查失败，可重试。')
    expect(root!.textContent).not.toContain('产物缺失，不可下载。')
    expect(button('重试检查')).toBeTruthy()
    expect(Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).some((item) => item.textContent?.includes('audio-copy.wav'))).toBe(false)
    button('重试检查')!.click()
    await vi.waitFor(() => {
      const download = Array.from(root!.querySelectorAll<HTMLButtonElement>('button'))
        .some((item) => item.textContent?.includes('audio-copy.wav'))
      expect(download).toBe(true)
    })
    expect(metadataRequests).toBe(2)
    expect(useWorkflowStore().outputChecks.output).toEqual({ state: 'available' })
  })

  it('keeps the latest draft and returns a readable error when a save conflicts', async () => {
    setup((path, init) => path.endsWith('/workflow') && (init as RequestInit)?.method === 'PUT'
      ? Promise.resolve(response({ detail: { error: { code: 'WORKFLOW_REVISION_CONFLICT', message: '工作流已被修改，请刷新后重试' } } }, 409))
      : undefined)
    await settle()
    button('＋ 输出节点')!.click(); await settle()
    button('校验并保存')!.click(); await settle()
    expect(root!.querySelector('.node-workflow-alert')?.textContent).toContain('工作流已被修改')
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
    expect(root!.querySelector('.workflow-dirty-state')?.textContent).toContain('未保存')
  })
})

describe('workflow request isolation and polling', () => {
  it('ignores a delayed old load after unloading and does not poll a stopped session', async () => {
    vi.useFakeTimers()
    const old = deferred<Response>()
    setup((path) => path.endsWith('/workflow') ? old.promise : undefined)
    await nextTick(); await nextTick()
    expect(root!.querySelector('.workflow-loading')?.textContent).toContain('正在读取')
    app!.unmount(); app = undefined
    old.resolve(response(documentBody()))
    await settle()
    const callsAfterUnload = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchMock.mock.calls.length).toBe(callsAfterUnload)
    expect(root!.querySelector('.workflow-node')).toBeNull()
  })

  it('loads history summaries without full run details and keeps polling through validate', async () => {
    const pinia = createPinia()
    vi.useFakeTimers()
    let pollCount = 0
    const active = runBody({ status: 'running' })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs?limit=30&offset=0')) {
        return Promise.resolve(response({ items: [summaryBody({ status: 'completed' })], total: 1, limit: 30, offset: 0 }))
      }
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(active, 202))
      if (path.match(/\/workflow-runs\/run-1$/)) {
        pollCount += 1
        return Promise.resolve(response(runBody({ status: pollCount > 1 ? 'completed' : 'running' })))
      }
      if (path.endsWith('/workflow/validate')) {
        return new Promise((resolve) => setTimeout(() => resolve(response({ valid: true, errors: [] })), 100))
      }
      return undefined
    }, pinia)
    await settle()
    const store = useWorkflowStore()
    expect(store.history).toHaveLength(1)
    expect(store.history[0]).not.toHaveProperty('workflow')
    store.markDirty()
    button('运行节点工作流')!.click(); await settle()
    expect(store.activeRun).toBeTruthy()
    const validatePromise = store.validate()
    await vi.advanceTimersByTimeAsync(1900)
    expect(pollCount).toBeGreaterThanOrEqual(1)
    await vi.advanceTimersByTimeAsync(100)
    await validatePromise
    expect(store.validating).toBe(false)
    expect(store.activeRun).toBeTruthy()
    store.stop()
  })

  it('does not request output metadata for input records and ignores an obsolete history selection response', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    const activeRun = runBody({
      status: 'running',
      nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: { artifact_id: 'art-in', port: 'audio', filename: 'voice.wav', media_type: 'audio/wav', size_bytes: 12, sha256: 'a', download_url: null }, error: null },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'running', started_at: null, finished_at: null, artifact: null, error: null },
      ],
    })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(activeRun, 202))
      if (path.match(/\/workflow-runs\/run-1$/)) return Promise.resolve(response(activeRun))
      return undefined
    }, pinia)
    await settle()
    const store = useWorkflowStore()
    expect(store.document).toBeTruthy()
    expect(store.history).toHaveLength(0)
    button('运行节点工作流')!.click(); await settle()
    expect(store.run?.nodes[0].artifact?.download_url).toBeNull()
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/outputs/input'))).toBe(false)
    store.stop()
  })

  it('does not let an older history selection overwrite the active run that keeps polling', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    const staleDetail = deferred<Response>()
    let pollCount = 0
    const activeRun = runBody({ status: 'running' })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs?limit=30&offset=0')) {
        return Promise.resolve(response({
          items: [summaryBody({ status: 'running' }), summaryBody({ id: 'run-old', status: 'completed' })],
          total: 2, limit: 30, offset: 0,
        }))
      }
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(activeRun, 202))
      if (path.endsWith('/workflow-runs/run-1')) {
        pollCount += 1
        return Promise.resolve(response(activeRun))
      }
      if (path.endsWith('/workflow-runs/run-old')) return staleDetail.promise
      return undefined
    }, pinia)
    await settle()
    const store = useWorkflowStore()
    expect(store.document).toBeTruthy()
    await store.loadHistory()
    await settle()
    expect(store.history).toHaveLength(2)
    await store.startRun()
    await settle()
    expect(store.activeRun).toBeTruthy()
    await vi.advanceTimersByTimeAsync(1900)
    expect(pollCount).toBeGreaterThanOrEqual(1)
    expect(store.run?.id).toBe('run-1')
    store.stop()
    staleDetail.resolve(response(runBody({ id: 'run-old', status: 'completed' })))
    await settle()
    expect(store.run?.id).toBe('run-1')
    expect(store.activeRun).toBeTruthy()
  })

  it('does not apply node statuses when a selected history snapshot differs from the current draft', async () => {
    const historical = runBody({
      status: 'completed',
      workflow: { nodes: structuredClone([input, { ...output, id: 'old-output' }]), edges: [] },
      nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'old-output', node_type: 'audio_output', config: { filename_stem: 'old-copy' }, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
      ],
    })
    setup((path) => path.endsWith('/workflow-runs') ? Promise.resolve(response(historical, 202)) : undefined)
    await settle()
    button('运行节点工作流')!.click(); await settle()
    expect(root!.textContent).toContain('历史快照，不映射当前画布状态')
    expect(root!.querySelector('.workflow-node')?.getAttribute('class')).toContain('is-state-draft')
  })
})
