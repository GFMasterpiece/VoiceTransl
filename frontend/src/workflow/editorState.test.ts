import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { cloneDocument, useWorkflowStore, type WorkflowDocument, type WorkflowDocumentInput, type WorkflowNode, type WorkflowRun } from '../stores/workflow'
import { readCapabilities } from './capabilities'

const graph = (): WorkflowDocumentInput => ({
  nodes: [
    { id: 'source', type: 'future_source', config: {}, position: { x: 0, y: 10 } },
    { id: 'process', type: 'future_processor', config: { label: 'original' }, position: { x: 400, y: 10 } },
  ],
  edges: [{ id: 'original-edge', source: 'source', source_port: 'audio', target: 'process', target_port: 'audio' }],
})
const capabilities = readCapabilities({
  schema_version: '1.0', workflow: {
    schema_version: '1.3',
    nodes: [
      { type: 'future_source', label: 'Source', is_source: true, removable: false, max_instances: 1, input_ports: [], output_ports: ['audio'], config: { kind: 'none', fields: [] } },
      { type: 'future_processor', label: 'Processor', input_ports: ['audio'], output_ports: ['audio'], removable: true, config: { kind: 'extension', fields: [
        { key: 'label', type: 'string', default: 'original' }, { key: 'amount', type: 'number' },
      ] } },
    ],
    ports: ['audio'], constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: graph(),
  }, asr: { providers: [] }, export: { formats: [], naming_modes: [] },
})
const documentBody = (value = graph()): WorkflowDocument => ({
  ...cloneDocument(value), schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '2026-11-06T00:00:00Z',
})
const response = (value: unknown) => ({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(value)) }) as Response
let store: ReturnType<typeof useWorkflowStore>
beforeEach(() => {
  setActivePinia(createPinia())
  store = useWorkflowStore()
  store.capabilities = structuredClone(capabilities)
  store.beginDraftEditing()
  store.document = documentBody()
  store.setCleanBaseline(graph())
  store.selectNode('process')
})
afterEach(() => { store.stop(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('editor graph history', () => {
  it('undoes and redoes additions, configuration, connections, removals and dimensions', () => {
    store.addNode('future_processor')
    const added = store.selectedNodeId!
    store.updateNodeConfig(added, { label: 'edited' })
    store.updateNodeUi(added, { size: { width: 400, height: 300 } })
    expect(store.connect('process', added, 'audio', 'audio')).toBe(true)
    store.removeNode(added)
    const final = cloneDocument(store.draft)
    for (let count = 0; count < 5; count++) expect(store.undo()).toBe(true)
    expect(store.draft).toEqual(graph())
    expect(store.dirty).toBe(false)
    expect(store.selectedNodeId).toBe('process')
    for (let count = 0; count < 5; count++) expect(store.redo()).toBe(true)
    expect(store.draft).toEqual(final)
  })

  it('merges same-field typing within 600ms and respects explicit and field boundaries', () => {
    vi.useFakeTimers()
    store.updateNodeConfig('process', { label: 'o' })
    vi.advanceTimersByTime(400)
    store.updateNodeConfig('process', { label: 'one' })
    store.updateNodeConfig('process', { label: 'one', amount: 1 })
    store.updateNodeConfig('process', { label: 'one', amount: 2 })
    expect(store.undo()).toBe(true)
    expect(store.selectedNode!.config).toEqual({ label: 'one' })
    expect(store.undo()).toBe(true)
    expect(store.selectedNode!.config).toEqual({ label: 'original' })
    store.updateNodeConfig('process', { label: 'a' })
    store.closeHistoryGroup()
    store.updateNodeConfig('process', { label: 'b' })
    store.undo()
    expect(store.selectedNode!.config.label).toBe('a')
    vi.advanceTimersByTime(601)
    store.updateNodeConfig('process', { label: 'c' })
    store.undo()
    expect(store.selectedNode!.config.label).toBe('a')
  })

  it('records a whole multi-node drag and resize transaction as one operation', () => {
    store.setSelection(['source', 'process'])
    expect(store.beginHistoryTransaction()).toBe(true)
    store.updateNodeUi('source', { position: { x: 100, y: 20 } })
    store.updateNodeUi('process', { position: { x: 500, y: 20 } })
    store.updateNodeUi('process', { size: { width: 300, height: 200 } })
    store.updateNodeUi('source', { position: { x: 120, y: 20 } })
    expect(store.commitHistoryTransaction()).toBe(true)
    store.undo()
    expect(store.draft).toEqual(graph())
    expect(store.selectedNodeIds).toEqual(['source', 'process'])
    expect(store.canUndo).toBe(false)
    store.redo()
    expect(store.draft.nodes[0].position.x).toBe(120)
    expect(store.draft.nodes[1].size).toEqual({ width: 300, height: 200 })
  })

  it('folds initial measured placement into node insertion without a second undo step', () => {
    expect(store.canAdjustInsertedNodeUi('process')).toBe(false)
    expect(store.adjustInsertedNodeUi('process', { position: { x: 1, y: 1 } })).toBe(false)
    store.addNode('future_processor')
    const id = store.selectedNodeId!
    expect(store.canAdjustInsertedNodeUi(id)).toBe(true)
    store.saving = true
    expect(store.canAdjustInsertedNodeUi(id)).toBe(false)
    store.saving = false
    store.beginHistoryTransaction()
    expect(store.canAdjustInsertedNodeUi(id)).toBe(false)
    store.cancelHistoryTransaction()
    expect(store.canAdjustInsertedNodeUi(id)).toBe(true)
    expect(store.adjustInsertedNodeUi(id, { position: { x: 450, y: 900 } })).toBe(true)
    expect(store.adjustInsertedNodeUi(id, { position: { x: 450, y: 950 } })).toBe(true)
    store.undo()
    expect(store.draft).toEqual(graph())
    expect(store.canUndo).toBe(false)
    store.redo()
    expect(store.selectedNode!.position).toEqual({ x: 450, y: 950 })
    expect(store.canAdjustInsertedNodeUi(id)).toBe(false)
    expect(store.adjustInsertedNodeUi(id, { position: { x: 1, y: 1 } })).toBe(false)
    expect(store.selectedNode!.position).toEqual({ x: 450, y: 950 })
    store.updateNodeConfig(id, { label: 'typed' })
    expect(store.adjustInsertedNodeUi(id, { position: { x: 2, y: 2 } })).toBe(false)
  })

  it.each([1, 2])('preserves supplied layout when measuring a paste of %i node(s)', (count) => {
    const nodes = Array.from({ length: count }, (_, index): WorkflowNode => ({
      ...graph().nodes[1], id: `copy-${index}`, position: { x: 500, y: 10 + index * 20 },
    }))
    expect(store.applyPastedGraph({ nodes, edges: [] })).toBe(true)
    const pasted = cloneDocument(store.draft)
    for (const node of nodes) expect(store.canAdjustInsertedNodeUi(node.id)).toBe(false)
    for (const node of nodes) expect(store.adjustInsertedNodeUi(node.id, { position: { x: 900, y: 900 } })).toBe(false)
    expect(store.draft).toEqual(pasted)
    store.undo()
    expect(store.draft).toEqual(graph())
    store.redo()
    for (const node of nodes) expect(store.adjustInsertedNodeUi(node.id, { position: { x: 900, y: 900 } })).toBe(false)
    expect(store.draft).toEqual(pasted)
  })

  it('preserves imported overlapping layout through measurement and history replay', () => {
    const imported = {
      nodes: graph().nodes.map((node, index) => ({ ...node, id: `import-${index}`, position: { x: 25, y: 25 + index * 20 } })),
      edges: [],
    }
    expect(store.replaceGraph(imported)).toBe(true)
    for (const node of imported.nodes) expect(store.canAdjustInsertedNodeUi(node.id)).toBe(false)
    for (const node of imported.nodes) expect(store.adjustInsertedNodeUi(node.id, { position: { x: 800, y: 800 } })).toBe(false)
    expect(store.draft).toEqual(imported)
    store.undo()
    store.redo()
    for (const node of imported.nodes) expect(store.adjustInsertedNodeUi(node.id, { position: { x: 800, y: 800 } })).toBe(false)
    expect(store.draft).toEqual(imported)
  })

  it('does not save, validate, or run an unfinished pointer transaction', async () => {
    store.projectId = 'p1'
    store.audioId = 'a1'
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    store.beginHistoryTransaction()
    expect(store.historyTransactionActive).toBe(true)
    store.updateNodeUi('process', { position: { x: 100, y: 20 } })
    expect(await store.save()).toBeNull()
    expect(await store.validate()).toEqual([])
    expect(await store.startRun()).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
    store.cancelHistoryTransaction()
    expect(store.historyTransactionActive).toBe(false)
    expect(store.draft).toEqual(graph())
  })

  it('keeps boolean, select and resource changes as discrete operations from metadata', () => {
    const field = capabilities.nodes[1]!.fields[0]!
    store.capabilities = {
      ...structuredClone(capabilities), nodes: capabilities.nodes.map((node) => node.type === 'future_processor' ? {
        ...node, resourceInput: { resourceType: 'audio', configKey: 'label' }, fields: [
          field, { ...field, key: 'enabled', type: 'boolean', default: false },
          { ...field, key: 'choice', type: 'select', options: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] },
        ],
      } : node),
    }
    store.updateNodeConfig('process', { label: 'resource-one' })
    store.updateNodeConfig('process', { label: 'resource-two' })
    store.undo()
    expect(store.selectedNode!.config.label).toBe('resource-one')
    store.updateNodeConfig('process', { label: 'resource-one', enabled: true })
    store.updateNodeConfig('process', { label: 'resource-one', enabled: false })
    store.undo()
    expect(store.selectedNode!.config.enabled).toBe(true)
    store.updateNodeConfig('process', { ...store.selectedNode!.config, choice: 'a' })
    store.updateNodeConfig('process', { ...store.selectedNode!.config, choice: 'b' })
    store.undo()
    expect(store.selectedNode!.config.choice).toBe('a')
  })

  it('locks editing for active runs and never changes the run snapshot when replaying history', () => {
    store.updateNodeConfig('process', { label: 'edited' })
    store.run = { status: 'running', workflow: cloneDocument(store.draft) } as WorkflowRun
    const snapshot = cloneDocument(store.run.workflow)
    expect(store.undo()).toBe(false)
    expect(store.connect('process', 'source', 'audio', 'audio')).toBe(false)
    store.removeNode('process')
    expect(store.draft).toEqual(snapshot)
    store.run.status = 'completed'
    expect(store.undo()).toBe(true)
    expect(store.draft).toEqual(graph())
    expect(store.run.workflow).toEqual(snapshot)
    store.redo()
    store.updateNodeConfig('process', { label: 'another' })
    expect(store.run.workflow).toEqual(snapshot)
  })

  it('cancels transactions and ignores no-op and failed actions', () => {
    store.beginHistoryTransaction()
    store.updateNodeUi('process', { position: { x: 999, y: 20 } })
    store.selectNode('source')
    store.cancelHistoryTransaction()
    expect(store.draft).toEqual(graph())
    expect(store.selectedNodeIds).toEqual(['process'])
    store.beginHistoryTransaction()
    expect(store.commitHistoryTransaction()).toBe(false)
    store.updateNodeConfig('process', { label: 'original' })
    store.disconnect('missing')
    expect(store.connect('source', 'process', 'audio', 'audio')).toBe(false)
    expect(store.canUndo).toBe(false)
    expect(store.dirty).toBe(false)
  })

  it('drops redo after a new edit and caps history at 50 operations', () => {
    for (let index = 1; index <= 55; index++) store.updateNodeUi('process', { position: { x: index, y: 0 } })
    let steps = 0
    while (store.undo()) steps++
    expect(steps).toBe(50)
    expect(store.draft.nodes[1].position.x).toBe(5)
    store.updateNodeUi('process', { position: { x: 60, y: 0 } })
    expect(store.canRedo).toBe(false)
  })

  it('preserves history through save and compares against the successful save baseline', async () => {
    store.projectId = 'p1'
    store.updateNodeConfig('process', { label: 'saved' })
    const saved = documentBody(store.draft)
    vi.stubGlobal('fetch', vi.fn(async () => response(saved)))
    await store.save()
    expect(store.dirty).toBe(false)
    expect(store.canUndo).toBe(true)
    store.undo()
    expect(store.dirty).toBe(true)
    store.redo()
    expect(store.dirty).toBe(false)
    store.updateNodeConfig('process', { label: 'later' })
    store.undo()
    expect(store.dirty).toBe(false)
  })

  it('retains old semantic dirty comparison for config defaults and edge identities', () => {
    store.updateNodeConfig('process', {})
    expect(store.dirty).toBe(false)
    store.setDraft({ nodes: graph().nodes, edges: [{ ...graph().edges[0], id: 'different-id' }] })
    expect(store.dirty).toBe(false)
    expect(store.canUndo).toBe(true)
  })

  it('keeps selection, viewport and measured content outside graph history', () => {
    store.selectNode('source')
    store.selectNode('process', 'add')
    store.selectNode('source', 'toggle')
    expect(store.selectedNodeIds).toEqual(['process'])
    store.selectedNodeId = 'source'
    expect(store.selectedNodeIds).toEqual(['source'])
    store.setSelection(['missing', 'process', 'process'])
    expect(store.selectedNodeIds).toEqual(['process'])
    store.zoom = 2
    store.pan = { x: 1, y: 2 }
    store.setNodeMinimum('process', { width: 600, height: 500 })
    expect(store.canUndo).toBe(false)
    expect(store.dirty).toBe(false)
  })

  it('deletes removable selection and attached edges in one step, preserving protected nodes', () => {
    store.setSelection(['source', 'process'])
    expect(store.deleteSelection()).toBe(true)
    expect(store.draft.nodes.map((node) => node.id)).toEqual(['source'])
    expect(store.draft.edges).toEqual([])
    expect(store.selectedNodeIds).toEqual(['source'])
    expect(store.error).toBeTruthy()
    store.undo()
    expect(store.draft).toEqual(graph())
    expect(store.selectedNodeIds).toEqual(['source', 'process'])
    expect(store.canUndo).toBe(false)
  })

  it('applies paste and import atomically with independent history steps and selection', () => {
    const copied: WorkflowNode = { ...graph().nodes[1], id: 'copy', position: { x: 600, y: 100 } }
    expect(store.applyPastedGraph({ nodes: [copied], edges: [] })).toBe(true)
    expect(store.selectedNodeIds).toEqual(['copy'])
    expect(store.draft.nodes).toHaveLength(3)
    expect(store.replaceGraph({ nodes: [copied], edges: [] })).toBe(true)
    expect(store.draft.nodes).toHaveLength(1)
    store.undo()
    expect(store.draft.nodes).toHaveLength(3)
    store.undo()
    expect(store.draft).toEqual(graph())
    expect(store.selectedNodeIds).toEqual(['process'])
    store.redo()
    expect(store.selectedNodeIds).toEqual(['copy'])
  })

  it('rejects invalid bulk edits without altering graph, selection, dirty, or history', () => {
    const before = cloneDocument(store.draft)
    expect(store.applyPastedGraph({ nodes: [graph().nodes[0]], edges: [] })).toBe(false)
    expect(store.replaceGraph({ nodes: [{ ...graph().nodes[1], type: 'unknown' }], edges: [] })).toBe(false)
    expect(store.draft).toEqual(before)
    expect(store.selectedNodeIds).toEqual(['process'])
    expect(store.dirty).toBe(false)
    expect(store.canUndo).toBe(false)
  })

  it.each(['loading', 'saving', 'validating', 'resourceUploading', 'starting', 'cancelling'] as const)('locks every editing API while %s', (flag) => {
    store.updateNodeConfig('process', { label: 'changed' })
    const before = cloneDocument(store.draft)
    store[flag] = true
    store.addNode('future_processor')
    store.removeNode('process')
    store.updateNodeConfig('process', { label: 'blocked' })
    store.updateNodeUi('process', { size: { width: 200, height: 200 } })
    store.disconnect('original-edge')
    store.deleteSelection()
    store.setDraft(graph())
    expect(store.undo()).toBe(false)
    expect(store.redo()).toBe(false)
    expect(store.beginHistoryTransaction()).toBe(false)
    expect(store.applyPastedGraph({ nodes: [], edges: [] })).toBe(false)
    expect(store.replaceGraph(graph())).toBe(false)
    expect(store.draft).toEqual(before)
    store[flag] = false
    expect(store.canUndo).toBe(true)
  })

  it('clears local history on discard, leave and successful reload', async () => {
    store.updateNodeConfig('process', { label: 'one' })
    store.discardDraft()
    expect(store.draft).toEqual(graph())
    expect(store.canUndo).toBe(false)
    store.updateNodeConfig('process', { label: 'two' })
    store.leaveEditor()
    store.beginDraftEditing()
    expect(store.canUndo).toBe(false)
    store.updateNodeConfig('process', { label: 'three' })
    vi.stubGlobal('fetch', vi.fn(async () => response(documentBody())))
    await store.load('p1', null)
    expect(store.canUndo).toBe(false)
    expect(store.canRedo).toBe(false)
    expect(store.dirty).toBe(false)
  })
})
