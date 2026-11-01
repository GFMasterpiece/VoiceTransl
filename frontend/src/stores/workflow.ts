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
export const WORKFLOW_NODE_PORT_Y = 45
export const WORKFLOW_NODE_FIELDS_Y = 62
export const WORKFLOW_NODE_FIELD_HEIGHT = 44
export function workflowFieldHeight(field: { type: string }): number { return field.type === 'json' ? 108 : WORKFLOW_NODE_FIELD_HEIGHT }

export const WORKFLOW_RESOURCE_HEIGHT = 236
export const WORKFLOW_PREVIEW_HEIGHT = 190
export function workflowFieldsHeight(type: NodeType, registry: NodeRegistry): number {
  const kind = registry.kind(type)
  return (kind?.fields || []).filter((field) => field.key !== kind?.resourceInput?.configKey).reduce((height, field) => height + workflowFieldHeight(field), 0)
}
export function workflowNodeHeight(type: NodeType, registry: NodeRegistry): number {
  const kind = registry.kind(type)
  return Math.max(WORKFLOW_NODE_HEIGHT, WORKFLOW_NODE_FIELDS_Y + workflowFieldsHeight(type, registry)
    + (kind?.resourceInput ? WORKFLOW_RESOURCE_HEIGHT : 0) + (kind?.preview ? WORKFLOW_PREVIEW_HEIGHT : 0) + 10)
}
export const WORKFLOW_NODE_ROW_GAP = 28
export interface WorkflowNodeSize {
  width: number
  height: number
}
export interface WorkflowNode {
  id: string
  type: NodeType
  config: Record<string, unknown>
  position: { x: number; y: number }
  size?: WorkflowNodeSize | null
}

