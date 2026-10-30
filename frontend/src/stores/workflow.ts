import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { t, locale, message, messageRef, hasMessage, type MessageParams } from '../i18n'
import { computed as vueComputed } from 'vue'
import type { NodeType, NodeRegistry, PortName } from '../workflow/nodeRegistry'
import { fetchCapabilities } from '../workflow/capabilities'
import { createNodeRegistry } from '../workflow/nodeRegistry'

export type { NodeType, PortName } from '../workflow/nodeRegistry'
export type OutputPortName = string
export type InputPortName = string
export type RunStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelling' | 'cancelled' | 'interrupted'
export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'cancelled' | 'interrupted'
export const WORKFLOW_NODE_WIDTH = 188
export const WORKFLOW_NODE_HEIGHT = 92
export const WORKFLOW_NODE_ROW_GAP = 28
export const MAX_WORKFLOW_NODES = 64
export const MAX_WORKFLOW_EDGES = 64

export type TextOutputFormat = 'txt' | 'srt' | 'vtt' | 'lrc'

export interface TextOutputNodeConfig {
  filename_stem: string
  format: TextOutputFormat
}

export const DEFAULT_TEXT_OUTPUT_CONFIG: Readonly<TextOutputNodeConfig> = Object.freeze({
  filename_stem: 'transcript',
  format: 'txt',
})

export function defaultTextOutputConfig(): TextOutputNodeConfig {
  return { ...DEFAULT_TEXT_OUTPUT_CONFIG }
}

export function readTextOutputConfig(config: Record<string, unknown>): TextOutputNodeConfig {
  return {
    filename_stem: config.filename_stem === undefined ? DEFAULT_TEXT_OUTPUT_CONFIG.filename_stem : config.filename_stem as string,
    format: config.format === undefined ? DEFAULT_TEXT_OUTPUT_CONFIG.format : config.format as TextOutputFormat,
  }
}

export interface WorkflowNode {
  id: string
  type: NodeType
  config: Record<string, unknown>
  position: { x: number; y: number }
}

export interface WorkflowEdge {
  id: string
  source: string
  source_port: PortName
  target: string
  target_port: PortName
}

export interface WorkflowDocumentInput {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
}

export interface WorkflowDocument extends WorkflowDocumentInput {
  schema_version: string
  project_id: string
  revision: number
  workflow_revision: number
  updated_at: string
}

export interface WorkflowIssue {
  code: string
  message: string
  node_id?: string | null
  edge_id?: string | null
  field?: string | null
  messageKey?: string
  messageParams?: MessageParams
}

export interface WorkflowArtifact {
  artifact_id: string
  port: PortName
  filename: string
  media_type: string
  size_bytes: number
  sha256: string
  download_url: string | null
}

export type WorkflowOutputState = 'checking' | 'available' | 'missing' | 'failed'

export interface WorkflowOutputCheck {
  state: WorkflowOutputState
}

export interface WorkflowError {
  type?: string
  code?: string
  message: string
}

export interface WorkflowNodeRun {
  node_id: string
  node_type: NodeType
  config: Record<string, unknown>
  status: NodeStatus
  started_at: string | null
  finished_at: string | null
  artifact: WorkflowArtifact | null
  error: WorkflowError | null
}

export interface WorkflowRun {
  id: string
  project_id: string
  audio_id: string
  workflow_revision: number
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  cancel_requested: boolean
  error: WorkflowError | null
  workflow: WorkflowDocumentInput
  nodes: WorkflowNodeRun[]
}

export interface WorkflowRunList {
  items: WorkflowRunSummary[]
  total: number
  limit: number
  offset: number
}

export interface WorkflowRunSummary {
  id: string
  workflow_revision: number
  status: RunStatus
  created_at: string
  finished_at: string | null
  error: WorkflowError | null
}

const readApiError = parseApiError
const workflowApiError = parseApiError

async function readJson(response: Response, fallbackKey: string): Promise<unknown> {
  try { return await response.json() } catch { throw message(fallbackKey) }
}

export function issueText(messageKey: string, messageParams?: MessageParams) {
  return { message: t(messageKey, messageParams), messageKey, messageParams }
}

export function workflowIssueLabel(issue: WorkflowIssue): string {
  return issue.messageKey ? t(issue.messageKey, issue.messageParams) : workflowErrorLabel(issue)
}

export function workflowErrorLabel(error: WorkflowError): string {
  const code = error.code || error.type
  const key = `workflow.error_${code}`
  if (code && hasMessage(key)) return t(key)
  const apiKey = `common.api.${code}`
  return code && hasMessage(apiKey) ? t(apiKey) : error.message
}

export function cloneDocument(value: WorkflowDocumentInput): WorkflowDocumentInput {
  return JSON.parse(JSON.stringify({
    nodes: value.nodes,
    edges: value.edges,
  })) as WorkflowDocumentInput
}

function canonicalNodeConfig(node: WorkflowNode, registry?: NodeRegistry): Record<string, unknown> {
  return { ...(registry?.createDefaultConfig(node.type) || {}), ...(node.config || {}) }
}

export function outputPortForNode(type: NodeType, registry: NodeRegistry): OutputPortName | null {
  return registry.outputPortForNode(type)
}

export function inputPortForNode(type: NodeType, registry: NodeRegistry): InputPortName | null {
  return registry.inputPortForNode(type)
}

export function isTerminalNode(type: NodeType, registry: NodeRegistry): boolean {
  return registry.isTerminalNode(type)
}

