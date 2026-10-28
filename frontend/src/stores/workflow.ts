import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE_URL } from './projectDetail'

export type NodeType = 'audio_input' | 'preprocess' | 'audio_output'
export type RunStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelling' | 'cancelled' | 'interrupted'
export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'cancelled' | 'interrupted'
export const WORKFLOW_NODE_WIDTH = 188
export const WORKFLOW_NODE_HEIGHT = 92
export const WORKFLOW_NODE_ROW_GAP = 28

export type PreprocessProvider = 'mock' | 'ffmpeg'
export type PreprocessChannels = 1 | 2

export interface PreprocessNodeConfig {
  provider: PreprocessProvider
  sample_rate: number
  channels: PreprocessChannels
  preserve_original: boolean
}

export const DEFAULT_PREPROCESS_CONFIG: Readonly<PreprocessNodeConfig> = Object.freeze({
  provider: 'mock',
  sample_rate: 16000,
  channels: 1,
  preserve_original: true,
})

export function defaultPreprocessConfig(): PreprocessNodeConfig {
  return { ...DEFAULT_PREPROCESS_CONFIG }
}

export function readPreprocessConfig(config: Record<string, unknown>): PreprocessNodeConfig {
  return {
    provider: config.provider === 'ffmpeg' ? 'ffmpeg' : 'mock',
    sample_rate: typeof config.sample_rate === 'number' ? config.sample_rate : DEFAULT_PREPROCESS_CONFIG.sample_rate,
    channels: config.channels === 2 ? 2 : 1,
    preserve_original: typeof config.preserve_original === 'boolean' ? config.preserve_original : DEFAULT_PREPROCESS_CONFIG.preserve_original,
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
  source_port: 'audio'
  target: string
  target_port: 'audio'
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
}

export interface WorkflowArtifact {
  artifact_id: string
  port: 'audio'
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

interface ApiErrorShape {
  error?: { code?: unknown; message?: unknown; details?: unknown }
}

function apiError(response: Response, payload?: unknown): Error {
  const error = (payload as ApiErrorShape | null)?.error
  const message = typeof error?.message === 'string' && error.message.trim()
    ? error.message
    : `节点工作流接口不可用（${response.status}）`
  const value = new Error(message)
  value.name = typeof error?.code === 'string' ? error.code : `HTTP_${response.status}`
  return value
}

async function readApiError(response: Response): Promise<Error> {
  let payload: unknown
  try { payload = await response.json() } catch { /* Use the status fallback. */ }
  return apiError(response, payload)
}

async function workflowApiError(response: Response): Promise<Error> {
  let payload: unknown
  try { payload = await response.json() } catch { /* Use the status fallback. */ }
  const body = payload as { error?: ApiErrorShape['error']; detail?: unknown } | null
  const detail = body?.detail
  const nested = detail && typeof detail === 'object' && 'error' in detail
    ? (detail as ApiErrorShape).error
    : undefined
  return apiError(response, { error: body?.error || nested })
}

export function cloneDocument(value: WorkflowDocumentInput): WorkflowDocumentInput {
  return JSON.parse(JSON.stringify({
    nodes: value.nodes,
    edges: value.edges,
  })) as WorkflowDocumentInput
}

function canonicalNodeConfig(node: WorkflowNode): Record<string, unknown> {
  const config = { ...node.config }
  if (node.type === 'preprocess') {
    if (config.provider === undefined) config.provider = DEFAULT_PREPROCESS_CONFIG.provider
    if (config.sample_rate === undefined) config.sample_rate = DEFAULT_PREPROCESS_CONFIG.sample_rate
    if (config.channels === undefined) config.channels = DEFAULT_PREPROCESS_CONFIG.channels
    if (config.preserve_original === undefined) config.preserve_original = DEFAULT_PREPROCESS_CONFIG.preserve_original
  } else if (node.type === 'audio_output' && config.filename_stem === undefined) {
    config.filename_stem = 'audio-copy'
  }
  return config
}

function stableObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableObject)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => [key, stableObject(item)]))
}

