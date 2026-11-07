import type { WorkflowDocumentInput, WorkflowEdge, WorkflowNode } from '../stores/workflow'
import type { NodeRegistry } from './nodeRegistry'

export const WORKFLOW_TRANSFER_MAX_BYTES = 4 * 1024 * 1024
const SCHEMA_VERSION = '1.0'
const LAYOUT_BOUND = 1_000_000
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/
const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])

export type TransferErrorCode = `workflow.transfer${
  'TooLarge' | 'InvalidJson' | 'Format' | 'Version' | 'Structure' | 'UnsafeData' |
  'UnknownNode' | 'UnknownField' | 'InvalidLayout' | 'DuplicateId' | 'InvalidEdge' |
  'Cycle' | 'NodeLimit' | 'EdgeLimit' | 'SourceLimit' | 'InstanceLimit' | 'EmptySelection'}`

/** A stable, localizable error; the transport layer never creates display text. */
export class TransferError extends Error {
  constructor(public readonly code: TransferErrorCode, public readonly params?: Record<string, string | number>) {
    super(code)
    this.name = 'TransferError'
  }
}

function fail(code: TransferErrorCode, p0?: string | number): never {
  throw new TransferError(code, p0 === undefined ? undefined : { p0 })
}

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail('workflow.transferStructure')
  const prototype = Object.getPrototypeOf(value)
  if (prototype !== Object.prototype && prototype !== null) fail('workflow.transferUnsafeData')
  return value as Record<string, unknown>
}

/** Check before stringify: JSON must never silently discard or coerce data. */
function checkJson(value: unknown): void {
  const pending: Array<{ value: unknown; leave?: boolean }> = [{ value }]
  const ancestors = new Set<object>()
  while (pending.length) {
    const item = pending.pop()!
    const current = item.value
    if (item.leave) { ancestors.delete(current as object); continue }
    if (current === null || typeof current === 'string' || typeof current === 'boolean') continue
    if (typeof current === 'number') {
      if (!Number.isFinite(current)) fail('workflow.transferUnsafeData')
      continue
    }
    if (typeof current !== 'object') fail('workflow.transferUnsafeData')
    if (ancestors.has(current)) fail('workflow.transferUnsafeData')
    if (Array.isArray(current)) {
      if (Object.getPrototypeOf(current) !== Array.prototype) fail('workflow.transferUnsafeData')
    } else object(current)
    ancestors.add(current)
    pending.push({ value: current, leave: true })
    if (Object.getOwnPropertySymbols(current).length) fail('workflow.transferUnsafeData')
    const keys = Object.keys(current)
    if (Object.getOwnPropertyNames(current).length !== keys.length + (Array.isArray(current) ? 1 : 0)) fail('workflow.transferUnsafeData')
    // Sparse arrays and extra properties are not losslessly representable as JSON arrays.
    if (Array.isArray(current) && (keys.length !== current.length || keys.some((key, index) => key !== String(index)))) fail('workflow.transferUnsafeData')
    for (const key of keys) {
      if (UNSAFE_KEYS.has(key)) fail('workflow.transferUnsafeData')
      const descriptor = Object.getOwnPropertyDescriptor(current, key)!
      if (!('value' in descriptor)) fail('workflow.transferUnsafeData')
      pending.push({ value: descriptor.value })
    }
  }
}

function exactKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): void {
  if (required.some((key) => !Object.prototype.hasOwnProperty.call(value, key)) || Object.keys(value).some((key) => !required.includes(key) && !optional.includes(key))) fail('workflow.transferStructure')
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail('workflow.transferStructure')
  return value
}

function coordinate(value: unknown, dimension = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value > LAYOUT_BOUND || (dimension ? value <= 0 : value < -LAYOUT_BOUND)) fail('workflow.transferInvalidLayout')
  return value
}

function cloneJson<T>(value: T): T {
  try { return JSON.parse(JSON.stringify(value)) as T } catch { return fail('workflow.transferUnsafeData') }
}

/**
 * Structural draft validation shared by import and atomic editor mutations.
 * Missing connections and invalid field values remain the save/run validator's job.
 * This function preserves resource bindings; portable transfers strip them separately.
 */