export function isSafeFilenameStem(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const reserved = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(value)
  return !!value.trim() && value === value.trim() && value !== '.' && value !== '..' && !value.endsWith('.')
    && !reserved && !value.includes(String.fromCharCode(92))
    && !/[\/<>:"|?*\u0000-\u001f\u007f]/.test(value) && value.length <= 128
}

function stableObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableObject)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => [key, stableObject(item)]))
}

export function executionSignature(value: WorkflowDocumentInput, registry?: NodeRegistry): string {
  return JSON.stringify({
    nodes: value.nodes.map((node) => ({
      config: stableObject(canonicalNodeConfig(node, registry)),
      id: node.id,
      type: node.type,
    })).sort((left, right) => left.id.localeCompare(right.id)),
    edges: value.edges.map((edge) => ({
      source: edge.source,
      source_port: edge.source_port,
      target: edge.target,
      target_port: edge.target_port,
    })).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right))),
  })
}

export function resolveArtifactUrl(
  downloadUrl: string,
  apiBase = API_BASE_URL,
  pageOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin,
): string {
  try {
    if (/^[a-z][a-z\d+.-]*:/i.test(downloadUrl)) {
      const absolute = new URL(downloadUrl)
      return ['http:', 'https:'].includes(absolute.protocol) ? absolute.href : ''
    }
    const base = new URL(apiBase, pageOrigin)
    const baseDirectory = new URL(base.pathname.endsWith('/') ? base.pathname : `${base.pathname}/`, base.origin)
    const resolved = new URL(downloadUrl, baseDirectory)
    return ['http:', 'https:'].includes(resolved.protocol) ? resolved.href : ''
  } catch {
    return ''
  }
}

function readWorkflow(value: unknown): WorkflowDocument {
  const document = value as WorkflowDocument
  if (!document || typeof document !== 'object' || document.schema_version !== '1.0'
    || typeof document.project_id !== 'string' || !Number.isInteger(document.revision)
    || !Number.isInteger(document.workflow_revision) || !Array.isArray(document.nodes)
    || !Array.isArray(document.edges)) {
    throw message('workflow.invalidWorkflow')
  }
  return document
}

function readArtifact(value: unknown): WorkflowArtifact | null {
  if (value === null || value === undefined) return null
  const artifact = value as WorkflowArtifact
  if (!artifact || typeof artifact.artifact_id !== 'string'
    || !['audio', 'transcript', 'text'].includes(artifact.port)
    || typeof artifact.filename !== 'string' || typeof artifact.media_type !== 'string'
    || !Number.isInteger(artifact.size_bytes) || artifact.size_bytes < 0
    || typeof artifact.sha256 !== 'string'
    || (artifact.download_url !== null && typeof artifact.download_url !== 'string')) {
    throw message('workflow.invalidArtifact')
  }
  return artifact
}

function readRun(value: unknown): WorkflowRun {
  const run = value as WorkflowRun
  if (!run || typeof run.id !== 'string' || typeof run.audio_id !== 'string'
    || typeof run.status !== 'string' || !Array.isArray(run.nodes)
    || !run.workflow || !Array.isArray(run.workflow.nodes) || !Array.isArray(run.workflow.edges)) {
    throw message('workflow.invalidRun')
  }
  for (const node of run.nodes) {
    if (!node || typeof node.node_id !== 'string' || typeof node.node_type !== 'string'
      || typeof node.status !== 'string') {
      throw message('workflow.invalidRun')
    }
    readArtifact(node.artifact)
  }
  return run
}

function readRunSummary(value: unknown): WorkflowRunSummary {
  const run = value as WorkflowRunSummary
  if (!run || typeof run.id !== 'string' || !Number.isInteger(run.workflow_revision)
    || typeof run.status !== 'string' || typeof run.created_at !== 'string'
    || (run.finished_at !== null && typeof run.finished_at !== 'string')
    || (run.error !== null && (typeof run.error !== 'object' || typeof run.error.message !== 'string'))) {
    throw message('workflow.invalidHistory')
  }
  return run
}

export function createDefaultWorkflow(): WorkflowDocumentInput {
  return {
    nodes: [
      { id: 'input', type: 'audio_input', config: {}, position: { x: 96, y: 176 } },
      { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 500, y: 176 } },
    ],
    edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
  }
}

export function validateNodeConfig(node: Pick<WorkflowNode, 'id' | 'type' | 'config'>, registry: NodeRegistry): WorkflowIssue[] {
  const issues: WorkflowIssue[] = []
  const keys = Object.keys(node.config || {})
  if (node.type === 'audio_input') {
    if (keys.length) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.inputNoConfig'), node_id: node.id })
  } else if (node.type === 'audio_output') {
    const unexpected = keys.filter((key) => key !== 'filename_stem')
    if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.audioUnknownFields', { p0: unexpected.join(', ') }), node_id: node.id })
    const stem = node.config.filename_stem === undefined ? 'audio-copy' : node.config.filename_stem
    if (!isSafeFilenameStem(stem)) issues.push({ code: 'INVALID_FILENAME_STEM', ...issueText('workflow.audioSafeFilename'), node_id: node.id })
  } else if (node.type === 'text_output') {
    const allowed = new Set(['filename_stem', 'format'])
    const unexpected = keys.filter((key) => !allowed.has(key))
    if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.textUnknownFields', { p0: unexpected.join(', ') }), node_id: node.id })
    const stem = node.config.filename_stem === undefined ? DEFAULT_TEXT_OUTPUT_CONFIG.filename_stem : node.config.filename_stem
    if (!isSafeFilenameStem(stem)) issues.push({ code: 'INVALID_FILENAME_STEM', ...issueText('workflow.textSafeFilename'), node_id: node.id })
    const format = node.config.format === undefined ? DEFAULT_TEXT_OUTPUT_CONFIG.format : node.config.format
    if (typeof format !== 'string' || !['txt', 'srt', 'vtt', 'lrc'].includes(format)) {
      issues.push({ code: 'INVALID_TEXT_FORMAT', ...issueText('workflow.textFormat'), node_id: node.id })
    }
  } else {
    for (const issue of registry.validateConfig(node.type, node.config)) issues.push({ ...issue, node_id: node.id })
  }
  return issues
}