/** Layout dimensions never change the node's configuration or execution identity. */
export function workflowNodeSize(node: Pick<WorkflowNode, 'type' | 'size'>, registry?: NodeRegistry | null, measuredMin?: WorkflowNodeSize): WorkflowNodeSize {
  const dimension = (value: number | undefined) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0
  return {
    width: Math.max(registry?.kind(node.type)?.preview?.renderer === 'audio' ? 316 : WORKFLOW_NODE_WIDTH,
      dimension(node.size?.width), dimension(measuredMin?.width)),
    height: Math.max(registry ? workflowNodeHeight(node.type, registry) : WORKFLOW_NODE_HEIGHT,
      dimension(node.size?.height), dimension(measuredMin?.height)),
  }
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
  preview?: { renderer: string; content_url: string; media_type: string } | null
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

function layoutSignature(value: WorkflowDocumentInput): string {
  return JSON.stringify(value.nodes.map((node) => ({
    id: node.id,
    position: { x: node.position.x, y: node.position.y },
    size: node.size ? { width: node.size.width, height: node.size.height } : null,
  }))
    .sort((left, right) => left.id.localeCompare(right.id)))
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
    || typeof artifact.port !== 'string' || !artifact.port
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
    if (node.preview != null && (typeof node.preview.renderer !== 'string' || typeof node.preview.content_url !== 'string' || typeof node.preview.media_type !== 'string')) throw message('workflow.invalidPreview')
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

export function createDefaultWorkflow(registry: NodeRegistry): WorkflowDocumentInput {
  return cloneDocument(registry.capabilities.defaultGraph)
}

export function validateNodeConfig(node: Pick<WorkflowNode, 'id' | 'type' | 'config'>, registry: NodeRegistry): WorkflowIssue[] {
  return registry.validateConfig(node.type, node.config).map((issue) => ({ ...issue, node_id: node.id }))
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

export function nextNodePosition(type: NodeType, nodes: WorkflowNode[], registry: NodeRegistry, measurements: Record<string, WorkflowNodeSize> = {}, preferred?: WorkflowNode['position']): WorkflowNode['position'] {
  const sameType = nodes.filter((node) => node.type === type)
  const definition = registry.kind(type)
  const columnX = preferred?.x ?? sameType[0]?.position.x ?? definition?.positionX ?? 500
  const minimum = sameType.reduce((size, node) => ({
    width: Math.max(size.width, measurements[node.id]?.width || 0),
    height: Math.max(size.height, measurements[node.id]?.height || 0),
  }), { width: 0, height: 0 })
  const { width, height } = workflowNodeSize({ type }, registry, minimum)
  const nodeSize = (node: WorkflowNode) => workflowNodeSize(node, registry, measurements[node.id])
  let candidateY = preferred?.y ?? (sameType.length
    ? Math.max(...sameType.map((node) => node.position.y + nodeSize(node).height)) + WORKFLOW_NODE_ROW_GAP
    : nodes.find((node) => registry.kind(node.type)?.isSource)?.position.y ?? 176)
  while (true) {
    const collisions = nodes.filter((node) => columnX < node.position.x + nodeSize(node).width
      && columnX + width > node.position.x
      && candidateY < node.position.y + nodeSize(node).height + WORKFLOW_NODE_ROW_GAP
      && candidateY + height + WORKFLOW_NODE_ROW_GAP > node.position.y)
    if (!collisions.length) break
    candidateY = Math.max(...collisions.map((node) => node.position.y + nodeSize(node).height)) + WORKFLOW_NODE_ROW_GAP
  }
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
  const draft = ref<WorkflowDocumentInput>({ nodes: [], edges: [] })
  const savedSignature = ref('')
  const savedLayoutSignature = ref('')
  const nodeMinimums = ref<Record<string, WorkflowNodeSize>>({})
  const loading = ref(false)
  const saving = ref(false)
  const resourceUploading = ref(false)
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
    || savedLayoutSignature.value !== layoutSignature(draft.value))
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

  function setNodeMinimum(id: string, size: WorkflowNodeSize) {
    if (!draft.value.nodes.some((node) => node.id === id)
      || !Number.isFinite(size.width) || !Number.isFinite(size.height) || size.width <= 0 || size.height <= 0) return
    const previous = nodeMinimums.value[id]
    if (previous?.width === size.width && previous.height === size.height) return
    nodeMinimums.value = { ...nodeMinimums.value, [id]: { width: size.width, height: size.height } }
  }

  function getNodeSize(node: WorkflowNode): WorkflowNodeSize {
    return workflowNodeSize(node, registry.value, nodeMinimums.value[node.id])
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
    savedLayoutSignature.value = layoutSignature(value)
  }

  function localConfigIssues(): WorkflowIssue[] {
    if (!registry.value) return []
    const issues = draft.value.nodes.flatMap((node) => validateNodeConfig(node, registry.value!))
    validationIssues.value = issues
    if (issues.length) errorState.set(message('workflow.validationFailed'))
    return issues
  }

  async function validate(): Promise<WorkflowIssue[]> {
    const { id, audioId: aid } = currentKey()
    if (!id || stopped || saving.value || validating.value || resourceUploading.value || activeRun.value) return []
    const localIssues = localConfigIssues()
    if (localIssues.length) return localIssues
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

  async function load(id: string, aid: string | null) {
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
    savedLayoutSignature.value = ''
    nodeMinimums.value = {}
    selectedNodeId.value = null
    clearHistoryState()
    runErrorState.set('')
    try {
      await loadCapabilities()
      if (!isCurrent(id, aid, token)) return
      if (!capabilities.value || !registry.value) throw capabilitiesFailure || message('workflow.capStopLoad')
      draft.value = createDefaultWorkflow(registry.value)
      const response = await fetch(endpoint(projectPath(id)))
      if (!response.ok) throw await readApiError(response)
      const value = readWorkflow(await readJson(response, 'workflow.invalidWorkflow'))
      if (!isCurrent(id, aid, token)) return
      document.value = value
      setDraft(value)
      savedSignature.value = executionSignature(value, registry.value || undefined)
      savedLayoutSignature.value = layoutSignature(value)
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
    if (!id || stopped || saving.value || loading.value || validating.value || resourceUploading.value || activeRun.value) return null
    const token = sessionGeneration
    if (localConfigIssues().length) return null
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
      savedLayoutSignature.value = layoutSignature(value)
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

  const boundAudioId = computed(() => {
    for (const node of draft.value.nodes) {
      const resource = registry.value?.kind(node.type)?.resourceInput
      if (resource?.resourceType === 'audio') {
        const id = node.config[resource.configKey]
        if (typeof id === 'string' && id) return id
      }
    }
    return null
  })
  async function setAudioContext(aid: string | null) {
    if (aid === audioId.value) return true
    if (activeRun.value || saving.value || validating.value || starting.value || resourceUploading.value || loading.value || stopped) return false
    sessionGeneration += 1
    historySequence += 1
    runDetailSequence += 1
    outputSequence += 1
    clearHistoryState()
    historyLoading.value = false
    audioId.value = aid
    if (aid) await loadHistory()
    return true
  }

  async function startRun() {
    if (resourceUploading.value || saving.value || validating.value || loading.value) return null
    if (!dirty.value && boundAudioId.value && boundAudioId.value !== audioId.value) await setAudioContext(boundAudioId.value)
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
    const completed = current.nodes.filter((node) => node.artifact && node.status === 'completed')
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
    if (!record || record.status !== 'completed' || !record.artifact) {
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

  function addNode(type: NodeType, position?: WorkflowNode['position']) {
    if (stopped || loading.value || saving.value || validating.value || resourceUploading.value || actionLoading.value || activeRun.value || !registry.value) return
    if (position && (!Number.isFinite(position.x) || !Number.isFinite(position.y))) { errorState.set(message('workflow.nodePlacementFailed')); return }
    const nodes = draft.value.nodes
    const definition = registry.value.kind(type)
    if (!definition) { errorState.set(message('workflow.unregisteredType', { p0: type })); return }
    if (nodes.length >= registry.value.capabilities.constraints.maxNodes) { errorState.set(message('workflow.nodeLimit', { p0: registry.value.capabilities.constraints.maxNodes })); return }
    if (definition.isSource && nodes.filter((node) => registry.value!.kind(node.type)?.isSource).length >= registry.value.capabilities.constraints.maxSources) {
      errorState.set(message('workflow.inputCount', { p0: registry.value.capabilities.constraints.maxSources })); return
    }
    if (definition.maxInstances !== null && nodes.filter((node) => node.type === type).length >= definition.maxInstances) {
      errorState.set(definition.maxInstances === 1 ? message('workflow.maxOne', { p0: type }) : message('workflow.maxMany', { p0: type, p1: definition.maxInstances }))
      return
    }
    const placed = nextNodePosition(type, nodes, registry.value, nodeMinimums.value, position ? { x: Math.max(-1_000_000, Math.min(1_000_000, Math.round(position.x))), y: Math.max(-1_000_000, Math.min(1_000_000, Math.round(position.y))) } : undefined)
    if (!Number.isFinite(placed.x) || !Number.isFinite(placed.y) || Math.abs(placed.x) > 1_000_000 || Math.abs(placed.y) > 1_000_000) { errorState.set(message('workflow.nodePlacementFailed')); return }
    const id = nextNodeId(type, nodes, registry.value)
    const node: WorkflowNode = {
      id,
      type,
      config: registry.value.createDefaultConfig(type),
      position: placed,
    }
    draft.value.nodes = [...nodes, node]
    draft.value.edges = [...draft.value.edges]
    selectNode(id)
    markDirty()
  }

  function removeNode(id: string) {
    if (stopped || activeRun.value) return
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node || registry.value?.kind(node.type)?.removable === false) {
      errorState.set(message('workflow.keepInput'))
      return
    }
    draft.value.nodes = draft.value.nodes.filter((item) => item.id !== id)
    delete nodeMinimums.value[id]
    draft.value.edges = draft.value.edges.filter((edge) => edge.source !== id && edge.target !== id)
    if (selectedNodeId.value === id) selectedNodeId.value = draft.value.nodes[0]?.id || null
    markDirty()
  }

  function updateNode(id: string, patch: Partial<Pick<WorkflowNode, 'config' | 'position' | 'size'>>) {
    if (stopped || saving.value || validating.value || activeRun.value || resourceUploading.value || actionLoading.value) return
    const node = draft.value.nodes.find((item) => item.id === id)
    if (!node) return
    if (patch.config !== undefined) node.config = patch.config
    if (patch.position !== undefined) node.position = patch.position
    if (patch.size !== undefined) node.size = patch.size === null ? null : { ...patch.size }
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
    if (!registry.value.portsCompatible(sourcePort, targetPort)) {
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
    if (draft.value.edges.length >= registry.value.capabilities.constraints.maxEdges) {
      errorState.set(message('workflow.connectLimit', { p0: registry.value.capabilities.constraints.maxEdges }))
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
      savedLayoutSignature.value = layoutSignature(document.value)
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
    capabilities, capabilitiesError, capabilitiesLoading, registry, projectId, audioId, document, draft, loading, saving, validating, resourceUploading, actionLoading, error, notice,
    validationIssues, selectedNodeId, selectedNode, currentOutputNode, activeRunId, run, activeRun,
    history, historyLoading, historyError, runLoading, runError, outputs, outputChecks, runOutputs, zoom, pan, dirty, boundAudioId, setAudioContext,
    runMatchesDraft, nodeMinimums, setNodeMinimum, getNodeSize,
    setError: errorState.set, load, save, validate, startRun, cancelRun, loadHistory, loadRun, loadOutputs, addNode, removeNode,
    updateNode, connect, disconnect, selectNode, setDraft, setCleanBaseline, markDirty, beginDraftEditing, leaveEditor, downloadArtifact, stop, stopPolling,
    discardDraft,
  }
})