export function validateTransferGraph(graph: unknown, registry: NodeRegistry): WorkflowDocumentInput {
  checkJson(graph)
  const root = object(graph)
  exactKeys(root, ['nodes', 'edges'])
  if (!Array.isArray(root.nodes) || !Array.isArray(root.edges)) fail('workflow.transferStructure')
  const limits = registry.capabilities.constraints
  if (root.nodes.length > limits.maxNodes) fail('workflow.transferNodeLimit', limits.maxNodes)
  if (root.edges.length > limits.maxEdges) fail('workflow.transferEdgeLimit', limits.maxEdges)
  const nodes = new Map<string, WorkflowNode>()
  const counts = new Map<string, number>()
  let sources = 0
  for (const raw of root.nodes) {
    const node = object(raw)
    exactKeys(node, ['id', 'type', 'config', 'position'], ['size'])
    const id = identifier(node.id)
    if (nodes.has(id)) fail('workflow.transferDuplicateId', id)
    if (typeof node.type !== 'string') fail('workflow.transferStructure')
    if (!registry.has(node.type)) fail('workflow.transferUnknownNode', node.type)
    const definition = registry.kind(node.type)!
    const config = object(node.config)
    const allowed = new Set(definition.fields.map((field) => field.key))
    for (const key of Object.keys(config)) if (!allowed.has(key)) fail('workflow.transferUnknownField', key)
    const position = object(node.position)
    exactKeys(position, ['x', 'y'])
    coordinate(position.x)
    coordinate(position.y)
    if (node.size !== undefined && node.size !== null) {
      const size = object(node.size)
      exactKeys(size, ['width', 'height'])
      coordinate(size.width, true)
      coordinate(size.height, true)
    }
    const count = (counts.get(node.type) || 0) + 1
    counts.set(node.type, count)
    if (definition.maxInstances !== null && count > definition.maxInstances) fail('workflow.transferInstanceLimit', node.type)
    if (definition.isSource) sources++
    nodes.set(id, raw as WorkflowNode)
  }
  if (sources > limits.maxSources) fail('workflow.transferSourceLimit', limits.maxSources)
  const edgeIds = new Set<string>()
  const targetInputs = new Map<string, Set<string>>()
  const outgoing = new Map<string, string[]>()
  const inbound = new Map([...nodes.keys()].map((id) => [id, 0]))
  for (const raw of root.edges) {
    const edge = object(raw)
    exactKeys(edge, ['id', 'source', 'source_port', 'target', 'target_port'])
    const id = identifier(edge.id)
    if (edgeIds.has(id)) fail('workflow.transferDuplicateId', id)
    edgeIds.add(id)
    const source = nodes.get(identifier(edge.source))
    const target = nodes.get(identifier(edge.target))
    if (!source || !target || source.id === target.id || typeof edge.source_port !== 'string' || typeof edge.target_port !== 'string'
      || !registry.outputPortsForNode(source.type).includes(edge.source_port)
      || !registry.inputPortsForNode(target.type).includes(edge.target_port)
      || !registry.portsCompatible(edge.source_port, edge.target_port)) fail('workflow.transferInvalidEdge', id)
    const occupied = targetInputs.get(target.id) || new Set<string>()
    if (occupied.has(edge.target_port)) fail('workflow.transferInvalidEdge', id)
    occupied.add(edge.target_port)
    targetInputs.set(target.id, occupied)
    outgoing.set(source.id, [...(outgoing.get(source.id) || []), target.id])
    inbound.set(target.id, inbound.get(target.id)! + 1)
  }
  const ready = [...inbound].filter(([, count]) => count === 0).map(([id]) => id)
  let visited = 0
  while (ready.length) {
    const id = ready.pop()!
    visited++
    for (const target of outgoing.get(id) || []) {
      const count = inbound.get(target)! - 1
      inbound.set(target, count)
      if (count === 0) ready.push(target)
    }
  }
  if (visited !== nodes.size) fail('workflow.transferCycle')
  return cloneJson(graph) as WorkflowDocumentInput
}

function portable(graph: WorkflowDocumentInput, registry: NodeRegistry): WorkflowDocumentInput {
  const result = validateTransferGraph({ nodes: graph.nodes, edges: graph.edges }, registry)
  for (const node of result.nodes) {
    const resource = registry.kind(node.type)?.resourceInput
    if (resource) { delete node.config[resource.configKey]; if (resource.collectionKey) delete node.config[resource.collectionKey] }
  }
  return result
}

function stringify(graph: WorkflowDocumentInput, registry: NodeRegistry, format: string): string {
  const text = JSON.stringify({ format, schema_version: SCHEMA_VERSION, ...portable(graph, registry) }, null, 2)
  checkSize(text)
  return text
}

