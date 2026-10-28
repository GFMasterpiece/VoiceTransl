import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, reactive, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowEditor from './components/WorkflowEditor.vue'
import {
  WORKFLOW_NODE_HEIGHT,
  WORKFLOW_NODE_WIDTH,
  cloneDocument, createDefaultWorkflow, executionSignature, nextNodePosition, resolveArtifactUrl, useWorkflowStore,
  validateDraft, type WorkflowDocument, type WorkflowDocumentInput, type WorkflowNode, type WorkflowRun, type WorkflowRunSummary,
} from './stores/workflow'

const input = { id: 'input', type: 'audio_input' as const, config: {}, position: { x: 80, y: 120 } }
const output = { id: 'output', type: 'audio_output' as const, config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }
const edge = { id: 'input-output', source: 'input', source_port: 'audio' as const, target: 'output', target_port: 'audio' as const }
const preprocess = { id: 'preprocess-1', type: 'preprocess' as const, config: { provider: 'mock' as const, sample_rate: 16000, channels: 1 as const, preserve_original: true }, position: { x: 260, y: 120 } }
const preprocessEdge = { id: 'input-preprocess', source: 'input', source_port: 'audio' as const, target: 'preprocess-1', target_port: 'audio' as const }
const preprocessToOutput = { id: 'preprocess-output', source: 'preprocess-1', source_port: 'audio' as const, target: 'output', target_port: 'audio' as const }
const outputArtifact = {
  artifact_id: 'art-out', port: 'audio' as const, filename: 'audio-copy.wav',
  media_type: 'audio/wav', size_bytes: 12, sha256: 'a',
  download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/output/download',
}
const preprocessArtifact = {
  ...outputArtifact, artifact_id: 'art-pre', filename: 'audio-pre.wav',
  download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/preprocess-1/download',
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
    if (path.endsWith('/workflow-runs/run-1/outputs/preprocess-1')) return Promise.resolve(response(preprocessArtifact))
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

function node(type: 'audio_input' | 'preprocess' | 'audio_output', index = 0) {
  return root!.querySelectorAll<SVGGElement>(`.workflow-node.is-${type}`)[index]
}

function port(nodeIndex: number, side: 'in' | 'out') {
  return root!.querySelectorAll<SVGCircleElement>('.workflow-node.is-preprocess')[nodeIndex].querySelector<SVGCircleElement>(`.node-port-${side}`)!
}

function outputPort(side: 'in' | 'out') {
  return root!.querySelector<SVGCircleElement>(`.workflow-node.is-audio_output .node-port-${side}`)!
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
    expect(validateDraft({ nodes: [input, { ...output, config: { filename_stem: 'bad/name' } }], edges: [edge] }).map((issue) => issue.code)).toContain('INVALID_FILENAME_STEM')
    expect(validateDraft({ nodes: [input, { ...output, config: { filename_stem: 'bad' + String.fromCharCode(92) + 'name' } }], edges: [edge] }).map((issue) => issue.code)).toContain('INVALID_FILENAME_STEM')
    const cyclic = validateDraft({ nodes: [input, output], edges: [edge, { ...edge, id: 'self-loop', source: 'output', target: 'output' }] })
    expect(cyclic.map((issue) => issue.code)).toEqual(expect.arrayContaining(['SELF_LOOP', 'CYCLE']))
  })

  it('keeps unknown configuration visible in dirty signatures even when known defaults are reordered', () => {
    const omitted = { nodes: [input, { ...preprocess, config: { command: 'ffmpeg -i input output.wav' } as Record<string, unknown> }, output], edges: [preprocessEdge, preprocessToOutput] }
    const explicit = { nodes: [input, { ...preprocess, config: { channels: 1, preserve_original: true, sample_rate: 16000, provider: 'mock', command: 'ffmpeg -i input output.wav' } }, output], edges: [preprocessEdge, preprocessToOutput] }
    expect(executionSignature(omitted)).toBe(executionSignature(explicit))
    expect(executionSignature(omitted)).not.toBe(executionSignature({ ...omitted, nodes: [input, preprocess, output] }))
  })

  it('accepts branches and chained preprocessing while preserving default values in snapshots', () => {
    const chained = { ...preprocess, id: 'preprocess-2', config: { provider: 'ffmpeg' as const, sample_rate: 22050, channels: 2 as const, preserve_original: false }, position: { x: 440, y: 260 } }
    const branch = { ...output, id: 'direct-output', config: { filename_stem: 'direct' }, position: { x: 440, y: 320 } }
    const graph = {
      nodes: [input, preprocess, chained, output, branch],
      edges: [preprocessEdge, preprocessToOutput, { id: 'preprocess-chained', source: 'preprocess-1', source_port: 'audio' as const, target: 'preprocess-2', target_port: 'audio' as const }, { id: 'chained-direct', source: 'preprocess-2', source_port: 'audio' as const, target: 'direct-output', target_port: 'audio' as const }],
    }
    expect(validateDraft(graph)).toEqual([])
    expect(executionSignature({ nodes: [input, { ...preprocess, config: {} }, output], edges: [preprocessEdge, preprocessToOutput] }))
      .toBe(executionSignature({ nodes: [input, preprocess, output], edges: [preprocessEdge, preprocessToOutput] }))
  })

  it('rejects invalid preprocessing values, fields, directions, multiple inputs and unreachable nodes', () => {
    const badConfig = validateDraft({
      nodes: [input, { ...preprocess, config: { provider: 'custom', sample_rate: 7999, channels: 3, preserve_original: 'yes', command: 'rm -rf /' } }, output],
      edges: [preprocessEdge, preprocessToOutput],
    }).map((issue) => issue.code)
    expect(badConfig).toEqual(expect.arrayContaining(['INVALID_NODE_CONFIG', 'INVALID_PREPROCESS_PROVIDER', 'INVALID_SAMPLE_RATE', 'INVALID_CHANNELS', 'INVALID_PRESERVE_ORIGINAL']))
    const invalidDirection = validateDraft({ nodes: [input, preprocess, output], edges: [preprocessEdge, { ...preprocessToOutput, id: 'reverse', source: 'output', target: 'preprocess-1' }] })
    expect(invalidDirection.map((issue) => issue.code)).toEqual(expect.arrayContaining(['INVALID_DIRECTION', 'PREPROCESS_INPUT_COUNT']))
    const multipleInputs = validateDraft({ nodes: [input, preprocess, output], edges: [preprocessEdge, preprocessToOutput, { ...preprocessEdge, id: 'input-again', source: 'input' }] })
    expect(multipleInputs.map((issue) => issue.code)).toContain('PREPROCESS_INPUT_COUNT')
    const unreachable = validateDraft({ nodes: [input, { ...preprocess, id: 'orphan' }, output], edges: [edge] }).map((issue) => issue.code)
    expect(unreachable).toEqual(expect.arrayContaining(['UNREACHABLE_FROM_INPUT', 'OUTPUT_UNREACHABLE']))
  })

  it('places every new node without overlapping any existing node rectangle', () => {
    const firstPre = nextNodePosition('preprocess', [input, output])
    expect(firstPre).toEqual({ x: 286, y: 240 })
    const secondPre = nextNodePosition('preprocess', [input, output, { ...preprocess, position: firstPre }])
    expect(secondPre).toEqual({ x: 286, y: 360 })
    expect(nextNodePosition('audio_output', [input, output, { ...preprocess, position: firstPre }])).toEqual({ x: 420, y: 360 })
    expect(nextNodePosition('audio_output', [{ ...input, position: { x: 80, y: 120 } }])).toEqual({ x: 500, y: 120 })
    const all = [input, output, { ...preprocess, position: firstPre }, { ...preprocess, id: 'preprocess-2', position: secondPre }]
    for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) {
      const overlapX = Math.min(all[i].position.x + WORKFLOW_NODE_WIDTH, all[j].position.x + WORKFLOW_NODE_WIDTH) - Math.max(all[i].position.x, all[j].position.x)
      const overlapY = Math.min(all[i].position.y + WORKFLOW_NODE_HEIGHT, all[j].position.y + WORKFLOW_NODE_HEIGHT) - Math.max(all[i].position.y, all[j].position.y)
      expect(overlapX <= 0 || overlapY <= 0).toBe(true)
    }
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
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
    node('audio_output', 0)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    const filename = root!.querySelector<HTMLInputElement>('#workflow-filename-stem')!
    filename.value = 'chapter-1'
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    button('应用节点设置')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow/validate'))).toBe(true)
    const [path, init] = putCalls()[0]
    expect(String(path)).toContain('/projects/p1/workflow')
    const body = JSON.parse(String((init as RequestInit).body)) as { expected_revision: number; nodes: WorkflowDocumentInput['nodes'] }
    expect(body.expected_revision).toBe(1)
    expect(body.nodes.filter((item) => item.type === 'audio_output')).toHaveLength(2)
    expect(body.nodes.find((item) => item.id === 'output')?.config.filename_stem).toBe('chapter-1')
  })

  it('rejects a Windows-style backslash filename locally without a validate request', async () => {
    setup()
    await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    node('audio_output', 0)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    const before = useWorkflowStore().draft.nodes.find((item) => item.id === 'output')!.config.filename_stem
    const filename = root!.querySelector<HTMLInputElement>('#workflow-filename-stem')!
    filename.value = 'bad' + String.fromCharCode(92) + 'name'
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    button('应用节点设置')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(root!.querySelector('.settings-error')?.textContent).toContain('路径分隔符')
    expect(useWorkflowStore().draft.nodes.find((item) => item.id === 'output')!.config.filename_stem).toBe(before)
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow/validate'))).toBe(false)
    expect(putCalls()).toHaveLength(0)
  })

  it('adds a preprocess node with defaults, edits settings and saves the complete four-field config without reconnecting outputs', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const beforeEdges = JSON.parse(JSON.stringify(store.draft.edges))
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(store.draft.nodes.filter((item) => item.type === 'preprocess')).toHaveLength(1)
    expect(store.draft.edges).toEqual(beforeEdges)
    expect(store.draft.nodes.find((item) => item.type === 'preprocess')?.config).toEqual({ provider: 'mock', sample_rate: 16000, channels: 1, preserve_original: true })
    expect(node('preprocess')!.textContent).toContain('Mock · 原字节直通')
    expect(node('preprocess')!.textContent).not.toContain('kHz')
    node('preprocess')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    expect(root!.textContent).toContain('Mock 只验证链路')
    const provider = root!.querySelector<HTMLSelectElement>('#workflow-preprocess-provider')!
    provider.value = 'ffmpeg'; provider.dispatchEvent(new Event('change', { bubbles: true }))
    const rate = root!.querySelector<HTMLInputElement>('#workflow-sample-rate')!
    rate.value = '22050'; rate.dispatchEvent(new Event('input', { bubbles: true }))
    const channels = root!.querySelector<HTMLSelectElement>('#workflow-channels')!
    channels.value = '2'; channels.dispatchEvent(new Event('change', { bubbles: true }))
    const preserve = root!.querySelector<HTMLInputElement>('.workflow-settings-modal input[type="checkbox"]')!
    preserve.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('应用节点设置')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(store.draft.nodes.find((item) => item.type === 'preprocess')?.config).toEqual({ provider: 'ffmpeg', sample_rate: 22050, channels: 2, preserve_original: false })
    expect(store.draft.edges).toEqual(beforeEdges)
  })

  it('creates chained preprocessing through actual source-right and target-left port clicks', async () => {
    setup()
    await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const store = useWorkflowStore()
    const first = store.draft.nodes.filter((item) => item.type === 'preprocess')[0]
    const second = store.draft.nodes.filter((item) => item.type === 'preprocess')[1]
    store.selectNode(first.id); await settle()
    button('连线模式')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const inputRight = root!.querySelector<SVGCircleElement>('.workflow-node.is-audio_input .node-port-out')!
    inputRight.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(0, 'in').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(0, 'in').dispatchEvent(new MouseEvent('pointerup', { bubbles: true })); await settle()
    expect(store.draft.edges.some((item) => item.source === 'input' && item.target === first.id)).toBe(true)
    store.selectNode(first.id); await settle()
    button('连线模式')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(0, 'out').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(1, 'in').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(1, 'in').dispatchEvent(new MouseEvent('pointerup', { bubbles: true })); await settle()
    expect(store.draft.edges.some((item) => item.source === first.id && item.target === second.id)).toBe(true)
    store.selectNode(second.id); await settle()
    button('连线模式')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    outputPort('in').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    outputPort('in').dispatchEvent(new MouseEvent('pointerup', { bubbles: true })); await settle()
    expect(store.draft.edges.filter((item) => item.target === 'output')).toEqual([expect.objectContaining({ source: second.id })])
    store.selectNode(second.id); await settle()
    button('连线模式')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(0, 'in').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    port(0, 'in').dispatchEvent(new MouseEvent('pointerup', { bubbles: true })); await settle()
    expect(store.error).toContain('循环')
    expect(store.draft.edges.some((item) => item.source === second.id && item.target === first.id)).toBe(false)
    expect(store.draft.edges.filter((item) => item.target === first.id)).toHaveLength(1)
  })

  it('removes a preprocess and its incident edges without touching unrelated branches', async () => {
    setup()
    await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const store = useWorkflowStore()
    const pre = store.draft.nodes.find((item) => item.type === 'preprocess')!
    const extraOutput = store.draft.nodes.find((item) => item.type === 'audio_output' && item.id !== 'output')!
    store.connect('input', pre.id); store.connect(pre.id, extraOutput.id)
    expect(store.draft.edges.some((item) => item.source === 'input' && item.target === 'output')).toBe(true)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    store.selectNode(pre.id); await settle()
    node('preprocess')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true })); await settle()
    expect(store.draft.nodes.some((item) => item.id === pre.id)).toBe(false)
    expect(store.draft.edges.some((item) => item.source === pre.id || item.target === pre.id)).toBe(false)
    expect(store.draft.nodes.some((item) => item.id === extraOutput.id)).toBe(true)
    expect(store.draft.edges.some((item) => item.source === 'input' && item.target === 'output')).toBe(true)
  })

  it('shows invalid preprocess drafts as locatable issues and leaves the draft intact', async () => {
    setup((path) => path.endsWith('/workflow/validate') ? Promise.resolve(response({ valid: false, errors: [{ code: 'INVALID_PREPROCESS_PROVIDER', message: 'Provider 无效', node_id: 'preprocess-1' }] })) : undefined)
    await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const store = useWorkflowStore()
    const pre = store.draft.nodes.find((item) => item.type === 'preprocess')!
    store.updateNode(pre.id, { config: { provider: 'custom', sample_rate: 1000 } })
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(putCalls()).toHaveLength(0)
    expect(store.dirty).toBe(true)
    expect(store.draft.nodes.find((item) => item.id === pre.id)?.config).toMatchObject({ provider: 'custom', sample_rate: 1000 })
  })

  it('does not cover default outputs when adding and stacking preprocess nodes', async () => {
    setup()
    await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const store = useWorkflowStore()
    const boxes = store.draft.nodes.map((item) => ({ id: item.id, left: item.position.x, right: item.position.x + WORKFLOW_NODE_WIDTH, top: item.position.y, bottom: item.position.y + WORKFLOW_NODE_HEIGHT }))
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const overlapX = Math.min(boxes[i].right, boxes[j].right) - Math.max(boxes[i].left, boxes[j].left)
      const overlapY = Math.min(boxes[i].bottom, boxes[j].bottom) - Math.max(boxes[i].top, boxes[j].top)
      expect(overlapX <= 0 || overlapY <= 0).toBe(true)
    }
  })

  it('stacks repeatedly added outputs without covering each other', async () => {
    setup()
    await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const transforms = Array.from(root!.querySelectorAll<SVGGElement>('.workflow-node.is-audio_output'))
      .map((item) => item.getAttribute('transform'))
    expect(transforms).toEqual(['translate(420 120)', 'translate(420 240)', 'translate(420 360)'])
  })

  it('fits all nodes inside a narrow canvas using both dimensions', async () => {
    setup()
    await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const canvas = root!.querySelector<HTMLElement>('.workflow-canvas')!
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, top: 0, left: 0, right: 343, bottom: 390, width: 343, height: 390,
      toJSON: () => ({}),
    })
    button('适配')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const post = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST')
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({ workflow_revision: 1 })
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/outputs/input'))).toBe(false)
    expect(root!.textContent).toContain('产物可用。')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const download = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('audio-copy.wav'))
    expect(download).toBeTruthy()
    download!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const anchor = click.mock.instances[click.mock.instances.length - 1] as unknown as HTMLAnchorElement
    expect(anchor.href).toContain('/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/output/download')
  })

  it('downloads a completed preprocess artifact from run details and historical snapshots', async () => {
    const completed = runBody({
      status: 'completed', finished_at: '2026-10-28T00:00:01Z',
      workflow: { nodes: structuredClone([input, preprocess, output]), edges: [structuredClone(preprocessEdge), structuredClone(preprocessToOutput)] },
      nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'preprocess-1', node_type: 'preprocess', config: structuredClone(preprocess.config), status: 'completed', started_at: null, finished_at: null, artifact: preprocessArtifact, error: null },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'completed', started_at: null, finished_at: null, artifact: outputArtifact, error: null },
      ],
    })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(completed, 202))
      if (path.endsWith('/workflow-runs/run-1/outputs/preprocess-1')) return Promise.resolve(response(preprocessArtifact))
      if (path.endsWith('/workflow-runs/run-1/outputs/output')) return Promise.resolve(response(outputArtifact))
      return undefined
    })
    await settle()
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/outputs/preprocess-1'))).toBe(true)
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/outputs/input'))).toBe(false)
    const download = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('audio-pre.wav'))
    expect(download).toBeTruthy()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    download!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const anchor = click.mock.instances[click.mock.instances.length - 1] as unknown as HTMLAnchorElement
    expect(anchor.href).toContain('/workflow-runs/run-1/outputs/preprocess-1/download')
  })

  it('keeps a processed run read-only and exposes partial and blocked preprocessing states', async () => {
    const partial = runBody({
      status: 'running',
      workflow: { nodes: structuredClone([input, preprocess, output]), edges: [structuredClone(preprocessEdge), structuredClone(preprocessToOutput)] },
      nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'preprocess-1', node_type: 'preprocess', config: structuredClone(preprocess.config), status: 'failed', started_at: null, finished_at: null, artifact: null, error: { type: 'PREPROCESS_FAILED', message: 'FFmpeg 不可用' } },
        { node_id: 'output', node_type: 'audio_output', config: { filename_stem: 'audio-copy' }, status: 'blocked', started_at: null, finished_at: null, artifact: null, error: null },
      ],
    })
    const pinia = createPinia()
    setup((path, init) => {
      if (path.endsWith('/workflow')) return Promise.resolve(response({ ...documentBody(), nodes: structuredClone([input, preprocess, output]), edges: [structuredClone(preprocessEdge), structuredClone(preprocessToOutput)] }))
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(partial, 202))
      if (path.endsWith('/workflow-runs/run-1')) return Promise.resolve(response(partial))
      return undefined
    }, pinia)
    await settle()
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const store = useWorkflowStore(pinia)
    expect(store.activeRun).toBeTruthy()
    expect(root!.querySelectorAll('.workflow-node.is-preprocess')).toHaveLength(1)
    expect(root!.textContent).toContain('FFmpeg 不可用')
    expect(root!.textContent).toContain('被阻塞')
    const edgeCount = store.draft.edges.length
    const nodeCount = store.draft.nodes.length
    store.disconnect(store.draft.edges[0].id)
    store.addNode('preprocess')
    store.removeNode('output')
    expect(store.draft.edges).toHaveLength(edgeCount)
    expect(store.draft.nodes).toHaveLength(nodeCount)
    expect(button('校验并保存')?.disabled).toBe(true)
    expect(button('取消运行')).toBeTruthy()
    expect(Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.trim().startsWith('运行历史'))?.disabled).toBe(true)
    store.stop()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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

  it('does not save a draft changed while its validation response is pending', async () => {
    const pendingValidation = deferred<Response>()
    let validationRequests = 0
    setup((path) => {
      if (!path.endsWith('/workflow/validate')) return undefined
      validationRequests += 1
      return validationRequests === 1
        ? pendingValidation.promise
        : Promise.resolve(response({ valid: true, errors: [] }))
    })
    await settle()
    button('＋ 预处理')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await nextTick()
    const store = useWorkflowStore()
    store.updateNode('preprocess-1', { config: { provider: 'ffmpeg', sample_rate: 22050, channels: 2, preserve_original: true } })
    pendingValidation.resolve(response({ valid: true, errors: [] })); await settle()
    expect(putCalls()).toHaveLength(0)
    expect(store.draft.nodes.find((item) => item.id === 'preprocess-1')?.config.provider).toBe('ffmpeg')
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(validationRequests).toBe(2)
    expect(putCalls()).toHaveLength(1)
    expect(JSON.parse(String((putCalls()[0][1] as RequestInit).body)).nodes.find((item: WorkflowNode) => item.id === 'preprocess-1').config.provider).toBe('ffmpeg')
  })

  it('keeps the latest draft and returns a readable error when a save conflicts', async () => {
    setup((path, init) => path.endsWith('/workflow') && (init as RequestInit)?.method === 'PUT'
      ? Promise.resolve(response({ detail: { error: { code: 'WORKFLOW_REVISION_CONFLICT', message: '工作流已被修改，请刷新后重试' } } }, 409))
      : undefined)
    await settle()
    button('＋ 输出节点')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
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
    button('运行节点工作流')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(root!.textContent).toContain('历史快照，不映射当前画布状态')
    expect(root!.querySelector('.workflow-node')?.getAttribute('class')).toContain('is-state-draft')
  })
})