export function executionSignature(value: WorkflowDocumentInput): string {
  return JSON.stringify({
    nodes: value.nodes.map((node) => ({
      config: stableObject(canonicalNodeConfig(node)),
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
    throw new Error('节点工作流响应不完整，请检查后端版本后重试。')
  }
  return document
}

function readRun(value: unknown): WorkflowRun {
  const run = value as WorkflowRun
  if (!run || typeof run.id !== 'string' || typeof run.audio_id !== 'string'
    || typeof run.status !== 'string' || !Array.isArray(run.nodes)
    || !run.workflow || !Array.isArray(run.workflow.nodes) || !Array.isArray(run.workflow.edges)) {
    throw new Error('节点工作流运行响应不完整，请检查后端版本后重试。')
  }
  return run
}

function readRunSummary(value: unknown): WorkflowRunSummary {
  const run = value as WorkflowRunSummary
  if (!run || typeof run.id !== 'string' || !Number.isInteger(run.workflow_revision)
    || typeof run.status !== 'string' || typeof run.created_at !== 'string'
    || (run.finished_at !== null && typeof run.finished_at !== 'string')
    || (run.error !== null && (typeof run.error !== 'object' || typeof run.error.message !== 'string'))) {
    throw new Error('运行历史响应不完整，请检查后端版本后重试。')
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

export function validateDraft(value: WorkflowDocumentInput): WorkflowIssue[] {
  const issues: WorkflowIssue[] = []
  const inputs = value.nodes.filter((node) => node.type === 'audio_input')
  const outputs = value.nodes.filter((node) => node.type === 'audio_output')
  if (inputs.length !== 1) issues.push({ code: 'INPUT_COUNT', message: `需要恰好一个音频输入节点，当前为 ${inputs.length} 个`, node_id: null })
  if (!outputs.length) issues.push({ code: 'OUTPUT_MISSING', message: '至少需要一个音频输出节点', node_id: null })

  const ids = new Set<string>()
  const nodeTypes = new Map<string, NodeType>()
  const supportedTypes = new Set<NodeType>(['audio_input', 'preprocess', 'audio_output'])
  for (const node of value.nodes) {
    if (ids.has(node.id)) {
      issues.push({ code: 'DUPLICATE_NODE', message: `节点 ID 重复：${node.id}`, node_id: node.id })
      continue
    }
    ids.add(node.id)
    nodeTypes.set(node.id, node.type)
    if (!supportedTypes.has(node.type)) {
      issues.push({ code: 'UNKNOWN_NODE_TYPE', message: `不支持的节点类型：${node.type}`, node_id: node.id })
    }
  }

  const edgeIds = new Set<string>()
  const incoming = new Map(value.nodes.map((node) => [node.id, [] as typeof value.edges]))
  const outgoing = new Map(value.nodes.map((node) => [node.id, [] as typeof value.edges]))
  for (const edge of value.edges) {
    if (edgeIds.has(edge.id)) issues.push({ code: 'DUPLICATE_EDGE', message: `连线 ID 重复：${edge.id}`, edge_id: edge.id })
    edgeIds.add(edge.id)
    if (!ids.has(edge.source)) issues.push({ code: 'UNKNOWN_SOURCE', message: `连线起点不存在：${edge.source}`, edge_id: edge.id })
    if (!ids.has(edge.target)) issues.push({ code: 'UNKNOWN_TARGET', message: `连线终点不存在：${edge.target}`, edge_id: edge.id })
    if (ids.has(edge.source)) outgoing.get(edge.source)!.push(edge)
    if (ids.has(edge.target)) incoming.get(edge.target)!.push(edge)
    const sourceType = nodeTypes.get(edge.source)
    const targetType = nodeTypes.get(edge.target)
    if (sourceType && targetType && (!['audio_input', 'preprocess'].includes(sourceType) || !['preprocess', 'audio_output'].includes(targetType))) {
      issues.push({ code: 'INVALID_DIRECTION', message: '只允许音频输入或预处理节点连接到预处理或音频输出节点', edge_id: edge.id })
    }
    if (edge.source === edge.target) issues.push({ code: 'SELF_LOOP', message: '节点不能连接到自身', edge_id: edge.id })
    if (edge.source_port !== 'audio' || edge.target_port !== 'audio') issues.push({ code: 'INVALID_PORT', message: '连线端口必须是 audio', edge_id: edge.id })
  }

  for (const node of value.nodes) {
    const inbound = incoming.get(node.id) || []
    const outbound = outgoing.get(node.id) || []
    if (node.type === 'preprocess' && inbound.length !== 1) {
      issues.push({ code: 'PREPROCESS_INPUT_COUNT', message: `预处理节点“${node.id}”需要恰好一条音频输入，当前为 ${inbound.length} 条`, node_id: node.id })
    }
    if (node.type === 'audio_output' && inbound.length !== 1) {
      issues.push({ code: 'OUTPUT_INPUT_COUNT', message: `输出节点“${node.id}”需要恰好一条音频输入，当前为 ${inbound.length} 条`, node_id: node.id })
    }
    if (node.type === 'audio_input' && inbound.length) {
      issues.push({ code: 'INPUT_SOURCE_REQUIRED', message: '音频输入节点不能有入边', node_id: node.id })
    }
    if (node.type === 'audio_output' && outbound.length) {
      issues.push({ code: 'OUTPUT_SINK_REQUIRED', message: '音频输出节点不能有出边', node_id: node.id })
    }
    if (!inbound.length && !outbound.length) {
      issues.push({ code: 'ISOLATED_NODE', message: `节点“${node.id}”尚未连接到工作流`, node_id: node.id })
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
      if (!visitFromInput.has(node.id)) issues.push({ code: 'UNREACHABLE_FROM_INPUT', message: `节点“${node.id}”无法从音频输入到达`, node_id: node.id })
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
    if (node.type !== 'audio_output' && !reachesOutput.has(node.id)) {
      issues.push({ code: 'OUTPUT_UNREACHABLE', message: `节点“${node.id}”无法到达任何音频输出`, node_id: node.id })
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
    issues.push({ code: 'CYCLE', message: '工作流不能包含循环连线', node_id: null })
  }

  for (const node of value.nodes) {
    const keys = Object.keys(node.config || {})
    if (node.type === 'audio_input') {
      if (keys.length) issues.push({ code: 'INVALID_NODE_CONFIG', message: '音频输入节点不需要配置字段', node_id: node.id })
    } else if (node.type === 'audio_output') {
      const unexpected = keys.filter((key) => key !== 'filename_stem')
      if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', message: `音频输出只支持 filename_stem，未知字段：${unexpected.join(', ')}`, node_id: node.id })
      const stem = node.config?.filename_stem ?? 'audio-copy'
      const reserved = typeof stem === 'string' && /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(stem)
      if (typeof stem !== 'string' || !stem.trim() || stem !== stem.trim() || stem === '.' || stem === '..' || stem.endsWith('.') || reserved || stem.includes(String.fromCharCode(92)) || /[\/<>:"|?*\u0000-\u001f\u007f]/.test(stem) || stem.length > 128) {
        issues.push({ code: 'INVALID_FILENAME_STEM', message: '输出文件名必须是无路径的安全文件名主体', node_id: node.id })
      }
    } else if (node.type === 'preprocess') {
      const allowed = new Set(['provider', 'sample_rate', 'channels', 'preserve_original'])
      const unexpected = keys.filter((key) => !allowed.has(key))
      if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', message: `预处理配置包含未知字段：${unexpected.join(', ')}`, node_id: node.id })
      const provider = node.config.provider ?? DEFAULT_PREPROCESS_CONFIG.provider
      if (provider !== 'mock' && provider !== 'ffmpeg') issues.push({ code: 'INVALID_PREPROCESS_PROVIDER', message: '预处理 Provider 只能是 mock 或 ffmpeg', node_id: node.id })
      const sampleRate = node.config.sample_rate ?? DEFAULT_PREPROCESS_CONFIG.sample_rate
      if (typeof sampleRate !== 'number' || !Number.isInteger(sampleRate) || sampleRate < 8000 || sampleRate > 192000) {
        issues.push({ code: 'INVALID_SAMPLE_RATE', message: '采样率必须是 8000–192000 之间的整数', node_id: node.id })
      }
      const channels = node.config.channels ?? DEFAULT_PREPROCESS_CONFIG.channels
      if (channels !== 1 && channels !== 2) issues.push({ code: 'INVALID_CHANNELS', message: '声道数只能是 1 或 2', node_id: node.id })
      const preserveOriginal = node.config.preserve_original ?? DEFAULT_PREPROCESS_CONFIG.preserve_original
      if (typeof preserveOriginal !== 'boolean') issues.push({ code: 'INVALID_PRESERVE_ORIGINAL', message: '保留源文件选项必须是布尔值', node_id: node.id })
    }
  }
  return issues
}

export function nextNodeId(type: NodeType, nodes: WorkflowNode[]): string {
  const prefix = type === 'audio_input' ? 'input' : type === 'preprocess' ? 'preprocess' : 'output'
  let index = 1
  while (nodes.some((node) => node.id === `${prefix}-${index}`)) index += 1
  return `${prefix}-${index}`
}

export function nextEdgeId(edges: WorkflowEdge[]): string {
  let index = 1
  while (edges.some((edge) => edge.id === `edge-${index}`)) index += 1
  return `edge-${index}`
}

export function nodeLabel(node: WorkflowNode): string {
  if (node.type === 'audio_input') return '音频输入'
  if (node.type === 'preprocess') return '音频预处理'
  return '音频输出'
}

export function nextNodePosition(type: NodeType, nodes: WorkflowNode[]): WorkflowNode['position'] {
  const sameType = nodes.filter((node) => node.type === type)
  const columnX = type === 'audio_input' ? 96 : type === 'preprocess' ? 286 : sameType[0]?.position.x ?? 500
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
  const error = ref('')
  const notice = ref('')
  const validationIssues = ref<WorkflowIssue[]>([])
  const selectedNodeId = ref<string | null>(null)
  const activeRunId = ref<string | null>(null)
  const run = ref<WorkflowRun | null>(null)
  const history = ref<WorkflowRunSummary[]>([])
  const historyLoading = ref(false)
  const historyError = ref('')
  const runLoading = ref(false)
  const runError = ref('')
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

  const dirty = computed(() => savedSignature.value !== executionSignature(draft.value)
    || (document.value?.nodes || []).some((node) => JSON.stringify(node.position) !== JSON.stringify(draft.value.nodes.find((item) => item.id === node.id)?.position)))
  const selectedNode = computed(() => draft.value.nodes.find((node) => node.id === selectedNodeId.value) || null)
  const activeRun = computed(() => run.value && ['pending', 'running', 'cancelling'].includes(run.value.status))
  const currentOutputNode = computed(() => selectedNode.value?.type === 'audio_output' ? selectedNode.value : null)
  const runOutputs = computed(() => (run.value?.nodes || []).filter((node) => !!node.artifact))
  const runMatchesDraft = computed(() => !!run.value
    && executionSignature(run.value.workflow) === executionSignature(draft.value))

  function currentKey() { return { id: projectId.value, audioId: audioId.value } }
  function isCurrent(id: string | null, aid: string | null, token: number) {
    return !stopped && id === projectId.value && aid === audioId.value && token === sessionGeneration
  }
  function endpoint(path: string) { return `${API_BASE_URL}${path}` }
  function projectPath(id: string) { return `/projects/${encodeURIComponent(id)}/workflow` }
  function runPath(id: string, aid: string) {
    return `/projects/${encodeURIComponent(id)}/audios/${encodeURIComponent(aid)}/workflow-runs`
  }

  function resetRunState() {
    run.value = null
    activeRunId.value = null
    outputs.value = {}
    outputChecks.value = {}
    runError.value = ''
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
    notice.value = ''
  }

  async function validate(): Promise<WorkflowIssue[]> {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped || validating.value || activeRun.value) return []
    const token = sessionGeneration
    const sequence = ++validateSequence
    validating.value = true
    error.value = ''
    const draftSnapshot = cloneDocument(draft.value)
    try {
      const response = await fetch(endpoint(projectPath(id) + '/validate'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draftSnapshot),
      })
      if (!response.ok) throw await readApiError(response)
      const payload = await response.json() as { valid?: unknown; errors?: unknown }
      if (!Array.isArray(payload.errors) || typeof payload.valid !== 'boolean') throw new Error('校验响应不完整，请检查后端版本后重试。')
      if (!isCurrent(id, aid, token) || sequence !== validateSequence) return payload.errors as WorkflowIssue[]
      validationIssues.value = payload.errors as WorkflowIssue[]
      if (!payload.valid) error.value = '工作流校验未通过，请按节点定位修正后重试。'
      return payload.errors as WorkflowIssue[]
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === validateSequence) {
        error.value = reason instanceof Error ? reason.message : '节点工作流接口不可用'
      }
      throw reason
    } finally {
      if (isCurrent(id, aid, token) && sequence === validateSequence) validating.value = false
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
    error.value = ''
    notice.value = ''
    validationIssues.value = []
    document.value = null
    draft.value = createDefaultWorkflow()
    savedSignature.value = ''
    selectedNodeId.value = null
    history.value = []
    historyError.value = ''
    runLoading.value = false
    resetRunState()
    try {
      const response = await fetch(endpoint(projectPath(id)))
      if (!response.ok) throw await readApiError(response)
      const value = readWorkflow(await response.json())
      if (!isCurrent(id, aid, token)) return
      document.value = value
      setDraft(value)
      savedSignature.value = executionSignature(value)
      selectedNodeId.value = draft.value.nodes[0]?.id || null
      zoom.value = 1
      pan.value = { x: 0, y: 0 }
      void loadHistory(id, aid)
    } catch (reason) {
      if (isCurrent(id, aid, token)) error.value = reason instanceof Error ? reason.message : '节点工作流接口不可用'
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
    const body = { expected_revision: document.value?.revision ?? 1, nodes: draftSnapshot.nodes, edges: draftSnapshot.edges }
    saving.value = true
    error.value = ''
    notice.value = ''
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
      const value = readWorkflow(await response.json())
      if (!isCurrent(id, aid, token) || sequence !== saveSequence) return null
      document.value = value
      setDraft(value)
      savedSignature.value = executionSignature(value)
      validationIssues.value = []
      notice.value = '节点工作流已保存。'
      return value
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === saveSequence) {
        error.value = reason instanceof Error ? reason.message : '节点工作流保存失败'
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
      error.value = '请先保存当前工作流再运行。'
      return null
    }
    const token = sessionGeneration
    const sequence = ++startSequence
    starting.value = true
    error.value = ''
    runError.value = ''
    try {
      const response = await fetch(endpoint(runPath(id, aid)), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_revision: document.value.workflow_revision }),
      })
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await response.json())
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
        error.value = reason instanceof Error ? reason.message : '节点工作流启动失败'
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
    error.value = ''
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}/cancel`), { method: 'POST' })
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await response.json())
      if (!isCurrent(id, aid, token) || sequence !== cancelSequence) return
      applyRun(value)
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === cancelSequence) {
        error.value = reason instanceof Error ? reason.message : '节点工作流取消失败'
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
    historyError.value = ''
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}?limit=30&offset=0`))
      if (!response.ok) throw await readApiError(response)
      const value = await response.json() as WorkflowRunList
      if (!Array.isArray(value.items)) throw new Error('运行历史响应不完整，请检查后端版本后重试。')
      if (!isCurrent(id, aid, token) || sequence !== historySequence) return
      history.value = value.items.map(readRunSummary)
      if (!run.value && history.value[0] && isActiveRunStatus(history.value[0].status)) {
        activeRunId.value = history.value[0].id
        await loadRun(history.value[0].id)
      }
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === historySequence) {
        historyError.value = reason instanceof Error ? reason.message : '运行历史读取失败'
      }
    } finally {
      if (isCurrent(id, aid, token) && sequence === historySequence) historyLoading.value = false
    }
  }

  async function loadRun(runId: string) {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped) return
    if (activeRun.value && run.value?.id !== runId) {
      runError.value = '活动运行尚未结束，暂不能切换到其他历史运行。'
      return
    }
    const token = sessionGeneration
    const sequence = ++runDetailSequence
    runLoading.value = true
    runError.value = ''
    try {
      const response = await fetch(endpoint(`${runPath(id, aid)}/${encodeURIComponent(runId)}`))
      if (!response.ok) throw await readApiError(response)
      const value = readRun(await response.json())
      if (!isCurrent(id, aid, token) || sequence !== runDetailSequence) return
      outputs.value = {}
      outputChecks.value = {}
      applyRun(value)
      await loadOutputs(id, aid, runId, token)
    } catch (reason) {
      if (isCurrent(id, aid, token) && sequence === runDetailSequence) {
        runError.value = reason instanceof Error ? reason.message : '运行详情读取失败'
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
      ['preprocess', 'audio_output'].includes(node.node_type) && node.status === 'completed'
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
        const value = readRun(await response.json())
        if (!isCurrent(id, aid, token) || run.value?.id !== runId) return
        applyRun(value)
        await loadOutputs(id, aid, runId, token)
      } catch (reason) {
        if (isCurrent(id, aid, token)) runError.value = reason instanceof Error ? reason.message : '运行状态刷新失败'
        if (isCurrent(id, aid, token) && activeRun.value) schedulePoll()
      }
    }, 1800)
  }

  async function downloadArtifact(nodeId: string) {
    const value = outputs.value[nodeId]
    if (!value) {
      const state = outputChecks.value[nodeId]?.state
      error.value = state === 'missing'
        ? '产物缺失，不可下载。'
        : state === 'failed'
          ? '产物检查失败，请重试。'
          : '产物仍在检查中，请稍候。'
      return
    }
    if (!value.download_url) {
      error.value = '此产物没有可用的下载地址。'
      return
    }
    const downloadUrl = resolveArtifactUrl(value.download_url)
    if (!downloadUrl) {
      error.value = '服务端返回了无效的产物下载地址。'
      return
    }
    const link = window.document.createElement('a')
    link.href = downloadUrl
    link.download = value.filename
    link.rel = 'noopener'
    link.click()
  }

  function addNode(type: NodeType) {
    if (stopped || loading.value || activeRun.value) return
    const nodes = draft.value.nodes
    if (type === 'audio_input' && nodes.some((node) => node.type === 'audio_input')) {
      error.value = '工作流只允许一个音频输入节点。'
      return
    }
    const id = nextNodeId(type, nodes)
    const node: WorkflowNode = {
      id,
      type,
      config: type === 'audio_input'
        ? {}
        : type === 'preprocess'
          ? { ...defaultPreprocessConfig() }
          : { filename_stem: 'audio-copy' },
      position: nextNodePosition(type, nodes),
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
      error.value = '工作流保留唯一的音频输入节点，请删除预处理或音频输出节点。'
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
    if (!from || !to || !['audio_input', 'preprocess'].includes(from.type) || !['preprocess', 'audio_output'].includes(to.type)) {
      error.value = '只允许音频输入或预处理节点连接到预处理或音频输出节点。'
      return false
    }
    if (source === target) {
      error.value = '节点不能连接到自身。'
      return false
    }
    const exists = draft.value.edges.some((edge) => edge.source === source && edge.target === target)
    if (exists) return false
    const outgoing = new Map(draft.value.nodes.map((node) => [node.id, [] as string[]]))
    for (const edge of draft.value.edges) outgoing.get(edge.source)?.push(edge.target)
    const pending = [target]
    const visited = new Set<string>()
    while (pending.length) {
      const nodeId = pending.shift()!
      if (nodeId === source) {
        error.value = '该连线会形成循环，不能连接。'
        return false
      }
      if (visited.has(nodeId)) continue
      visited.add(nodeId)
      pending.push(...(outgoing.get(nodeId) || []))
    }
    draft.value.edges = draft.value.edges.filter((edge) => edge.target !== target)
    draft.value.edges.push({ id: nextEdgeId(draft.value.edges), source, source_port: 'audio', target, target_port: 'audio' })
    error.value = ''
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
      savedSignature.value = executionSignature(document.value)
    }
    discardSelection()
    validationIssues.value = []
    notice.value = ''
    error.value = ''
  }

  function discardSelection() {
    const first = document.value?.nodes[0]?.id || null
    selectedNodeId.value = first
  }

  return {
    projectId, audioId, document, draft, loading, saving, validating, actionLoading, error, notice,
    validationIssues, selectedNodeId, selectedNode, currentOutputNode, activeRunId, run, activeRun,
    history, historyLoading, historyError, runLoading, runError, outputs, outputChecks, runOutputs, zoom, pan, dirty,
    runMatchesDraft,
    load, save, validate, startRun, cancelRun, loadHistory, loadRun, loadOutputs, addNode, removeNode,
    updateNode, connect, disconnect, selectNode, setDraft, markDirty, downloadArtifact, stop, stopPolling,
    discardDraft,
  }
})