function checkSize(text: string): void {
  if (typeof text !== 'string') fail('workflow.transferInvalidJson')
  // String length avoids allocating an encoder buffer for obviously oversized input.
  if (text.length > WORKFLOW_TRANSFER_MAX_BYTES || new TextEncoder().encode(text).byteLength > WORKFLOW_TRANSFER_MAX_BYTES) fail('workflow.transferTooLarge')
}

function parse(text: string, registry: NodeRegistry, format: string): WorkflowDocumentInput {
  checkSize(text)
  let raw: unknown
  try { raw = JSON.parse(text) } catch { return fail('workflow.transferInvalidJson') }
  checkJson(raw)
  const root = object(raw)
  exactKeys(root, ['format', 'schema_version', 'nodes', 'edges'])
  if (root.format !== format) fail('workflow.transferFormat')
  if (root.schema_version !== SCHEMA_VERSION) fail('workflow.transferVersion')
  return portable({ nodes: root.nodes as WorkflowNode[], edges: root.edges as WorkflowEdge[] }, registry)
}

export function serializeWorkflow(graph: WorkflowDocumentInput, registry: NodeRegistry): string {
  return stringify(graph, registry, 'voicetransl.workflow')
}

export function parseWorkflowFile(text: string, registry: NodeRegistry): WorkflowDocumentInput {
  return parse(text, registry, 'voicetransl.workflow')
}

export function copySelection(graph: WorkflowDocumentInput, ids: readonly string[], registry: NodeRegistry): string {
  const selected = new Set(ids)
  if (!selected.size) fail('workflow.transferEmptySelection')
  const nodes = graph.nodes.filter((node) => selected.has(node.id))
  if (nodes.length !== selected.size) fail('workflow.transferStructure')
  const edges = graph.edges.filter((edge) => selected.has(edge.source) && selected.has(edge.target))
  return stringify({ nodes, edges }, registry, 'voicetransl.selection')
}

export function parseSelection(text: string, registry: NodeRegistry): WorkflowDocumentInput {
  const graph = parse(text, registry, 'voicetransl.selection')
  if (!graph.nodes.length) fail('workflow.transferEmptySelection')
  return graph
}

/** Allocate all IDs and validate the complete merged candidate before returning additions. */
export function preparePaste(selectionText: string, existingGraph: WorkflowDocumentInput, registry: NodeRegistry, point?: { x: number; y: number }): WorkflowDocumentInput {
  const selection = parseSelection(selectionText, registry)
  const existing = validateTransferGraph({ nodes: existingGraph.nodes, edges: existingGraph.edges }, registry)
  const used = new Set([...existing.nodes, ...existing.edges, ...selection.nodes, ...selection.edges].map((item) => item.id))
  let serial = 1
  const allocate = (prefix: string): string => {
    let id: string
    do { id = `${prefix}_${serial++}` } while (used.has(id))
    used.add(id)
    return id
  }
  const mapping = new Map(selection.nodes.map((node) => [node.id, allocate('paste_node')]))
  const minX = Math.min(...selection.nodes.map((node) => node.position.x))
  const minY = Math.min(...selection.nodes.map((node) => node.position.y))
  const maxX = Math.max(...selection.nodes.map((node) => node.position.x))
  const maxY = Math.max(...selection.nodes.map((node) => node.position.y))
  if (point && (!Number.isFinite(point.x) || !Number.isFinite(point.y))) fail('workflow.transferInvalidLayout')
  const offsetX = Math.max(-LAYOUT_BOUND - minX, Math.min(LAYOUT_BOUND - maxX, point ? point.x - minX : 32))
  const offsetY = Math.max(-LAYOUT_BOUND - minY, Math.min(LAYOUT_BOUND - maxY, point ? point.y - minY : 32))
  const nodes = selection.nodes.map((node) => ({ ...node, id: mapping.get(node.id)!, position: { x: node.position.x + offsetX, y: node.position.y + offsetY } }))
  const edges = selection.edges.map((edge) => ({ ...edge, id: allocate('paste_edge'), source: mapping.get(edge.source)!, target: mapping.get(edge.target)! }))
  validateTransferGraph({ nodes: [...existing.nodes, ...nodes], edges: [...existing.edges, ...edges] }, registry)
  return { nodes, edges }
}
