import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  WORKFLOW_NODE_ROW_GAP, cloneDocument, executionSignature, nextNodePosition, useWorkflowStore,
  workflowNodeHeight, workflowNodeSize, type WorkflowDocument, type WorkflowNode, type WorkflowRun,
} from '../stores/workflow'
import { readCapabilities } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'

const payload = {
  schema_version: '1.0',
  workflow: {
    schema_version: '1.3',
    nodes: [
      { type: 'source', label: 'Source', is_source: true, position_x: 0, input_ports: [], output_ports: ['audio'], config: { kind: 'none', fields: [] } },
      { type: 'processor', label: 'Processor', position_x: 500, input_ports: ['audio'], output_ports: ['audio'], config: { kind: 'processor', fields: [] } },
      { type: 'future_audio', label: 'Audio preview', position_x: 500, input_ports: ['audio'], output_ports: [], terminal: true, config: { kind: 'none', fields: [] }, presentation: { preview: { renderer: 'audio' } } },
      { type: 'future_text', label: 'Text preview', position_x: 500, input_ports: ['audio'], output_ports: [], terminal: true, config: { kind: 'none', fields: [] }, presentation: { preview: { renderer: 'text' } } },
    ],
    ports: ['audio'],
    constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: { nodes: [], edges: [] },
  },
  asr: { providers: [] },
  export: { formats: [], naming_modes: [] },
}
const capabilities = readCapabilities(payload)
const registry = createNodeRegistry(capabilities)
const node = (id = 'node-1', type = 'processor', x = 500, y = 176): WorkflowNode => ({ id, type, config: {}, position: { x, y } })
const document = (nodes = [node()]): WorkflowDocument => ({
  schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 1,
  updated_at: '2026-11-01T00:00:00Z', nodes, edges: [],
})
const response = (value: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(value) }) as Response
const stores: ReturnType<typeof useWorkflowStore>[] = []
function createStore() {
  const store = useWorkflowStore()
  stores.push(store)
  store.capabilities = structuredClone(capabilities)
  store.beginDraftEditing()
  store.setCleanBaseline(document())
  return store
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => {
  stores.forEach((store) => store.stop())
  stores.length = 0
  vi.unstubAllGlobals()
})