export function validateDraft(value: WorkflowDocumentInput, registry: NodeRegistry): WorkflowIssue[] {
  const issues: WorkflowIssue[] = []
  const inputKinds = new Set(registry.kinds.filter((kind) => kind.inputPorts.length === 0 && kind.outputPorts.length > 0).map((kind) => kind.type))
  const inputs = value.nodes.filter((node) => inputKinds.has(node.type))
  const outputs = value.nodes.filter((node) => isTerminalNode(node.type, registry))
  if (inputs.length !== 1) issues.push({ code: 'INPUT_COUNT', ...issueText('workflow.inputCount', { p0: inputs.length }), node_id: null })
  if (!outputs.length) issues.push({ code: 'OUTPUT_MISSING', ...issueText('workflow.outputMissing'), node_id: null })
  if (value.nodes.length > MAX_WORKFLOW_NODES) issues.push({ code: 'TOO_MANY_NODES', ...issueText('workflow.nodeLimit', { p0: MAX_WORKFLOW_NODES }), node_id: null })
  if (value.edges.length > MAX_WORKFLOW_EDGES) issues.push({ code: 'TOO_MANY_EDGES', ...issueText('workflow.edgeLimit', { p0: MAX_WORKFLOW_EDGES }), node_id: null })

  const ids = new Set<string>()
  const nodeTypes = new Map<string, NodeType>()
  const supportedTypes = new Set<NodeType>(registry.kinds.map((kind) => kind.type))
  for (const node of value.nodes) {
    if (ids.has(node.id)) {
      issues.push({ code: 'DUPLICATE_NODE', ...issueText('workflow.duplicateNode', { p0: node.id }), node_id: node.id })
      continue
    }
    ids.add(node.id)
    nodeTypes.set(node.id, node.type)
    if (!supportedTypes.has(node.type)) {
      issues.push({ code: 'UNKNOWN_NODE_TYPE', ...issueText('workflow.unknownNodeType', { p0: node.type }), node_id: node.id })
    }
  }

  const edgeIds = new Set<string>()
  const incoming = new Map(value.nodes.map((node) => [node.id, [] as typeof value.edges]))
  const outgoing = new Map(value.nodes.map((node) => [node.id, [] as typeof value.edges]))
  for (const edge of value.edges) {
    if (edgeIds.has(edge.id)) issues.push({ code: 'DUPLICATE_EDGE', ...issueText('workflow.duplicateEdge', { p0: edge.id }), edge_id: edge.id })
    edgeIds.add(edge.id)
    if (!ids.has(edge.source)) issues.push({ code: 'UNKNOWN_SOURCE', ...issueText('workflow.unknownSource', { p0: edge.source }), edge_id: edge.id })
    if (!ids.has(edge.target)) issues.push({ code: 'UNKNOWN_TARGET', ...issueText('workflow.unknownTarget', { p0: edge.target }), edge_id: edge.id })
    if (ids.has(edge.source)) outgoing.get(edge.source)!.push(edge)
    if (ids.has(edge.target)) incoming.get(edge.target)!.push(edge)
    const sourceType = nodeTypes.get(edge.source)
    const targetType = nodeTypes.get(edge.target)
    if (sourceType && targetType && (!outputPortForNode(sourceType, registry) || !inputPortForNode(targetType, registry))) {
      issues.push({ code: 'INVALID_DIRECTION', ...issueText('workflow.invalidDirection'), edge_id: edge.id })
    }
    if (edge.source === edge.target) issues.push({ code: 'SELF_LOOP', ...issueText('workflow.selfLoop'), edge_id: edge.id })
    const expectedSourcePort = sourceType ? outputPortForNode(sourceType, registry) : null
    const expectedTargetPort = targetType ? inputPortForNode(targetType, registry) : null
    if (expectedSourcePort && edge.source_port !== expectedSourcePort) {
      issues.push({ code: 'INVALID_PORT', ...issueText('workflow.sourcePort', { p0: expectedSourcePort }), edge_id: edge.id })
    }
    if (expectedTargetPort && edge.target_port !== expectedTargetPort) {
      issues.push({ code: 'INVALID_PORT', ...issueText('workflow.targetPort', { p0: expectedTargetPort }), edge_id: edge.id })
    }
    if (expectedSourcePort && expectedTargetPort && expectedSourcePort !== expectedTargetPort) {
      issues.push({ code: 'INVALID_PORT', ...issueText('workflow.invalidPort', { p0: expectedSourcePort, p1: expectedTargetPort }), edge_id: edge.id })
    }
  }

  for (const node of value.nodes) {
    const inbound = incoming.get(node.id) || []
    const outbound = outgoing.get(node.id) || []
    const definition = registry.kind(node.type)
    if (definition && definition.requiredInbound !== inbound.length && !definition.terminal) {
      issues.push({ code: 'NODE_INPUT_COUNT', ...issueText('workflow.nodeInputCount', { p0: node.id, p1: definition.requiredInbound, p2: inbound.length }), node_id: node.id })
    }
    if (definition?.terminal && inbound.length !== definition.requiredInbound) {
      issues.push({ code: 'NODE_INPUT_COUNT', ...issueText('workflow.nodeInputCount', { p0: node.id, p1: definition.requiredInbound, p2: inbound.length }), node_id: node.id })
    }
    if (definition && definition.inputPorts.length === 0 && inbound.length) {
      issues.push({ code: 'NODE_SOURCE_REQUIRED', ...issueText('workflow.noInbound', { p0: node.id }), node_id: node.id })
    }
    if (isTerminalNode(node.type, registry) && outbound.length) {
      issues.push({ code: 'OUTPUT_SINK_REQUIRED', ...issueText('workflow.outputNoOutbound'), node_id: node.id })
    }
    if (!inbound.length && !outbound.length) {
      issues.push({ code: 'ISOLATED_NODE', ...issueText('workflow.isolatedNode', { p0: node.id }), node_id: node.id })
    }
  }

  const outgoingTargets = new Map(value.nodes.map((node) => [node.id, outgoing.get(node.id)!.map((edge) => edge.target)]))
  const visitFromInput = new Set<string>()
  function visit(nodeId: string) {
    if (visitFromInput.has(nodeId)) return
    visitFromInput.add(nodeId)
    for (const target of outgoingTargets.get(nodeId) || []) visit(target)
  }
  if (inputs.length === 1) {
    visit(inputs[0].id)
    for (const node of value.nodes) {
      if (!visitFromInput.has(node.id)) issues.push({ code: 'UNREACHABLE_FROM_INPUT', ...issueText('workflow.unreachableInput', { p0: node.id }), node_id: node.id })
    }
  }

  const reachesOutput = new Set(outputs.map((node) => node.id))
  const reverseQueue = outputs.map((node) => node.id)
  while (reverseQueue.length) {
    const nodeId = reverseQueue.shift()!
    for (const edge of incoming.get(nodeId) || []) {
      if (reachesOutput.has(edge.source)) continue
      reachesOutput.add(edge.source)
      reverseQueue.push(edge.source)
    }
  }
  for (const node of value.nodes) {
    if (!isTerminalNode(node.type, registry) && !reachesOutput.has(node.id)) {
      issues.push({ code: 'OUTPUT_UNREACHABLE', ...issueText('workflow.unreachableOutput', { p0: node.id }), node_id: node.id })
    }
  }

  const indegree = new Map(value.nodes.map((node) => [node.id, incoming.get(node.id)!.length]))
  const queue = value.nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id)
  let visited = 0
  while (queue.length) {
    const nodeId = queue.shift()!
    visited += 1
    for (const target of outgoingTargets.get(nodeId) || []) {
      const degree = (indegree.get(target) || 0) - 1
      indegree.set(target, degree)
      if (degree === 0) queue.push(target)
    }
  }
  if (value.nodes.length && visited !== value.nodes.length) {
    issues.push({ code: 'CYCLE', ...issueText('workflow.cycle'), node_id: null })
  }

  for (const node of value.nodes) issues.push(...validateNodeConfig(node, registry))
  return issues
}

