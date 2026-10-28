import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE_URL } from './projectDetail'

export type NodeType = 'audio_input' | 'audio_output'
export type RunStatus = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelling' | 'cancelled' | 'interrupted'
export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'blocked' | 'cancelled' | 'interrupted'
export const WORKFLOW_NODE_WIDTH = 188
export const WORKFLOW_NODE_HEIGHT = 92
export const WORKFLOW_NODE_ROW_GAP = 28

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

export function executionSignature(value: WorkflowDocumentInput): string {
  return JSON.stringify({
    nodes: value.nodes.map((node) => ({
      id: node.id,
      type: node.type,
      config: node.config,
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
  for (const node of value.nodes) {
    if (ids.has(node.id)) issues.push({ code: 'DUPLICATE_NODE', message: `节点 ID 重复：${node.id}`, node_id: node.id })
    ids.add(node.id)
    nodeTypes.set(node.id, node.type)
  }
  const edgeIds = new Set<string>()
  for (const edge of value.edges) {
    if (edgeIds.has(edge.id)) issues.push({ code: 'DUPLICATE_EDGE', message: `连线 ID 重复：${edge.id}`, edge_id: edge.id })
    edgeIds.add(edge.id)
    if (!ids.has(edge.source)) issues.push({ code: 'UNKNOWN_SOURCE', message: `连线起点不存在：${edge.source}`, edge_id: edge.id })
    if (!ids.has(edge.target)) issues.push({ code: 'UNKNOWN_TARGET', message: `连线终点不存在：${edge.target}`, edge_id: edge.id })
  }
  for (const output of outputs) {
    const incoming = value.edges.filter((edge) => edge.target === output.id)
    if (incoming.length !== 1) issues.push({ code: 'OUTPUT_INPUT_COUNT', message: `输出节点“${output.id}”需要恰好一条音频输入，当前为 ${incoming.length} 条`, node_id: output.id })
  }
  for (const edge of value.edges) {
    const sourceType = nodeTypes.get(edge.source)
    const targetType = nodeTypes.get(edge.target)
    if (sourceType && targetType && (sourceType !== 'audio_input' || targetType !== 'audio_output')) {
      issues.push({ code: 'INVALID_DIRECTION', message: '只允许音频输入节点连接到音频输出节点', edge_id: edge.id })
    }
    if (edge.source === edge.target) issues.push({ code: 'SELF_LOOP', message: '节点不能连接到自身', edge_id: edge.id })
    if (edge.source_port !== 'audio' || edge.target_port !== 'audio') issues.push({ code: 'INVALID_PORT', message: '首版连线端口必须是 audio', edge_id: edge.id })
  }
  const outgoing = new Map(value.nodes.map((node) => [node.id, [] as string[]]))
  for (const edge of value.edges) {
    if (outgoing.has(edge.source)) outgoing.get(edge.source)!.push(edge.target)
  }
  const visiting = new Set<string>()
  const visited = new Set<string>()
  const cyclic = new Set<string>()
  function visit(nodeId: string) {
    if (visiting.has(nodeId)) { cyclic.add(nodeId); return }
    if (visited.has(nodeId)) return
    visiting.add(nodeId)
    for (const next of outgoing.get(nodeId) || []) visit(next)
    visiting.delete(nodeId)
    visited.add(nodeId)
  }
  for (const node of value.nodes) visit(node.id)
  if (cyclic.size) {
    for (const nodeId of cyclic) issues.push({ code: 'CYCLE', message: '工作流不能包含循环连线', node_id: nodeId })
  }
  const connected = new Set<string>()
  for (const edge of value.edges) { connected.add(edge.source); connected.add(edge.target) }
  for (const node of value.nodes) {
    if (!connected.has(node.id)) issues.push({ code: 'ISOLATED_NODE', message: `节点“${node.id}”尚未连接到工作流`, node_id: node.id })
  }
  for (const output of outputs) {
    const stem = output.config?.filename_stem
    if (typeof stem !== 'string' || !stem.trim() || stem === '.' || stem === '..' || /[\\/\u0000-\u001f]/.test(stem) || stem.length > 120) {
      issues.push({ code: 'INVALID_FILENAME_STEM', message: '输出文件名必须是无路径的安全文件名主体', node_id: output.id })
    }
  }
  return issues
}

export function nextNodeId(type: NodeType, nodes: WorkflowNode[]): string {
  const prefix = type === 'audio_input' ? 'input' : 'output'
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
  return node.type === 'audio_input' ? '音频输入' : '音频输出'
}

export function nextNodePosition(type: NodeType, nodes: WorkflowNode[]): WorkflowNode['position'] {
  if (type === 'audio_output') {
    const outputs = nodes.filter((node) => node.type === 'audio_output')
    if (outputs.length) {
      return {
        x: outputs[outputs.length - 1].position.x,
        y: Math.max(...outputs.map((node) => node.position.y)) + WORKFLOW_NODE_HEIGHT + WORKFLOW_NODE_ROW_GAP,
      }
    }
    const input = nodes.find((node) => node.type === 'audio_input')
    return { x: 420, y: input?.position.y ?? 120 }
  }
  return { x: 96, y: 176 }
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
    validationIssues.value = []
    notice.value = ''
  }

  async function validate(): Promise<WorkflowIssue[]> {
    const { id, audioId: aid } = currentKey()
    if (!id || !aid || stopped || validating.value) return []
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
    if (!id || !aid || stopped || saving.value || loading.value || validating.value) return null
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
      node.node_type === 'audio_output' && node.status === 'completed'
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
    if (stopped || loading.value) return
    const nodes = draft.value.nodes
    if (type === 'audio_input' && nodes.some((node) => node.type === 'audio_input')) {
      error.value = '首版只允许一个音频输入节点。'
      return
    }
    const input = nodes.find((node) => node.type === 'audio_input')
    const id = nextNodeId(type, nodes)
    const node: WorkflowNode = {
      id,
      type,
      config: type === 'audio_input' ? {} : { filename_stem: 'audio-copy' },
      position: nextNodePosition(type, nodes),
    }
    draft.value.nodes = [...nodes, node]
    draft.value.edges = [...draft.value.edges]
    if (type === 'audio_output' && input) {
      draft.value.edges.push({ id: nextEdgeId(draft.value.edges), source: input.id, source_port: 'audio', target: id, target_port: 'audio' })
    }
    selectNode(id)
    markDirty()
  }

  function removeNode(id: string) {
    if (stopped || activeRun.value) return
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node || node.type === 'audio_input') {
      error.value = '首版保留唯一的音频输入节点，请删除输出节点或清空画布。'
      return
    }
    draft.value.nodes = draft.value.nodes.filter((item) => item.id !== id)
    draft.value.edges = draft.value.edges.filter((edge) => edge.source !== id && edge.target !== id)
    if (selectedNodeId.value === id) selectedNodeId.value = draft.value.nodes[0]?.id || null
    markDirty()
  }

  function updateNode(id: string, patch: Partial<Pick<WorkflowNode, 'config' | 'position'>>) {
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node) return
    if (patch.config !== undefined) node.config = patch.config
    if (patch.position !== undefined) node.position = patch.position
    markDirty()
  }

  function connect(source: string, target: string) {
    const from = draft.value.nodes.find((node) => node.id === source)
    const to = draft.value.nodes.find((node) => node.id === target)
    if (!from || !to || from.type !== 'audio_input' || to.type !== 'audio_output') {
      error.value = '首版只支持音频输入连接到音频输出。'
      return false
    }
    const exists = draft.value.edges.some((edge) => edge.source === source && edge.target === target)
    if (exists) return false
    draft.value.edges = draft.value.edges.filter((edge) => edge.target !== target)
    draft.value.edges.push({ id: nextEdgeId(draft.value.edges), source, source_port: 'audio', target, target_port: 'audio' })
    markDirty()
    return true
  }

  function disconnect(edgeId: string) {
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