describe('workflow node dimensions', () => {
  it('keeps legacy nodes free of size and derives the old height baseline', () => {
    const legacy = node()
    expect(workflowNodeSize(legacy, registry)).toEqual({ width: 188, height: workflowNodeHeight(legacy.type, registry) })
    const store = createStore()
    expect(store.draft.nodes[0]).not.toHaveProperty('size')
    expect(cloneDocument(document()).nodes[0]).not.toHaveProperty('size')
    expect(store.dirty).toBe(false)
  })

  it('clamps explicit dimensions against metadata and the current measured minimum', () => {
    const preview = { ...node('preview', 'future_audio'), size: { width: 80, height: 20 } }
    expect(workflowNodeSize(preview, registry)).toEqual({ width: 316, height: workflowNodeHeight('future_audio', registry) })
    expect(workflowNodeSize({ ...preview, type: 'future_text' }, registry).width).toBe(188)
    expect(workflowNodeSize(preview, registry, { width: 350, height: 500 })).toEqual({ width: 350, height: 500 })
    expect(workflowNodeSize({ ...preview, size: { width: NaN, height: Infinity } }, registry).width).toBe(316)
    expect(workflowNodeSize(node(), null)).toEqual({ width: 188, height: 92 })
  })

  it('stores only changed content measurements without persisting or dirtying the draft', () => {
    const store = createStore()
    store.validationIssues = [{ code: 'example', message: 'retain' }]
    store.setNodeMinimum('node-1', { width: 340, height: 220 })
    const firstMeasurements = store.nodeMinimums
    store.setNodeMinimum('node-1', { width: 340, height: 220 })
    expect(store.nodeMinimums).toBe(firstMeasurements)
    expect(store.getNodeSize(store.draft.nodes[0]!)).toEqual({ width: 340, height: 220 })
    expect(store.dirty).toBe(false)
    expect(store.validationIssues).toHaveLength(1)
    expect(cloneDocument(store.draft)).toEqual({ nodes: [node()], edges: [] })
    store.setNodeMinimum('node-1', { width: 200, height: 100 })
    expect(store.getNodeSize(store.draft.nodes[0]!)).toEqual({ width: 200, height: 100 })
  })

  it('allows a large node to shrink and reset without retaining its previous dimensions as a minimum', () => {
    const store = createStore()
    store.setNodeMinimum('node-1', { width: 210, height: 120 })
    store.updateNode('node-1', { size: { width: 800, height: 700 } })
    expect(store.getNodeSize(store.draft.nodes[0]!)).toEqual({ width: 800, height: 700 })
    store.updateNode('node-1', { size: { width: 220, height: 130 } })
    expect(store.getNodeSize(store.draft.nodes[0]!)).toEqual({ width: 220, height: 130 })
    store.updateNode('node-1', { size: null })
    expect(store.getNodeSize(store.draft.nodes[0]!)).toEqual({ width: 210, height: 120 })
    expect(store.dirty).toBe(false)
  })

  it('changes layout dirty state while keeping execution signatures and run matching stable', () => {
    const store = createStore()
    const original = cloneDocument(store.draft)
    store.run = { status: 'completed', workflow: original, nodes: [] } as unknown as WorkflowRun
    store.updateNode('node-1', { size: { width: 450, height: 300 } })
    expect(store.dirty).toBe(true)
    expect(executionSignature(store.draft, registry)).toBe(executionSignature(original, registry))
    expect(store.runMatchesDraft).toBe(true)
    expect(cloneDocument(store.draft).nodes[0]!.size).toEqual({ width: 450, height: 300 })
    store.updateNode('node-1', { size: null })
    expect(store.dirty).toBe(false)
    store.updateNode('node-1', { position: { x: 500, y: 200 } })
    expect(store.dirty).toBe(true)
  })

  it.each(['saving', 'validating', 'resourceUploading'] as const)('blocks size changes while %s', (state) => {
    const store = createStore()
    store[state] = true
    store.updateNode('node-1', { size: { width: 500, height: 500 } })
    expect(store.draft.nodes[0]).not.toHaveProperty('size')
  })

  it('blocks size changes during an active run', () => {
    const store = createStore()
    store.run = { status: 'running' } as WorkflowRun
    store.updateNode('node-1', { size: { width: 500, height: 500 } })
    expect(store.draft.nodes[0]).not.toHaveProperty('size')
  })

  it('blocks size changes while a run start request is pending', async () => {
    const store = createStore()
    store.document = document()
    store.projectId = 'p1'
    store.audioId = 'a1'
    let resolveStart!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { resolveStart = resolve })))
    const pendingStart = store.startRun()
    expect(store.actionLoading).toBe(true)
    store.updateNode('node-1', { size: { width: 500, height: 500 } })
    expect(store.draft.nodes[0]).not.toHaveProperty('size')
    resolveStart(response({ id: 'run1', audio_id: 'a1', status: 'completed', nodes: [], workflow: cloneDocument(store.draft) }))
    await pendingStart
    expect(store.actionLoading).toBe(false)
  })

  it('compares dimensions independently of object property order', () => {
    const store = createStore()
    store.setCleanBaseline(document([{ ...node(), size: { height: 300, width: 400 } }]))
    store.updateNode('node-1', { size: { width: 400, height: 300 } })
    expect(store.dirty).toBe(false)
  })

  it('saves and reloads explicit size while keeping measurements out of the request and clearing them across projects', async () => {
    const store = createStore()
    let saved = document()
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const body = JSON.parse(init.body as string)
        saved = { ...saved, nodes: body.nodes, edges: body.edges, revision: 2 }
      }
      return Promise.resolve(response(saved))
    })
    vi.stubGlobal('fetch', fetchMock)
    await store.load('p1', null)
    store.setNodeMinimum('node-1', { width: 210, height: 150 })
    store.updateNode('node-1', { size: { width: 400, height: 280 } })
    await store.save()
    expect(store.dirty).toBe(false)
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT')!
    expect(JSON.parse(put[1]!.body as string)).toEqual({
      expected_revision: 1, nodes: [{ ...node(), size: { width: 400, height: 280 } }], edges: [],
    })
    expect(store.document!.nodes[0]!.size).toEqual({ width: 400, height: 280 })
    await store.load('p2', null)
    expect(store.nodeMinimums).toEqual({})
    expect(store.draft.nodes[0]!.size).toEqual({ width: 400, height: 280 })
  })
})

describe('placement with variable node rectangles', () => {
  it('avoids a widened node whose origin is more than one baseline width away', () => {
    const wide = { ...node('wide', 'source', 150, 176), size: { width: 450, height: 400 } }
    expect(nextNodePosition('processor', [wide], registry)).toEqual({ x: 500, y: 576 + WORKFLOW_NODE_ROW_GAP })
  })

  it('places same-type nodes below explicit and measured heights', () => {
    const tall = { ...node(), size: { width: 250, height: 500 } }
    expect(nextNodePosition('processor', [tall], registry).y).toBe(676 + WORKFLOW_NODE_ROW_GAP)
    expect(nextNodePosition('processor', [node()], registry, { 'node-1': { width: 280, height: 600 } }).y).toBe(776 + WORKFLOW_NODE_ROW_GAP)
  })

  it('accounts for the new type audio minimum width and same-type measured width', () => {
    const obstacle = node('obstacle', 'source', 760, 176)
    expect(nextNodePosition('future_audio', [obstacle], registry).y).toBe(268 + WORKFLOW_NODE_ROW_GAP)
    const sameType = node('processor-1', 'processor', 500, -200)
    const lowerObstacle = node('obstacle', 'source', 800, -80)
    expect(nextNodePosition('processor', [sameType, lowerObstacle], registry, { 'processor-1': { width: 400, height: 92 } }).y)
      .toBe(12 + WORKFLOW_NODE_ROW_GAP)
  })

  it('passes measured dimensions through store.addNode', () => {
    const store = createStore()
    store.setNodeMinimum('node-1', { width: 400, height: 600 })
    store.addNode('processor')
    expect(store.draft.nodes[1]!.position).toEqual({ x: 500, y: 776 + WORKFLOW_NODE_ROW_GAP })
  })
})