export function nextNodeId(type: NodeType, nodes: WorkflowNode[], registry: NodeRegistry): string {
  const prefix = registry.kind(type)?.idPrefix || type
  let index = 1
  while (nodes.some((node) => node.id === `${prefix}-${index}`)) index += 1
  return `${prefix}-${index}`
}

export function nextEdgeId(edges: WorkflowEdge[]): string {
  let index = 1
  while (edges.some((edge) => edge.id === `edge-${index}`)) index += 1
  return `edge-${index}`
}

export function nodeLabel(node: WorkflowNode, registry: NodeRegistry): string {
  return registry.label(node.type)
}

export function nextNodePosition(type: NodeType, nodes: WorkflowNode[], registry: NodeRegistry): WorkflowNode['position'] {
  const sameType = nodes.filter((node) => node.type === type)
  const definition = registry.kind(type)
  const columnX = sameType[0]?.position.x ?? definition?.positionX ?? 500
  let candidateY = sameType.length
    ? Math.max(...sameType.map((node) => node.position.y)) + WORKFLOW_NODE_HEIGHT + WORKFLOW_NODE_ROW_GAP
    : nodes.find((node) => node.type === 'audio_input')?.position.y ?? 176
  const overlaps = (y: number) => nodes.some((node) => (
    Math.abs(node.position.x - columnX) < WORKFLOW_NODE_WIDTH
    && Math.abs(node.position.y - y) < WORKFLOW_NODE_HEIGHT
  ))
  while (overlaps(candidateY)) candidateY += WORKFLOW_NODE_HEIGHT + WORKFLOW_NODE_ROW_GAP
  return { x: columnX, y: candidateY }
}

function summarizeRun(value: WorkflowRun): WorkflowRunSummary {
  return {
    id: value.id,
    workflow_revision: value.workflow_revision,
    status: value.status,
    created_at: value.created_at,
    finished_at: value.finished_at,
    error: value.error,
  }
}

function isActiveRunStatus(status: RunStatus) {
  return ['pending', 'running', 'cancelling'].includes(status)
}

