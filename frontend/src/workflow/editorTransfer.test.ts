import { fieldFixture } from './fixtures'
import { describe, expect, it, vi } from 'vitest'
import type { WorkflowDocumentInput, WorkflowNode } from '../stores/workflow'
import type { Capabilities, CapabilityField, CapabilityNode } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'
import {
  copySelection, parseSelection, parseWorkflowFile, preparePaste, serializeWorkflow,
  TransferError, validateTransferGraph, WORKFLOW_TRANSFER_MAX_BYTES,
} from './editorTransfer'

const field = (key: string, type: CapabilityField['type'], extra: Partial<CapabilityField> = {}): CapabilityField => (fieldFixture({
  key, type, inputPort: null, required: false, default: null, minimum: null, maximum: null,
  minLength: null, maxLength: null, pattern: null, validator: null, options: [], label: key, description: '', ...extra,
}))
const kind = (type: string, extra: Partial<CapabilityNode> = {}): CapabilityNode => ({
  type, label: type, addLabel: type, idPrefix: type, inputPorts: [], outputPorts: [], terminal: false,
  isSource: false, isParameterSource: false, maxInstances: null, requiredInbound: 0, requiredInputPorts: [],
  removable: true, configKind: type, fields: [], positionX: null, ...extra,
})
const capabilities: Capabilities = {
  schemaVersion: '1.5', nodeTypes: ['media_root', 'custom_effect', 'custom_sink', 'numeric_root'],
  nodes: [
    kind('media_root', { isSource: true, removable: false, maxInstances: 1, outputPorts: ['original_voice'], fields: [field('media_ref', 'string')], resourceInput: { resourceType: 'audio', configKey: 'media_ref' } }),
    kind('custom_effect', { inputPorts: ['voice_input', 'gain_control'], outputPorts: ['processed_voice'], requiredInbound: 1, requiredInputPorts: ['voice_input'], fields: [field('strength', 'number', { default: 2, inputPort: 'gain_control' }), field('settings', 'json')] }),
    kind('custom_sink', { inputPorts: ['listen'], terminal: true, requiredInbound: 1, requiredInputPorts: ['listen'], fields: [field('filename', 'string', { required: true })] }),
    kind('numeric_root', { isParameterSource: true, outputPorts: ['integer_value'], fields: [field('scalar', 'integer')] }),
  ],
  ports: [
    ...['original_voice', 'voice_input', 'processed_voice', 'listen'].map((id) => ({ id, typeId: 'audio', label: id, capabilityRequirements: [] })),
    { id: 'gain_control', typeId: 'number', label: 'Gain', capabilityRequirements: [] },
    { id: 'integer_value', typeId: 'integer', label: 'Integer', capabilityRequirements: [] },
  ],
  dataTypes: [
    { id: 'audio', label: 'Audio', parentTypeId: null, capabilities: [] },
    { id: 'number', label: 'Number', parentTypeId: null, capabilities: [] },
    { id: 'integer', label: 'Integer', parentTypeId: 'number', capabilities: [] },
  ],
  constraints: { maxNodes: 64, maxEdges: 64, minSources: 1, maxSources: 1, minTerminals: 1 },
  defaultGraph: { nodes: [], edges: [] }, asr: { providers: [] }, export: { formats: [], namingModes: [] },
}
const registry = createNodeRegistry(capabilities)
const node = (id: string, type = 'custom_effect', x = 100, y = 200): WorkflowNode => ({ id, type, config: {}, position: { x, y } })
const edge = (id: string, source: string, source_port: string, target: string, target_port: string) => ({ id, source, source_port, target, target_port })
function graph(): WorkflowDocumentInput {
  return {
    nodes: [
      { ...node('media', 'media_root', -100, 50), config: { media_ref: 'project-audio-id' } },
      { ...node('effect', 'custom_effect', 240, 50), config: { strength: 2.5, settings: { nested: ['原始输入', true, null, 3] } }, size: { width: 350.5, height: 280 } },
      { ...node('sink', 'custom_sink', 650, 50), config: { filename: '字幕' }, size: null },
      { ...node('value', 'numeric_root', 240, -120), config: { scalar: 7 } },
    ],
    edges: [
      edge('voice', 'media', 'original_voice', 'effect', 'voice_input'),
      edge('parameter', 'value', 'integer_value', 'effect', 'gain_control'),
      edge('output', 'effect', 'processed_voice', 'sink', 'listen'),
    ],
  }
}
function file(value: unknown, format = 'voicetransl.workflow'): string {
  return JSON.stringify({ format, schema_version: '4.0', ...(value as object) })
}
function expectCode(action: () => unknown, code: string) {
  try { action(); throw new Error('Expected TransferError') } catch (error) {
    expect(error).toBeInstanceOf(TransferError)
    expect((error as TransferError).code).toBe(`workflow.transfer${code}`)
  }
}

describe('portable workflow transfer', () => {
  it('round trips typed extension ports, parameters, fallback values and exact dimensions without mutation', () => {
    const original = graph()
    const before = structuredClone(original)
    const text = serializeWorkflow(original, registry)
    const result = parseWorkflowFile(text, registry)
    const expected = structuredClone(original)
    expected.nodes[0].config = {}
    expect(JSON.parse(text).format).toBe('voicetransl.workflow')
    expect(JSON.parse(text).schema_version).toBe('4.0')
    expect(result).toEqual(expected)
    expect(result.nodes[1].config.strength).toBe(2.5)
    expect(original).toEqual(before)
    result.nodes[1].config.strength = 99
    expect(original.nodes[1].config.strength).toBe(2.5)
  })

  it('excludes document identity, history, revisions and viewport from exports', () => {
    const document = { ...graph(), project_id: 'private-project', revision: 17, history: ['private'], viewport: { x: 5 }, runs: [] }
    const exported = JSON.parse(serializeWorkflow(document, registry))
    expect(Object.keys(exported)).toEqual(['format', 'schema_version', 'nodes', 'edges'])
    expect(JSON.stringify(exported)).not.toContain('private')
  })

  it('strips metadata-declared resource binding on import and copy, including resource URL strings', () => {
    const source = graph()
    source.nodes[0].config.media_ref = 'file:///C:/private/audio.wav'
    expect(parseWorkflowFile(file(source), registry).nodes[0].config).toEqual({})
    expect(parseSelection(copySelection(source, ['media'], registry), registry).nodes[0].config).toEqual({})
    expect(validateTransferGraph(source, registry).nodes[0].config.media_ref).toBe('file:///C:/private/audio.wav')
  })

  it('preserves ordinary strings as data without evaluating code', () => {
    const source = graph()
    const payload = 'globalThis.compromised = true; import("https://example.test/code.js")'
    source.nodes[2].config.filename = payload
    expect(parseWorkflowFile(serializeWorkflow(source, registry), registry).nodes[2].config.filename).toBe(payload)
    expect((globalThis as Record<string, unknown>).compromised).toBeUndefined()
  })

  it('accepts unfinished drafts and repairable field values without injecting defaults', () => {
    const source = { nodes: [{ ...node('draft'), config: { strength: 'typing-', settings: null } }, node('sink', 'custom_sink')], edges: [] }
    expect(parseWorkflowFile(file(source), registry)).toEqual(source)
    expect(parseWorkflowFile(file({ nodes: [], edges: [] }), registry)).toEqual({ nodes: [], edges: [] })
    expect(registry.validateConfig('custom_effect', source.nodes[0].config)).not.toEqual([])
  })

  it('keeps legacy default passthrough graphs without size unchanged', () => {
    const legacy = { nodes: [node('input', 'media_root', 80, 120), node('output', 'custom_sink', 420, 120)], edges: [edge('input-output', 'input', 'original_voice', 'output', 'listen')] }
    expect(parseWorkflowFile(serializeWorkflow(legacy, registry), registry)).toEqual(legacy)
    expect(parseWorkflowFile(serializeWorkflow(legacy, registry), registry).nodes[0]).not.toHaveProperty('size')
  })

  it('strictly separates selection and workflow formats and rejects unknown versions', () => {
    expectCode(() => parseSelection(serializeWorkflow(graph(), registry), registry), 'Format')
    expectCode(() => parseWorkflowFile(copySelection(graph(), ['effect'], registry), registry), 'Format')
    for (const schema_version of ['0.9', '1.1', 1, null]) {
      expectCode(() => parseWorkflowFile(JSON.stringify({ ...JSON.parse(file(graph())), schema_version }), registry), 'Version')
    }
  })

  it('checks UTF-8 size before parsing, with exactly 4 MiB permitted', () => {
    const small = file({ nodes: [], edges: [] })
    expect(parseWorkflowFile(small + ' '.repeat(WORKFLOW_TRANSFER_MAX_BYTES - small.length), registry)).toEqual({ nodes: [], edges: [] })
    expectCode(() => parseWorkflowFile('x'.repeat(WORKFLOW_TRANSFER_MAX_BYTES + 1), registry), 'TooLarge')
    expectCode(() => parseWorkflowFile('中'.repeat(Math.floor(WORKFLOW_TRANSFER_MAX_BYTES / 3) + 1), registry), 'TooLarge')
    const source = graph()
    source.nodes[2].config.filename = '中'.repeat(1_500_000)
    expectCode(() => serializeWorkflow(source, registry), 'TooLarge')
    expectCode(() => parseWorkflowFile('{broken', registry), 'InvalidJson')
  })

  it.each(['__proto__', 'constructor', 'prototype'])('rejects dangerous object key %s at any depth', (key) => {
    const source = graph()
    source.nodes[1].config.settings = JSON.parse(`{"nested":{"${key}":{"polluted":true}}}`)
    expectCode(() => parseWorkflowFile(file(source), registry), 'UnsafeData')
    expectCode(() => serializeWorkflow(source, registry), 'UnsafeData')
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  it.each([undefined, NaN, Infinity, -Infinity, BigInt(1), () => 1, new Date(), Symbol('value')])('rejects non-JSON config data %# without coercion', (value) => {
    const source = graph()
    source.nodes[1].config.settings = value
    expectCode(() => serializeWorkflow(source, registry), 'UnsafeData')
  })

  it('rejects cycles, sparse arrays, accessors and hidden toJSON without executing them', () => {
    const source = graph()
    const cycle: Record<string, unknown> = {}; cycle.nested = cycle
    const getter = vi.fn(() => 'executed')
    const accessor = Object.defineProperty({}, 'data', { enumerable: true, get: getter })
    const toJSON = vi.fn(() => ({}))
    const hidden = Object.defineProperty({}, 'toJSON', { value: toJSON })
    class CustomArray extends Array { toJSON = toJSON }
    const inheritedArray = Object.setPrototypeOf([], { toJSON })
    for (const value of [cycle, Array(2), accessor, hidden, new CustomArray(), inheritedArray]) {
      source.nodes[1].config.settings = value
      expectCode(() => serializeWorkflow(source, registry), 'UnsafeData')
    }
    expect(getter).not.toHaveBeenCalled()
    expect(toJSON).not.toHaveBeenCalled()
  })

  it.each([
    ['top unknown field', (g: any) => { g.history = [] }, 'Structure'],
    ['node unknown field', (g: any) => { g.nodes[0].unexpected = true }, 'Structure'],
    ['config unknown field', (g: any) => { g.nodes[0].config.api_key = 'secret' }, 'UnknownField'],
    ['missing id', (g: any) => { delete g.nodes[0].id }, 'Structure'],
    ['invalid id', (g: any) => { g.nodes[0].id = '../node' }, 'Structure'],
    ['unknown node', (g: any) => { g.nodes[0].type = 'unregistered' }, 'UnknownNode'],
    ['non-string node type', (g: any) => { g.nodes[0].type = { toString: 'untrusted' } }, 'Structure'],
    ['duplicate node', (g: any) => { g.nodes[1].id = g.nodes[0].id }, 'DuplicateId'],
    ['duplicate edge', (g: any) => { g.edges[1].id = g.edges[0].id }, 'DuplicateId'],
    ['missing port', (g: any) => { delete g.edges[0].source_port }, 'Structure'],
    ['unknown source', (g: any) => { g.edges[0].source = 'missing' }, 'InvalidEdge'],
    ['unknown port', (g: any) => { g.edges[0].source_port = 'audio' }, 'InvalidEdge'],
    ['wrong direction', (g: any) => { g.edges[0].target_port = 'processed_voice' }, 'InvalidEdge'],
    ['incompatible type', (g: any) => { g.edges[0].target_port = 'gain_control'; g.edges.splice(1, 1) }, 'InvalidEdge'],
    ['duplicate target', (g: any) => { g.edges.push({ ...g.edges[0], id: 'duplicate_input' }) }, 'InvalidEdge'],
    ['self edge', (g: any) => { g.edges[0].source = 'effect'; g.edges[0].source_port = 'processed_voice' }, 'InvalidEdge'],
    ['position unknown field', (g: any) => { g.nodes[0].position.z = 1 }, 'Structure'],
    ['missing coordinate', (g: any) => { delete g.nodes[0].position.y }, 'Structure'],
    ['string coordinate', (g: any) => { g.nodes[0].position.x = '1' }, 'InvalidLayout'],
    ['coordinate over bound', (g: any) => { g.nodes[0].position.x = 1_000_001 }, 'InvalidLayout'],
    ['zero dimension', (g: any) => { g.nodes[1].size.width = 0 }, 'InvalidLayout'],
    ['negative dimension', (g: any) => { g.nodes[1].size.width = -1 }, 'InvalidLayout'],
    ['dimension over bound', (g: any) => { g.nodes[1].size.height = 1_000_001 }, 'InvalidLayout'],
  ])('rejects %s instead of repairing same-version data', (_name, mutate, code) => {
    const source = graph()
    mutate(source)
    expectCode(() => parseWorkflowFile(file(source), registry), code)
  })

  it('rejects dependency cycles while accepting disconnected parameter roots', () => {
    const source = { nodes: [node('a'), node('b')], edges: [edge('a-b', 'a', 'processed_voice', 'b', 'voice_input'), edge('b-a', 'b', 'processed_voice', 'a', 'voice_input')] }
    expectCode(() => parseWorkflowFile(file(source), registry), 'Cycle')
    expect(validateTransferGraph({ nodes: [node('parameter', 'numeric_root')], edges: [] }, registry).nodes).toHaveLength(1)
  })

  it('derives all upper limits from the registry and treats parameter roots separately from resource sources', () => {
    const capped = structuredClone(capabilities)
    capped.constraints.maxNodes = 1
    expectCode(() => parseWorkflowFile(file(graph()), createNodeRegistry(capped)), 'NodeLimit')
    capped.constraints.maxNodes = 64; capped.constraints.maxEdges = 1
    expectCode(() => parseWorkflowFile(file(graph()), createNodeRegistry(capped)), 'EdgeLimit')
    capped.constraints.maxEdges = 64; capped.constraints.maxSources = 0
    expectCode(() => parseWorkflowFile(file(graph()), createNodeRegistry(capped)), 'SourceLimit')
    capped.constraints.maxSources = 1; capped.nodes.find((entry) => entry.type === 'custom_effect')!.maxInstances = 1
    const multiple = { nodes: [node('a'), node('b')], edges: [] }
    expectCode(() => parseWorkflowFile(file(multiple), createNodeRegistry(capped)), 'InstanceLimit')
    expect(validateTransferGraph({ nodes: [node('a', 'numeric_root'), node('b', 'numeric_root')], edges: [] }, registry).nodes).toHaveLength(2)
  })
})

describe('selection copy and atomic paste', () => {
  it('copies only selected nodes and fully internal named data and parameter edges', () => {
    const original = graph()
    const before = structuredClone(original)
    const copied = parseSelection(copySelection(original, ['value', 'effect', 'sink'], registry), registry)
    expect(copied.nodes.map((entry) => entry.id)).toEqual(['effect', 'sink', 'value'])
    expect(copied.edges.map((entry) => entry.id)).toEqual(['parameter', 'output'])
    expect(original).toEqual(before)
    expectCode(() => copySelection(original, [], registry), 'EmptySelection')
    expectCode(() => copySelection(original, ['missing'], registry), 'Structure')
    expectCode(() => parseSelection(file({ nodes: [], edges: [] }, 'voicetransl.selection'), registry), 'EmptySelection')
  })

  it('remaps every ID and connection, keeps layout/size/fallback, and returns only fresh additions', () => {
    const original = graph()
    const before = structuredClone(original)
    const selected = ['effect', 'sink', 'value']
    const text = copySelection(original, selected, registry)
    const pasted = preparePaste(text, original, registry, { x: 1000, y: 2000 })
    const oldIds = new Set([...original.nodes, ...original.edges].map((entry) => entry.id))
    expect([...pasted.nodes, ...pasted.edges].every((entry) => !oldIds.has(entry.id))).toBe(true)
    expect(new Set([...pasted.nodes, ...pasted.edges].map((entry) => entry.id)).size).toBe(5)
    expect(pasted.nodes).toHaveLength(3)
    expect(pasted.nodes[0].position).toEqual({ x: 1000, y: 2170 })
    expect(pasted.nodes[1].position).toEqual({ x: 1410, y: 2170 })
    expect(pasted.nodes[2].position).toEqual({ x: 1000, y: 2000 })
    expect(pasted.nodes[0].size).toEqual(original.nodes[1].size)
    expect(pasted.nodes[0].config).toEqual(original.nodes[1].config)
    expect(pasted.edges[0]).toMatchObject({ source: pasted.nodes[2].id, source_port: 'integer_value', target: pasted.nodes[0].id, target_port: 'gain_control' })
    expect(pasted.edges[1]).toMatchObject({ source: pasted.nodes[0].id, source_port: 'processed_voice', target: pasted.nodes[1].id, target_port: 'listen' })
    expect(original).toEqual(before)
    pasted.nodes[0].config.strength = 100
    expect(original).toEqual(before)
    expect(JSON.parse(text).nodes[0].config.strength).toBe(2.5)
  })

  it('allocates IDs independently on repeat pastes and avoids all old node/edge identifiers', () => {
    const original = graph()
    const text = copySelection(original, ['effect'], registry)
    const first = preparePaste(text, original, registry)
    const second = preparePaste(text, { nodes: [...original.nodes, ...first.nodes], edges: [...original.edges, ...first.edges] }, registry)
    expect(second.nodes[0].id).not.toBe(first.nodes[0].id)
    expect(first.nodes[0].position).toEqual({ x: 272, y: 82 })
    const oldSelectionId = file({ nodes: [node('paste_node_1')], edges: [] }, 'voicetransl.selection')
    expect(preparePaste(oldSelectionId, { nodes: [], edges: [] }, registry).nodes[0].id).not.toBe('paste_node_1')
  })

  it('refuses a whole copied source graph rather than silently skipping the unique resource source', () => {
    const original = graph()
    const before = structuredClone(original)
    const text = copySelection(original, original.nodes.map((entry) => entry.id), registry)
    expectCode(() => preparePaste(text, original, registry), 'InstanceLimit')
    expect(original).toEqual(before)
    const pastedIntoEmpty = preparePaste(text, { nodes: [], edges: [] }, registry)
    expect(pastedIntoEmpty.nodes).toHaveLength(4)
    expect(pastedIntoEmpty.nodes[0].config).toEqual({})
  })

  it('checks merged graph node/edge/source/type limits before returning any nodes', () => {
    const original = graph()
    const before = structuredClone(original)
    const text = copySelection(original, ['effect', 'sink'], registry)
    const capped = structuredClone(capabilities)
    capped.constraints.maxNodes = original.nodes.length + 1
    expectCode(() => preparePaste(text, original, createNodeRegistry(capped)), 'NodeLimit')
    capped.constraints.maxNodes = 64; capped.constraints.maxEdges = original.edges.length
    expectCode(() => preparePaste(text, original, createNodeRegistry(capped)), 'EdgeLimit')
    capped.constraints.maxEdges = 64; capped.nodes[0].maxInstances = null
    expectCode(() => preparePaste(copySelection(original, ['media'], registry), original, createNodeRegistry(capped)), 'SourceLimit')
    capped.nodes.find((entry) => entry.type === 'custom_effect')!.maxInstances = 1
    expectCode(() => preparePaste(text, original, createNodeRegistry(capped)), 'InstanceLimit')
    expect(original).toEqual(before)
  })

  it('clamps the entire pasted layout to bounds while keeping relative positions', () => {
    const source = { nodes: [node('a', 'custom_effect', 999_980, -999_990), node('b', 'custom_effect', 999_995, -999_970)], edges: [] }
    const text = copySelection(source, ['a', 'b'], registry)
    for (const point of [undefined, { x: 2_000_000, y: -2_000_000 }]) {
      const pasted = preparePaste(text, { nodes: [], edges: [] }, registry, point)
      expect(pasted.nodes[1].position.x - pasted.nodes[0].position.x).toBe(15)
      expect(pasted.nodes[1].position.y - pasted.nodes[0].position.y).toBe(20)
      for (const entry of pasted.nodes) {
        expect(Math.abs(entry.position.x)).toBeLessThanOrEqual(1_000_000)
        expect(Math.abs(entry.position.y)).toBeLessThanOrEqual(1_000_000)
      }
    }
    expectCode(() => preparePaste(text, { nodes: [], edges: [] }, registry, { x: Infinity, y: 1 }), 'InvalidLayout')
  })
})