export const useWorkflowStore = defineStore('workflow', () => {
  const capabilities = ref<Awaited<ReturnType<typeof fetchCapabilities>> | null>(null)
  const capabilitiesErrorState = messageRef()
  const capabilitiesError = capabilitiesErrorState.text
  let capabilitiesFailure: unknown
  const capabilitiesLoading = ref(false)
  const registry = vueComputed(() => {
    void locale.value
    return capabilities.value ? createNodeRegistry(capabilities.value) : null
  })
  const projectId = ref<string | null>(null)
  const audioId = ref<string | null>(null)
  const document = ref<WorkflowDocument | null>(null)
  const draft = ref<WorkflowDocumentInput>(createDefaultWorkflow())
  const savedSignature = ref('')
  const loading = ref(false)
  const saving = ref(false)
  const validating = ref(false)
  const starting = ref(false)
  const cancelling = ref(false)
  const actionLoading = computed(() => starting.value || cancelling.value)
  const errorState = messageRef()
  const error = errorState.text
  const noticeState = messageRef()
  const notice = noticeState.text
  const validationIssues = ref<WorkflowIssue[]>([])
  const selectedNodeId = ref<string | null>(null)
  const activeRunId = ref<string | null>(null)
  const run = ref<WorkflowRun | null>(null)
  const history = ref<WorkflowRunSummary[]>([])
  const historyLoading = ref(false)
  const historyErrorState = messageRef()
  const historyError = historyErrorState.text
  const runLoading = ref(false)
  const runErrorState = messageRef()
  const runError = runErrorState.text
  const outputs = ref<Record<string, WorkflowArtifact>>({})
  const outputChecks = ref<Record<string, WorkflowOutputCheck>>({})
  const zoom = ref(1)
  const pan = ref({ x: 0, y: 0 })
  let sessionGeneration = 0
  let validateSequence = 0
  let saveSequence = 0
  let historySequence = 0
  let runDetailSequence = 0
  let startSequence = 0
  let cancelSequence = 0
  let outputSequence = 0
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let stopped = true

  const dirty = computed(() => savedSignature.value !== executionSignature(draft.value, registry.value || undefined)
    || (document.value?.nodes || []).some((node) => JSON.stringify(node.position) !== JSON.stringify(draft.value.nodes.find((item) => item.id === node.id)?.position)))
  const selectedNode = computed(() => draft.value.nodes.find((node) => node.id === selectedNodeId.value) || null)
  const activeRun = computed(() => run.value && ['pending', 'running', 'cancelling'].includes(run.value.status))
  const currentOutputNode = computed(() => selectedNode.value && registry.value && isTerminalNode(selectedNode.value.type, registry.value) ? selectedNode.value : null)
  const runOutputs = computed(() => (run.value?.nodes || []).filter((node) => !!node.artifact))
  const runMatchesDraft = computed(() => !!run.value && !!registry.value
    && executionSignature(run.value.workflow, registry.value) === executionSignature(draft.value, registry.value))

  function currentKey() { return { id: projectId.value, audioId: audioId.value } }
  function isCurrent(id: string | null, aid: string | null, token: number) {
    return !stopped && id === projectId.value && aid === audioId.value && token === sessionGeneration
  }
  function endpoint(path: string) { return `${API_BASE_URL}${path}` }
  function projectPath(id: string) { return `/projects/${encodeURIComponent(id)}/workflow` }
  function runPath(id: string, aid: string) {
    return `/projects/${encodeURIComponent(id)}/audios/${encodeURIComponent(aid)}/workflow-runs`
  }

  function clearHistoryState() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
    run.value = null
    activeRunId.value = null
    outputs.value = {}
    outputChecks.value = {}
    runErrorState.set('')
    runLoading.value = false
    history.value = []
    historyErrorState.set('')
  }

  function resetRunState() {
    run.value = null
    activeRunId.value = null
    outputs.value = {}
    outputChecks.value = {}
    runErrorState.set('')
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function stopPolling() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function selectNode(id: string | null) {
    selectedNodeId.value = id && draft.value.nodes.some((node) => node.id === id) ? id : null
  }

  function setDraft(value: WorkflowDocumentInput) {
    draft.value = cloneDocument(value)
    if (selectedNodeId.value && !draft.value.nodes.some((node) => node.id === selectedNodeId.value)) selectedNodeId.value = draft.value.nodes[0]?.id || null
  }

  function markDirty() {
    validateSequence += 1
    validating.value = false
    validationIssues.value = []
    noticeState.set('')
  }

  function setCleanBaseline(value: WorkflowDocumentInput) {
    setDraft(value)
    savedSignature.value = executionSignature(value, registry.value || undefined)
  }

  async function validate(): Promise<WorkflowIssue[]> {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped || validating.value || activeRun.value) return []
    const token = sessionGeneration
    const sequence = ++validateSequence
    validating.value = true
    errorState.set('')
    const draftSnapshot = cloneDocument(draft.value)
    try {
      const response = await fetch(endpoint(projectPath(id) + '/validate'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draftSnapshot),
      })
      if (!response.ok) throw await readApiError(response)
      const payload = await readJson(response, 'workflow.invalidValidation') as { valid?: unknown; errors?: unknown }
      if (!payload || !Array.isArray(payload.errors) || typeof payload.valid !== 'boolean') throw message('workflow.invalidValidation')
      if (!isCurrent(id, aid, token) || sequence !== validateSequence) return payload.errors as WorkflowIssue[]
      validationIssues.value = payload.errors as WorkflowIssue[]
      if (!payload.valid) errorState.set(message('workflow.validationFailed'))
      return payload.errors as WorkflowIssue[]
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === validateSequence) {
        errorState.set(reason instanceof Error ? reason : message('workflow.apiUnavailable'))
      }
      throw reason
    } finally {
      if (isCurrent(id, aid, token) && sequence === validateSequence) validating.value = false
    }
  }

  async function loadCapabilities() {
    if (capabilities.value || capabilitiesLoading.value) return
    capabilitiesLoading.value = true
    capabilitiesErrorState.set('')
    capabilitiesFailure = undefined
    try {
      const value = await fetchCapabilities()
      if (stopped) return
      capabilities.value = value
    } catch (reason) {
      capabilitiesFailure = reason instanceof Error ? reason : message('workflow.capReadFailed')
      capabilitiesErrorState.set(capabilitiesFailure)
    } finally {
      capabilitiesLoading.value = false
    }
  }

  async function load(id: string, aid: string) {
    stop()
    projectId.value = id
    audioId.value = aid
    stopped = false
    const token = sessionGeneration
    loading.value = false
    saving.value = false
    validating.value = false
    starting.value = false
    cancelling.value = false
    historyLoading.value = false
    runLoading.value = false
    loading.value = true
    errorState.set('')
    noticeState.set('')
    validationIssues.value = []
    document.value = null
    draft.value = { nodes: [], edges: [] }
    savedSignature.value = ''
    selectedNodeId.value = null
    clearHistoryState()
    runErrorState.set('')
    try {
      await loadCapabilities()
      if (!isCurrent(id, aid, token)) return
      if (!capabilities.value || !registry.value) throw capabilitiesFailure || message('workflow.capStopLoad')
      const response = await fetch(endpoint(projectPath(id)))
      if (!response.ok) throw await readApiError(response)
      const value = readWorkflow(await readJson(response, 'workflow.invalidWorkflow'))
      if (!isCurrent(id, aid, token)) return
      document.value = value
      setDraft(value)
      savedSignature.value = executionSignature(value, registry.value || undefined)
      selectedNodeId.value = draft.value.nodes[0]?.id || null
      zoom.value = 1
      pan.value = { x: 0, y: 0 }
      void loadHistory(id, aid)
    } catch (reason) {
      if (isCurrent(id, aid, token)) errorState.set(reason instanceof Error ? reason : message('workflow.apiUnavailable'))
    } finally {
      if (isCurrent(id, aid, token)) loading.value = false
    }
  }

  async function save() {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped || saving.value || loading.value || validating.value || activeRun.value) return null
    const token = sessionGeneration
    const sequence = ++saveSequence
    const draftSnapshot = cloneDocument(draft.value)
    if (!registry.value) { errorState.set(message('workflow.capSave')); return null }
    const body = { expected_revision: document.value?.revision ?? 1, nodes: draftSnapshot.nodes, edges: draftSnapshot.edges }
    saving.value = true
    errorState.set('')
    noticeState.set('')
    try {
      const response = await fetch(endpoint(projectPath(id)), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!response.ok) {
        if (response.status === 409) {
          const failure = await workflowApiError(response)
          failure.name = 'WORKFLOW_REVISION_CONFLICT'
          throw failure
        }
        throw await workflowApiError(response)
      }
      const value = readWorkflow(await readJson(response, 'workflow.invalidWorkflow'))
      if (!isCurrent(id, aid, token) || sequence !== saveSequence) return null
      document.value = value
      setDraft(value)
      savedSignature.value = executionSignature(value, registry.value || undefined)
      validationIssues.value = []
      noticeState.set(message('workflow.saved'))
      return value
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === saveSequence) {
        errorState.set(reason instanceof Error ? reason : message('workflow.saveFailed'))
      }
      throw reason
    } finally {
      if (isCurrent(id, aid, token) && sequence === saveSequence) saving.value = false
    }
  }

  async function startRun() {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped || starting.value || activeRun.value || !document.value) return null
    if (dirty.value) {
      errorState.set(message('workflow.saveBeforeRun'))
      return null
    }
    const token = sessionGeneration
    const sequence = ++startSequence
    starting.value = true
    errorState.set('')
    runErrorState.set('')
    try {
      const response = await fetch(endpoint(runPath(id, aid)), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_revision: document.value.workflow_revision }),
      })
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await readJson(response, 'workflow.invalidRun'))
      if (!isCurrent(id, aid, token) || sequence !== startSequence) return null
      ++runDetailSequence
      outputs.value = {}
      outputChecks.value = {}
      run.value = value
      activeRunId.value = value.id
      history.value = [summarizeRun(value), ...history.value.filter((item) => item.id !== value.id)]
      await loadOutputs(id, aid, value.id, token)
      if (isActiveRunStatus(value.status)) schedulePoll()
      return value
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === startSequence) {
        errorState.set(reason instanceof Error ? reason : message('workflow.startFailed'))
      }
      throw reason
    } finally {
      if (isCurrent(id, aid, token) && sequence === startSequence) starting.value = false
    }
  }

  async function cancelRun() {
    const { id, audioId: aid } = currentKey()
    const runId = run.value?.id
    if (!id || !aid || !runId || stopped || cancelling.value) return
    const token = sessionGeneration
    const sequence = ++cancelSequence
    cancelling.value = true
    errorState.set('')
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}/cancel`), { method: 'POST' })
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await readJson(response, 'workflow.invalidRun'))
      if (!isCurrent(id, aid, token) || sequence !== cancelSequence) return
      applyRun(value)
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === cancelSequence) {
        errorState.set(reason instanceof Error ? reason : message('workflow.cancelFailed'))
      }
      throw reason
    } finally {
      if (isCurrent(id, aid, token) && sequence === cancelSequence) cancelling.value = false
    }
  }

  function applyRun(value: WorkflowRun) {
    run.value = value
    activeRunId.value = value.id
    const summary = summarizeRun(value)
    const index = history.value.findIndex((item) => item.id === value.id)
    if (index >= 0) history.value[index] = summary
    else history.value = [summary, ...history.value]
    if (isActiveRunStatus(value.status)) schedulePoll()
    else stopPolling()
  }

  async function loadHistory(id = projectId.value, aid = audioId.value) {
    if (!id || !aid || stopped || historyLoading.value) return
    const token = sessionGeneration
    const sequence = ++historySequence
    historyLoading.value = true
    historyErrorState.set('')
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}?limit=30&offset=0`))
      if (!response.ok) throw await readApiError(response)
      const value = await readJson(response, 'workflow.invalidHistory') as WorkflowRunList
      if (!value || !Array.isArray(value.items)) throw message('workflow.invalidHistory')
      if (!isCurrent(id, aid, token) || sequence !== historySequence) return
      history.value = value.items.map(readRunSummary)
      if (!run.value && history.value[0] && isActiveRunStatus(history.value[0].status)) {
        activeRunId.value = history.value[0].id
        await loadRun(history.value[0].id)
      }
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === historySequence) {
        historyErrorState.set(reason instanceof Error ? reason : message('workflow.historyFailed'))
      }
    } finally {
      if (isCurrent(id, aid, token) && sequence === historySequence) historyLoading.value = false
    }
  }

  async function loadRun(runId: string) {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped) return
    if (activeRun.value && run.value?.id !== runId) {
      runErrorState.set(message('workflow.activeHistory'))
      return
    }
    const token = sessionGeneration
    const sequence = ++runDetailSequence
    runLoading.value = true
    runErrorState.set('')
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}`))
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await readJson(response, 'workflow.invalidRun'))
      if (!isCurrent(id, aid, token) || sequence !== runDetailSequence) return
      outputs.value = {}
      outputChecks.value = {}
      applyRun(value)
      await loadOutputs(id, aid, runId, token)
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === runDetailSequence) {
        runErrorState.set(reason instanceof Error ? reason : message('workflow.runFailed'))
      }
    } finally {
      if (isCurrent(id, aid, token) && sequence === runDetailSequence) runLoading.value = false
    }
  }

  async function loadOutputs(id: string, aid: string, runId: string, token = sessionGeneration) {
    if (!isCurrent(id, aid, token)) return
    const sequence = ++outputSequence
    const current = run.value
    if (!current || current.id !== runId) return
    const completed = current.nodes.filter((node) => (
      ['preprocess', 'asr', 'audio_output', 'text_output'].includes(node.node_type) && node.status === 'completed'
    ))
    outputs.value = {}
    outputChecks.value = {}
    for (const node of completed) outputChecks.value[node.node_id] = { state: 'checking' }
    for (const node of completed) {
      try {
        const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(node.node_id)}`))
        if (!isCurrent(id, aid, token) || sequence !== outputSequence || run.value?.id !== runId) return
        if (response.status === 404) {
          outputChecks.value[node.node_id] = { state: 'missing' }
        } else if (!response.ok) {
          outputChecks.value[node.node_id] = { state: 'failed' }
        } else {
          const value = await response.json() as WorkflowArtifact
          if (!isCurrent(id, aid, token) || sequence !== outputSequence || run.value?.id !== runId) return
          if (value && typeof value.artifact_id === 'string'
            && (value.download_url === null || typeof value.download_url === 'string')) {
            outputs.value[node.node_id] = value
            outputChecks.value[node.node_id] = { state: 'available' }
          } else {
            outputChecks.value[node.node_id] = { state: 'failed' }
          }
        }
      } catch {
        if (!isCurrent(id, aid, token) || sequence !== outputSequence || run.value?.id !== runId) return
        outputChecks.value[node.node_id] = { state: 'failed' }
      }
    }
  }

  function schedulePoll() {
    if (!run.value || !activeRun.value || stopped) return
    stopPolling()
    const { id, audioId: aid } = currentKey()
    if (!id || !aid) return
    const token = sessionGeneration
    const runId = run.value.id
    pollTimer = setTimeout(async () => {
      pollTimer = null
      if (!isCurrent(id, aid, token) || run.value?.id !== runId || !run.value || !['pending', 'running', 'cancelling'].includes(run.value.status)) return
      try {
        const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}`))
        if (!response.ok) throw await readApiError(response)
        const value = readRun(await readJson(response, 'workflow.invalidRun'))
        if (!isCurrent(id, aid, token) || run.value?.id !== runId) return
        applyRun(value)
        await loadOutputs(id, aid, runId, token)
      } catch (reason) {
        if (isCurrent(id, aid, token) && run.value?.id === runId) {
          runErrorState.set(reason instanceof Error ? reason : message('workflow.pollFailed'))
          if (activeRun.value) schedulePoll()
        }
      }
    }, 1800)
  }

  function beginDraftEditing() {
    stopped = false
  }

  function leaveEditor() {
    stopped = true
    sessionGeneration += 1
    stopPolling()
  }

  async function downloadArtifact(nodeId: string) {
    const record = run.value?.nodes.find((node) => node.node_id === nodeId)
    if (!record || record.status !== 'completed' || !record.artifact || record.node_type === 'audio_input') {
      errorState.set(record?.status === 'completed' ? message('workflow.noArtifact') : message('workflow.nodeIncomplete'))
      return
    }
    const value = outputs.value[nodeId]
    if (!value) {
      const state = outputChecks.value[nodeId]?.state
      errorState.set(state === 'missing'
        ? message('workflow.artifactMissing')
        : state === 'failed'
          ? message('workflow.artifactRetry')
          : message('workflow.artifactWait'))
      return
    }
    if (!value.download_url) {
      errorState.set(message('workflow.artifactNoUrl'))
      return
    }
    const downloadUrl = resolveArtifactUrl(value.download_url)
    if (!downloadUrl) {
      errorState.set(message('workflow.artifactInvalidUrl'))
      return
    }
    const link = window.document.createElement('a')
    link.href = downloadUrl
    link.download = value.filename
    link.rel = 'noopener'
    link.click()
  }

  function addNode(type: NodeType) {
    if (stopped || loading.value || activeRun.value || !registry.value || draft.value.nodes.length >= MAX_WORKFLOW_NODES) return
    const nodes = draft.value.nodes
    const definition = registry.value.kind(type)
    if (!definition) { errorState.set(message('workflow.unregisteredType', { p0: type })); return }
    if (definition.maxInstances !== null && nodes.filter((node) => node.type === type).length >= definition.maxInstances) {
      errorState.set(definition.maxInstances === 1 ? message('workflow.maxOne', { p0: type }) : message('workflow.maxMany', { p0: type, p1: definition.maxInstances }))
      return
    }
    const id = nextNodeId(type, nodes, registry.value)
    const node: WorkflowNode = {
      id,
      type,
      config: registry.value.createDefaultConfig(type),
      position: nextNodePosition(type, nodes, registry.value),
    }
    draft.value.nodes = [...nodes, node]
    draft.value.edges = [...draft.value.edges]
    selectNode(id)
    markDirty()
  }

  function removeNode(id: string) {
    if (stopped || activeRun.value) return
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node || node.type === 'audio_input') {
      errorState.set(message('workflow.keepInput'))
      return
    }
    draft.value.nodes = draft.value.nodes.filter((item) => item.id !== id)
    draft.value.edges = draft.value.edges.filter((edge) => edge.source !== id && edge.target !== id)
    if (selectedNodeId.value === id) selectedNodeId.value = draft.value.nodes[0]?.id || null
    markDirty()
  }

  function updateNode(id: string, patch: Partial<Pick<WorkflowNode, 'config' | 'position'>>) {
    if (stopped || activeRun.value) return
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node) return
    if (patch.config !== undefined) node.config = patch.config
    if (patch.position !== undefined) node.position = patch.position
    markDirty()
  }

  function connect(source: string, target: string) {
    if (stopped || activeRun.value) return false
    const from = draft.value.nodes.find((node) => node.id === source)
    const to = draft.value.nodes.find((node) => node.id === target)
    if (!registry.value) return false
    const sourcePort = from ? outputPortForNode(from.type, registry.value) : null
    const targetPort = to ? inputPortForNode(to.type, registry.value) : null
    if (!from || !to || !sourcePort || !targetPort) {
      errorState.set(message('workflow.noPorts'))
      return false
    }
    if (sourcePort !== targetPort) {
      errorState.set(message('workflow.connectPortMismatch', { p0: sourcePort, p1: targetPort }))
      return false
    }
    if (source === target) {
      errorState.set(message('workflow.connectSelf'))
      return false
    }
    const exists = draft.value.edges.some((edge) => edge.source === source && edge.target === target)
    if (exists) return false
    if (!registry.value) { errorState.set(message('workflow.capConnect')); return false }
    if (draft.value.edges.length >= MAX_WORKFLOW_EDGES) {
      errorState.set(message('workflow.connectLimit', { p0: MAX_WORKFLOW_EDGES }))
      return false
    }
    const outgoing = new Map(draft.value.nodes.map((node) => [node.id, [] as string[]]))
    for (const edge of draft.value.edges) outgoing.get(edge.source)?.push(edge.target)
    const pending = [target]
    const visited = new Set<string>()
    while (pending.length) {
      const nodeId = pending.shift()!
      if (nodeId === source) {
        errorState.set(message('workflow.connectCycle'))
        return false
      }
      if (visited.has(nodeId)) continue
      visited.add(nodeId)
      pending.push(...(outgoing.get(nodeId) || []))
    }
    draft.value.edges = draft.value.edges.filter((edge) => edge.target !== target)
    draft.value.edges.push({ id: nextEdgeId(draft.value.edges), source, source_port: sourcePort, target, target_port: targetPort })
    errorState.set('')
    markDirty()
    return true
  }

  function disconnect(edgeId: string) {
    if (stopped || activeRun.value) return
    draft.value.edges = draft.value.edges.filter((edge) => edge.id !== edgeId)
    markDirty()
  }

  function stop() {
    stopped = true
    sessionGeneration += 1
    validateSequence += 1
    saveSequence += 1
    historySequence += 1
    runDetailSequence += 1
    startSequence += 1
    cancelSequence += 1
    outputSequence += 1
    loading.value = false
    saving.value = false
    validating.value = false
    starting.value = false
    cancelling.value = false
    historyLoading.value = false
    runLoading.value = false
    stopPolling()
  }

  function discardDraft() {
    if (document.value) {
      setDraft(document.value)
      savedSignature.value = executionSignature(document.value, registry.value || undefined)
    }
    discardSelection()
    validationIssues.value = []
    noticeState.set('')
    errorState.set('')
  }

  function discardSelection() {
    const first = document.value?.nodes[0]?.id || null
    selectedNodeId.value = first
  }

  return {
    capabilities, capabilitiesError, capabilitiesLoading, registry, projectId, audioId, document, draft, loading, saving, validating, actionLoading, error, notice,
    validationIssues, selectedNodeId, selectedNode, currentOutputNode, activeRunId, run, activeRun,
    history, historyLoading, historyError, runLoading, runError, outputs, outputChecks, runOutputs, zoom, pan, dirty,
    runMatchesDraft,
    setError: errorState.set, load, save, validate, startRun, cancelRun, loadHistory, loadRun, loadOutputs, addNode, removeNode,
    updateNode, connect, disconnect, selectNode, setDraft, setCleanBaseline, markDirty, beginDraftEditing, leaveEditor, downloadArtifact, stop, stopPolling,
    discardDraft,
  }
})
