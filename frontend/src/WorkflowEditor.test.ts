import { debugMode, setDebugMode, DEBUG_MODE_STORAGE_KEY } from './composables/editorPreferences'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, reactive, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowEditor from './components/WorkflowEditor.vue'
import {
  WORKFLOW_NODE_HEIGHT, WORKFLOW_NODE_WIDTH, WORKFLOW_NODE_PORT_Y, WORKFLOW_NODE_FIELD_HEIGHT, WORKFLOW_NODE_ROW_GAP, workflowNodeHeight, workflowFieldsHeight, workflowNodeSize,
  cloneDocument, createDefaultWorkflow, executionSignature, nextNodePosition,
  resolveArtifactUrl, useWorkflowStore, validateNodeConfig,
  type WorkflowDocument, type WorkflowDocumentInput, type WorkflowNode, type WorkflowRun, type WorkflowRunSummary,
} from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry, type NodeRegistry } from './workflow/nodeRegistry'
import { setLocale, message, t } from './i18n'
import { workflowErrorLabel, workflowIssueLabel } from './stores/workflow'
import { copySelection, serializeWorkflow } from './workflow/editorTransfer'
import { parseWorkflowFile } from './workflow/editorTransfer'

const testCapabilities: Record<string, any> = {
  schema_version: '1.0',
  workflow: {
    schema_version: '1.2',
    nodes: [
      { type: 'audio_input', label: '音频输入', id_prefix: 'input', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] }, position_x: 96 },
      { type: 'preprocess', label: '音频预处理', add_label: '预处理', id_prefix: 'preprocess', input_ports: ['audio'], output_ports: ['audio'], terminal: false, required_inbound: 1, removable: true, config: { kind: 'preprocess', fields: [
        { key: 'provider', label: 'Provider', type: 'select', required: false, default: 'mock', minimum: null, maximum: null, options: [{ value: 'mock', label: 'Mock' }] },
      ] }, position_x: 286 },
      { type: 'asr', label: '语音识别', add_label: 'ASR', id_prefix: 'asr', input_ports: ['audio'], output_ports: ['transcript'], terminal: false, required_inbound: 1, removable: true, config: { kind: 'asr', fields: [
        { key: 'provider', label: 'Provider', type: 'select', required: false, default: 'mock', minimum: null, maximum: null, options: [{ value: 'mock', label: 'Mock' }] },
        { key: 'language', label: '语言', type: 'string', required: false, default: 'ja', minimum: null, maximum: null, min_length: 1, max_length: 32, options: [] },
      ] }, position_x: 486 },
      { type: 'audio_output', label: '音频输出', add_label: '输出节点', id_prefix: 'output', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [
        { key: 'filename_stem', label: '文件名', type: 'string', required: false, default: 'audio-copy', minimum: null, maximum: null, min_length: 1, max_length: 128, validator: 'safe_filename_stem', options: [] },
      ] }, position_x: 500 },
      { type: 'text_output', label: '文本输出', add_label: '文本输出', id_prefix: 'text', input_ports: ['transcript'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'text_output', fields: [
        { key: 'filename_stem', label: '文件名', type: 'string', required: false, default: 'transcript', minimum: null, maximum: 128, min_length: 1, max_length: 128, validator: 'safe_filename_stem', options: [] },
        { key: 'format', label: '格式', type: 'select', required: false, default: 'txt', minimum: null, maximum: null, options: [{ value: 'txt', label: 'TXT' }, { value: 'srt', label: 'SRT' }, { value: 'vtt', label: 'VTT' }, { value: 'lrc', label: 'LRC' }] },
      ] }, position_x: 686 },
    ],
    ports: ['audio', 'transcript', 'text'],
    constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: {
      nodes: [
        { id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } },
        { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } },
      ],
      edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
    },
  },
  asr: { providers: [{ id: 'mock', label: 'Mock' }] },
  export: { formats: [{ id: 'srt', label: 'SRT' }, { id: 'vtt', label: 'VTT' }, { id: 'lrc', label: 'LRC' }], naming_modes: [{ id: 'language', label: '按语言命名' }, { id: 'original', label: '沿用源文件名' }] },
}
const testRegistry: NodeRegistry = createNodeRegistry(readCapabilities(testCapabilities))
const input = { id: 'input', type: 'audio_input' as const, config: {}, position: { x: 80, y: 120 } }
const output = { id: 'output', type: 'audio_output' as const, config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }
const edge = { id: 'input-output', source: 'input', source_port: 'audio' as const, target: 'output', target_port: 'audio' as const }
const preprocess = { id: 'preprocess-1', type: 'preprocess' as const, config: { provider: 'mock' as const }, position: { x: 260, y: 120 } }
const preprocessEdge = { id: 'input-preprocess', source: 'input', source_port: 'audio' as const, target: 'preprocess-1', target_port: 'audio' as const }
const preprocessToOutput = { id: 'preprocess-output', source: 'preprocess-1', source_port: 'audio' as const, target: 'output', target_port: 'audio' as const }
const asr = { id: 'asr-1', type: 'asr' as const, config: { provider: 'mock' as const, language: 'ja' }, position: { x: 486, y: 120 } }
const inputAsr = { id: 'input-asr', source: 'input', source_port: 'audio' as const, target: 'asr-1', target_port: 'audio' as const }
const textOutput = { id: 'text-1', type: 'text_output' as const, config: { filename_stem: 'transcript', format: 'txt' as const }, position: { x: 700, y: 120 } }
const asrText = { id: 'asr-text', source: 'asr-1', source_port: 'transcript' as const, target: 'text-1', target_port: 'transcript' as const }
const outputArtifact = {
  artifact_id: 'art-out', port: 'audio' as const, filename: 'audio-copy.wav',
  media_type: 'audio/wav', size_bytes: 12, sha256: 'a',
  download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/output/download',
}
const preprocessArtifact = {
  ...outputArtifact, artifact_id: 'art-pre', filename: 'audio-pre.wav',
  download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/preprocess-1/download',
}
const transcriptArtifact = {
  ...outputArtifact, artifact_id: 'art-asr', port: 'transcript' as const, filename: 'transcript.json',
  media_type: 'application/json', download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/asr-1/download',
}
const textArtifact = {
  ...outputArtifact, artifact_id: 'art-text', port: 'text' as const, filename: 'transcript.srt',
  media_type: 'application/x-subrip', download_url: '/api/v1/projects/p1/audios/a1/workflow-runs/run-1/outputs/text-1/download',
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
    if (path.endsWith('/capabilities')) return Promise.resolve(response(testCapabilities))
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

async function closePanel() {
  if (root!.querySelector('.workflow-panel-content')) {
    root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click()
    await settle()
  }
}
async function openPanel(panel: 'run' | 'artifacts' | 'logs') {
  if (root!.querySelector(`.workflow-panel-content[data-panel="${panel}"]`)) return
  await closePanel()
  root!.querySelector<HTMLButtonElement>(`[data-open-panel="${panel}"]`)!.click()
  await settle()
}
async function startRun() {
  await openPanel('run')
  button(t('workflow.runWorkflow'))!.click()
  await settle()
}
async function addNode(type: string) {
  await closePanel()
  const store = useWorkflowStore()
  const position = nextNodePosition(type, store.draft.nodes, store.registry!, store.nodeMinimums)
  const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
  const bounds = svg.getBoundingClientRect()
  svg.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: bounds.left + store.pan.x + position.x * store.zoom, clientY: bounds.top + store.pan.y + position.y * store.zoom }))
  await settle()
  root!.querySelector<HTMLButtonElement>(`[data-add-kind="${type}"]`)!.click()
  await settle()
}

function node(type: string, index = 0) {
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

describe('workflow localization', () => {
  it('renders English settings, controls, and accessible port labels without translating user filenames', async () => {
    setLocale('en')
    setup(undefined, createPinia(), { audioName: '用户录音.wav' })
    await settle()
    expect(root!.querySelector('h2')?.textContent).toContain('Node workflow')
    expect(button('Validate & save')).toBeTruthy()
    expect(root!.querySelector('[data-open-panel=run]')).toBeTruthy()
    expect(root!.querySelector('[aria-label="Workflow tools"]')).toBeTruthy()
    expect(root!.querySelector('[aria-label="Output port audio"] title')?.textContent).toBe('Output port audio')
    expect(root!.textContent).toContain('用户录音.wav')
    node('audio_output').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.workflow-settings-modal')).toBeNull()
    expect(document.activeElement).toBe(node('audio_output').querySelector('input'))
    expect(button('Edit node settings')).toBeUndefined()
    expect(node('audio_output')?.textContent).toContain('Name / prefix')
    expect((node('audio_output').querySelector('input[data-field="filename_stem"]') as HTMLInputElement).value).toBe('audio-copy')
    expect(root!.textContent).not.toMatch(/[节点工作流设置]/)
  })

  it('keeps inline invalid drafts and errors intact while translating them', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const graph = JSON.stringify(store.draft)
    const signature = executionSignature(store.draft, store.registry!)
    const calls = fetchMock.mock.calls.length
    node('audio_output').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    const filename = node('audio_output').querySelector<HTMLInputElement>('input[data-field="filename_stem"]')!
    filename.value = ''
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.node-field-error')?.textContent).toContain('长度小于最小长度')
    expect(store.draft.nodes.find((item) => item.id === 'output')?.config.filename_stem).toBe('')
    expect(store.dirty).toBe(true)
    setLocale('en')
    await settle()
    expect(root!.querySelector('.workflow-settings-modal')).toBeNull()
    expect(document.activeElement).toBe(node('audio_output').querySelector('input'))
    expect(filename.value).toBe('')
    expect(JSON.stringify(store.draft)).not.toBe(graph)
    expect(executionSignature(store.draft, store.registry!)).not.toBe(signature)
    expect(store.dirty).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(calls)
    setLocale('zh-CN')
    await settle()
    expect(root!.querySelector('.node-field-error')?.textContent).toContain('长度小于最小长度')
  })

  it('switches node field labels and dirty presentation without changing any execution data or run snapshot', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const run = runBody({ status: 'completed' })
    run.nodes.forEach((record) => { record.status = 'completed' })
    store.run = run
    const cleanDraft = JSON.stringify(store.draft)
    const snapshot = JSON.stringify(store.run)
    const cleanSignature = executionSignature(store.draft, store.registry!)
    const calls = fetchMock.mock.calls.length
    expect(store.runMatchesDraft).toBe(true)
    setLocale('en')
    await settle()
    expect(store.dirty).toBe(false)
    expect(store.runMatchesDraft).toBe(true)
    expect(JSON.stringify(store.draft)).toBe(cleanDraft)
    expect(executionSignature(store.draft, store.registry!)).toBe(cleanSignature)
    expect(JSON.stringify(store.run)).toBe(snapshot)
    await openPanel('run')
    expect(root!.textContent).toContain('Snapshot matches the current canvas')
    await closePanel()
    store.addNode('asr')
    await settle()
    const dirtyDraft = JSON.stringify(store.draft)
    const dirtySignature = executionSignature(store.draft, store.registry!)
    const newNode = root!.querySelector<SVGGElement>('.workflow-node.is-asr')!
    newNode.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    expect(node('asr')?.textContent).toContain('Language')
    expect(store.draft.nodes.find((item) => item.type === 'asr')?.config).toEqual({ provider: 'mock', language: 'ja' })
    setLocale('zh-CN')
    await settle()
    expect(node('asr')?.textContent).toContain('语言')
    expect(store.dirty).toBe(true)
    expect(store.runMatchesDraft).toBe(false)
    expect(JSON.stringify(store.draft)).toBe(dirtyDraft)
    expect(executionSignature(store.draft, store.registry!)).toBe(dirtySignature)
    expect(JSON.stringify(store.run)).toBe(snapshot)
    expect(fetchMock).toHaveBeenCalledTimes(calls)
  })

  it('retranslates stored API errors and notices while keeping unknown server errors verbatim', async () => {
    setup((path, init) => path.endsWith('/workflow-runs') && init?.method === 'POST'
      ? Promise.resolve(response({ error: { code: 'AUDIO_BUSY', message: '此音频忙碌' } }, 409)) : undefined)
    await settle()
    const store = useWorkflowStore()
    await expect(store.startRun()).rejects.toMatchObject({ name: 'AUDIO_BUSY' })
    expect(store.error).toContain('正在运行')
    setLocale('en')
    await settle()
    expect(store.error).toBe('This audio already has a running or queued task')
    store.setError(new Error('插件保留的服务端错误'))
    setLocale('zh-CN')
    expect(store.error).toBe('插件保留的服务端错误')
    store.setError('')
    await store.save()
    expect(store.notice).toBe('节点工作流已保存。')
    setLocale('en')
    expect(store.notice).toBe('Workflow saved.')
    expect(store.error).toBe('')
  })

  it('keeps backend validation errors and machine references unchanged while translating their display', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const issue = { code: 'INVALID_PORT', message: '服务器端口错误', edge_id: 'edge-extension' }
    const issues = [issue]
    expect(workflowIssueLabel(issue)).toBe('连接端口不匹配或不符合能力目录定义。')
    store.validationIssues = issues
    const raw = JSON.stringify(store.validationIssues)
    setLocale('en')
    await settle()
    expect(workflowIssueLabel(issue)).toBe('The connection ports do not match or are not defined by the capability catalog.')
    expect(root!.querySelector('.workflow-issues')?.textContent).toContain('The connection ports do not match or are not defined by the capability catalog.')
    expect(JSON.stringify(store.validationIssues)).toBe(raw)
    expect(issue.code).toBe('INVALID_PORT')
    expect(workflowIssueLabel({ code: 'INVALID_PORT', message: '服务器端口错误', edge_id: 'edge-extension' })).toBe('The connection ports do not match or are not defined by the capability catalog.')
    expect(workflowIssueLabel({ code: 'EXTENSION_VALIDATION', message: '插件原始错误', node_id: 'extension-1' })).toBe('插件原始错误')
  })

  it('translates statuses, dates, known run errors and legacy type-based node errors without mutating history', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const failed = runBody({ status: 'partial', error: { code: 'PARTIAL_FAILURE', message: '后端部分失败' } })
    failed.nodes[0].status = 'completed'
    failed.nodes[1].status = 'blocked'
    failed.nodes[1].error = { type: 'DEPENDENCY_BLOCKED', message: '后端上游阻塞' }
    store.run = failed
    store.history = [summaryBody({ status: 'partial' })]
    const snapshot = JSON.stringify(store.run)
    const history = JSON.stringify(store.history)
    setLocale('en')
    await settle()
    await openPanel('run')
    expect(root!.textContent).toContain('Partially completed')
    expect(root!.textContent).toContain('Blocked')
    expect(root!.textContent).toContain('Some output nodes failed.')
    expect(root!.textContent).toContain('upstream dependency did not complete successfully')
    expect(root!.textContent).toContain(new Intl.DateTimeFormat('en', { dateStyle: 'short', timeStyle: 'medium', hour12: false }).format(new Date(failed.created_at)))
    expect(workflowErrorLabel({ code: 'EXTENSION_ERROR', message: '未知服务错误' })).toBe('未知服务错误')
    setLocale('zh-CN')
    await settle()
    expect(root!.textContent).toContain('部分成功')
    expect(root!.textContent).toContain('上游节点未成功完成')
    expect(JSON.stringify(store.run)).toBe(snapshot)
    expect(JSON.stringify(store.history)).toBe(history)
  })

  it.each([
    ['checking', 'Checking the artifact file…'],
    ['missing', 'The artifact is missing and cannot be downloaded.'],
    ['failed', 'The artifact check failed. You can retry.'],
    ['available', 'The artifact is available.'],
  ] as const)('translates the %s artifact state', async (state, label) => {
    setLocale('en')
    setup()
    await settle()
    const store = useWorkflowStore()
    const completed = runBody({ status: 'completed' })
    completed.nodes[1].status = 'completed'
    completed.nodes[1].artifact = structuredClone(outputArtifact)
    store.run = completed
    store.outputChecks = { output: { state } }
    if (state === 'available') store.outputs = { output: structuredClone(outputArtifact) }
    await settle()
    await openPanel('artifacts')
    expect(root!.querySelector('.workflow-output-state')?.textContent).toBe(label)
    if (state === 'available') expect(button('Download audio-copy.wav')).toBeTruthy()
    if (state === 'failed') expect(button('Retry check')).toBeTruthy()
  })

  it('keeps extension labels and configuration editable in English without describing it as audio output', async () => {
    const extended = structuredClone(testCapabilities)
    extended.workflow.nodes.push({
      type: 'extension', label: '插件处理节点', add_label: 'Plugin node', id_prefix: 'plugin',
      input_ports: ['audio'], output_ports: ['audio'], terminal: false, required_inbound: 1,
      config: { kind: 'extension', fields: [
        { key: 'mode', label: '插件模式', type: 'select', required: true, default: 'future', options: [{ value: 'future', label: '扩展选项' }] },
        { key: 'custom', label: 'User field', type: 'string', default: '保留原文' },
      ] },
    })
    setLocale('en')
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(extended)) : undefined)
    await settle()
    await addNode('extension')
    await settle()
    const store = useWorkflowStore()
    const extension = store.draft.nodes.find((item) => item.type === 'extension')!
    const card = root!.querySelector<SVGGElement>('.workflow-node.is-extension')!
    expect(card.textContent).toContain('插件处理节点')
    expect(card.textContent).not.toContain('AUDIO OUTPUT')
    expect(card.textContent).not.toContain('audio-copy')
    expect(root!.querySelector('.workflow-inspector')).toBeNull()
    card.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    const fieldGroup = card
    expect(fieldGroup.textContent).toContain('插件模式')
    expect(fieldGroup.textContent).toContain('扩展选项')
    expect(fieldGroup.textContent).not.toContain('copies bytes and media type')
    const input = fieldGroup.querySelector<HTMLInputElement>('input[type=text]')!
    input.value = '用户新配置'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    expect(store.draft.nodes.find((item) => item.id === extension.id)?.config).toEqual({ mode: 'future', custom: '用户新配置' })
    const draft = JSON.stringify(store.draft)
    setLocale('zh-CN')
    await settle()
    expect(JSON.stringify(store.draft)).toBe(draft)
    expect(root!.querySelector('[data-add-node]')).toBeTruthy()
    expect(store.registry!.label('extension')).toBe('插件处理节点')
  })

  it('uses English delete confirmations and live localized external errors', async () => {
    setLocale('en')
    setup()
    await settle()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    node('audio_output').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    node('audio_output').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 500, clientY: 200 }))
    await settle()
    button('Delete node')!.click()
    expect(confirm).toHaveBeenCalledWith('Delete node “Audio output”? Its connections will also be removed.')
    const store = useWorkflowStore()
    store.setError(message('workflow.saveFirst'))
    expect(store.error).toBe('Save the current workflow first.')
    setLocale('zh-CN')
    expect(store.error).toBe('请先保存当前工作流。')
  })

  it('uses a live translated fallback for malformed successful workflow responses', async () => {
    setup((path) => path.endsWith('/workflow') ? Promise.resolve({
      ok: true, status: 200, json: async () => { throw new SyntaxError('Unexpected token') },
    } as unknown as Response) : undefined)
    await settle()
    const store = useWorkflowStore()
    expect(store.error).toBe('节点工作流响应不完整，请检查后端版本后重试。')
    setLocale('en')
    await settle()
    expect(store.error).toBe('The workflow response is incomplete. Check the backend version and retry.')
    expect(root!.querySelector('[role=alert]')?.textContent).not.toContain('Unexpected token')
  })
})

describe('workflow validation and helpers', () => {
  it('uses server-provided defaults and ignores layout and edge IDs in execution signatures', () => {
    const changedLayout = { nodes: [{ ...input, position: { x: 140, y: 160 } }, output], edges: [{ ...edge, id: 'renamed-edge' }] }
    expect(executionSignature({ nodes: [input, output], edges: [edge] }, testRegistry)).toBe(executionSignature(changedLayout, testRegistry))
    expect(createDefaultWorkflow(testRegistry)).toEqual(testCapabilities.workflow.default_graph)
  })

  it('keeps unknown configuration visible in dirty signatures even when registry defaults are reordered', () => {
    const omitted = { nodes: [input, { ...preprocess, config: { provider: 'mock', command: 'unsafe external command' } as Record<string, unknown> }, output], edges: [preprocessEdge, preprocessToOutput] }
    const explicit = { nodes: [input, { ...preprocess, config: { command: 'unsafe external command', provider: 'mock' } }, output], edges: [preprocessEdge, preprocessToOutput] }
    expect(executionSignature(omitted, testRegistry)).toBe(executionSignature(explicit, testRegistry))
    expect(executionSignature(omitted, testRegistry)).not.toBe(executionSignature({ ...omitted, nodes: [input, preprocess, output] }, testRegistry))
    expect(executionSignature(omitted, testRegistry)).toContain('unsafe external command')
  })

  it('includes registry defaults in signatures for a catalog-only node', () => {
    const capabilities = structuredClone(testCapabilities)
    capabilities.workflow.nodes.push({
      type: 'denoise', label: '降噪', id_prefix: 'denoise', input_ports: ['audio'], output_ports: ['audio'], terminal: false,
      required_inbound: 1, removable: true, config: { kind: 'denoise', fields: [
        { key: 'strength', type: 'integer', required: true, default: 2, minimum: 1, maximum: 5 },
      ] }, position_x: 386,
    })
    const registry = createNodeRegistry(readCapabilities(capabilities))
    const denoise = { id: 'denoise-1', type: 'denoise', config: {}, position: { x: 386, y: 120 } }
    const graph = { nodes: [input, denoise, output], edges: [preprocessEdge, preprocessToOutput] }
    expect(executionSignature(graph, registry)).toContain('"strength":2')
    expect(executionSignature(graph, registry)).not.toBe(executionSignature({ ...graph, nodes: [input, { ...denoise, config: { strength: 1 } }, output] }, registry))
    expect(executionSignature(graph)).not.toBe(executionSignature(graph, registry))
  })

  it('keeps a loaded graph clean with registry defaults in its saved signature', async () => {
    const capabilities = structuredClone(testCapabilities)
    capabilities.workflow.nodes.push({
      type: 'denoise', label: '降噪', id_prefix: 'denoise', input_ports: ['audio'], output_ports: ['audio'], terminal: false,
      required_inbound: 1, removable: true, config: { kind: 'denoise', fields: [
        { key: 'strength', type: 'integer', required: true, default: 2, minimum: 1, maximum: 5 },
      ] }, position_x: 386,
    })
    const document = documentBody()
    const denoise = { id: 'denoise-1', type: 'denoise', config: {}, position: { x: 386, y: 120 } }
    document.nodes = structuredClone([input, denoise, output])
    document.edges = structuredClone([preprocessEdge, preprocessToOutput])
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(capabilities)) : path.endsWith('/workflow') ? Promise.resolve(response(document)) : undefined)
    await settle()
    expect(useWorkflowStore().dirty).toBe(false)
  })

  it('places every new node without overlapping any existing node rectangle', () => {
    const firstPre = nextNodePosition('preprocess', [input, output], testRegistry)
    expect(firstPre).toEqual({ x: 286, y: output.position.y + workflowNodeHeight(output.type, testRegistry) + WORKFLOW_NODE_ROW_GAP })
    const secondPre = nextNodePosition('preprocess', [input, output, { ...preprocess, position: firstPre }], testRegistry)
    expect(secondPre).toEqual({ x: 286, y: firstPre.y + workflowNodeHeight(preprocess.type, testRegistry) + WORKFLOW_NODE_ROW_GAP })
    expect(nextNodePosition('audio_output', [input, output, { ...preprocess, position: firstPre }], testRegistry)).toEqual({ x: 420, y: secondPre.y })
    expect(nextNodePosition('audio_output', [{ ...input, position: { x: 80, y: 120 } }], testRegistry)).toEqual({ x: 500, y: 120 })
    const all = [input, output, { ...preprocess, position: firstPre }, { ...preprocess, id: 'preprocess-2', position: secondPre }]
    for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) {
      const overlapX = Math.min(all[i].position.x + workflowNodeSize(all[i], testRegistry).width, all[j].position.x + workflowNodeSize(all[j], testRegistry).width) - Math.max(all[i].position.x, all[j].position.x)
      const overlapY = Math.min(all[i].position.y + workflowNodeHeight(all[i].type, testRegistry), all[j].position.y + workflowNodeHeight(all[j].type, testRegistry)) - Math.max(all[i].position.y, all[j].position.y)
      expect(overlapX <= 0 || overlapY <= 0).toBe(true)
    }
  })

  it('deep-clones reactive drafts with JSON-safe data', () => {
    const draft = reactive(createDefaultWorkflow(testRegistry))
    const copy = cloneDocument(draft)
    expect(copy).toEqual(draft)
    expect(copy).not.toBe(draft)
    expect(copy.nodes[0]).not.toBe(draft.nodes[0])
  })

  it('uses catalog defaults and validates only declared field metadata', () => {
    expect(testRegistry.createDefaultConfig('preprocess')).toEqual({ provider: 'mock' })
    expect(testRegistry.createDefaultConfig('asr')).toEqual({ provider: 'mock', language: 'ja' })
    expect(testRegistry.validateConfig('asr', { provider: 'mock', language: 'ja' })).toEqual([])
    expect(testRegistry.validateConfig('asr', { provider: 'unsupported', language: 'ja' }).map((issue) => issue.code)).toContain('INVALID_NODE_CONFIG')
    expect(testRegistry.validateConfig('asr', { provider: 'mock', language: 'x'.repeat(33) }).map((issue) => issue.code)).toContain('INVALID_NODE_CONFIG')
    expect(testRegistry.validateConfig('audio_output', { filename_stem: 'bad/name' })).toEqual([])
    expect(testRegistry.capabilities.asr.providers.map((provider) => provider.value)).toEqual(['mock'])
  })

  it('enforces the edge limit published by the backend capability catalog', async () => {
    const edgeLimit = testRegistry.capabilities.constraints.maxEdges
    setActivePinia(createPinia())
    const store = useWorkflowStore()
    vi.stubGlobal('fetch', vi.fn((request: RequestInfo | URL) => String(request).endsWith('/capabilities') ? Promise.resolve(response(testCapabilities)) : Promise.resolve(response(documentBody()))))
    await store.load('p1', 'a1')
    store.beginDraftEditing()
    store.setDraft({
      nodes: [
        input,
        ...Array.from({ length: edgeLimit + 1 }, (_, index) => ({ ...output, id: `output-${index}`, position: { x: 420, y: 120 + index } })),
      ],
      edges: [
        ...Array.from({ length: edgeLimit - 1 }, (_, index) => ({
          id: `edge-${index}`, source: 'input', source_port: 'audio' as const, target: `output-${index}`, target_port: 'audio' as const,
        })),
        { id: 'replace-target', source: 'input', source_port: 'audio' as const, target: `output-${edgeLimit - 1}`, target_port: 'audio' as const },
      ],
    })
    const existing = JSON.parse(JSON.stringify(store.draft.edges.find((item) => item.id === 'replace-target')))
    expect(store.connect('input', `output-${edgeLimit}`)).toBe(false)
    expect(store.draft.edges).toHaveLength(edgeLimit)
    expect(store.draft.edges).toEqual(expect.arrayContaining([existing]))
    expect(store.draft.edges.length).toBeLessThanOrEqual(edgeLimit)
    expect(store.error).toContain(String(edgeLimit))
  })
  it('resolves relative artifact URLs against the API origin and rejects unsupported schemes', () => {
    expect(resolveArtifactUrl('/api/v1/outputs/output/download', '/api/v1', 'https://ui.example'))
      .toBe('https://ui.example/api/v1/outputs/output/download')
    expect(resolveArtifactUrl('outputs/output/download', 'https://api.example/api/v1', 'https://ui.example'))
      .toBe('https://api.example/api/v1/outputs/output/download')
    expect(resolveArtifactUrl('javascript:alert(1)', '/api/v1', 'https://ui.example')).toBe('')
  })
})

describe('capability catalog and node registry', () => {
  it('builds node validation and typed ports from the authoritative catalog, including a new node type', () => {
    const extended = structuredClone(testCapabilities)
    extended.workflow.nodes.push({
      type: 'denoise', label: '降噪', id_prefix: 'denoise', input_ports: ['audio'], output_ports: ['audio'], terminal: false,
      required_inbound: 1, removable: true, config: { kind: 'denoise', fields: [
        { key: 'strength', label: '强度', type: 'integer', required: true, default: 2, minimum: 1, maximum: 5 },
        { key: 'mode', label: '模式', type: 'select', required: false, default: 'soft', options: [{ id: 'soft', label: '柔和' }, { id: 'hard', label: '强力' }] },
      ] }, position_x: 386,
    })
    const registry = createNodeRegistry(readCapabilities(extended))
    const denoise = { id: 'denoise-1', type: 'denoise', config: { strength: 2, mode: 'soft' }, position: { x: 386, y: 120 } }
    expect(registry.createDefaultConfig('denoise')).toEqual({ strength: 2, mode: 'soft' })
    expect(registry.outputPortForNode('denoise')).toBe('audio')
    expect(registry.validateConfig('denoise', denoise.config)).toEqual([])
    expect(registry.validateConfig('denoise', { strength: 9, mode: 'other' }).map((issue) => issue.code))
      .toEqual(expect.arrayContaining(['INVALID_NODE_CONFIG']))
  })

  it('adds and edits a catalog-only node through the generic editor without changing a fixed type branch', async () => {
    const extended = structuredClone(testCapabilities)
    extended.workflow.nodes.push({
      type: 'denoise', label: '降噪', id_prefix: 'denoise', input_ports: ['audio'], output_ports: ['audio'], terminal: false,
      required_inbound: 1, removable: true, config: { kind: 'denoise', fields: [
        { key: 'strength', label: '强度', type: 'integer', required: true, default: 2, minimum: 1, maximum: 5 },
      ] }, position_x: 386,
    })
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(extended)) : undefined)
    await settle()
    const store = useWorkflowStore()
    await addNode('denoise')
    expect(store.draft.nodes.find((item) => item.type === 'denoise')?.config).toEqual({ strength: 2 })
    const denoiseNode = Array.from(root!.querySelectorAll<SVGGElement>('.workflow-node')).find((item) => item.getAttribute('class')?.includes('is-denoise'))!
    denoiseNode.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    const strength = denoiseNode.querySelector<HTMLInputElement>('input[data-field="strength"]')!
    strength.value = '4'; strength.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    expect(store.draft.nodes.find((item) => item.type === 'denoise')?.config).toEqual({ strength: 4 })
  })

  it('does not duplicate builtin fields in the generic editor for builtin nodes', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    await addNode('preprocess')
    const pre = store.draft.nodes.find((item) => item.type === 'preprocess')!
    store.selectNode(pre.id); await settle()
    node('preprocess').dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(node('preprocess').querySelectorAll('.node-config-row').length).toBe(1)
    expect(node('preprocess')?.textContent).toContain('Provider')
  })

  it('renders and resaves a legacy graph through the live capability registry', async () => {
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(testCapabilities)) : undefined)
    await settle()
    const store = useWorkflowStore()
    expect(store.document?.nodes.map((node) => node.type)).toEqual(['audio_input', 'audio_output'])
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(2)
    const before = JSON.stringify(store.draft)
    expect(store.dirty).toBe(false)
    button('校验并保存')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await settle()
    // Unchanged legacy graph remains renderable and is not mutated by capability loading.
    expect(JSON.stringify(store.draft)).toBe(before)
  })

  it('accepts the current backend capability contract without frontend-only layout fields', () => {
    const backendShape = {
      schema_version: '1.0',
      workflow: {
        schema_version: '1.2',
        nodes: [
          { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
          { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [{ key: 'filename_stem', type: 'string', required: false, default: 'audio-copy', minimum: null, maximum: null, min_length: 1, max_length: 128, validator: 'safe_filename_stem', options: [] }] } },
        ],
        ports: ['audio'],
        constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
        default_graph: testCapabilities.workflow.default_graph,
      },
      asr: { backends: ['qwen3'], status: 'available', message: null, truncated: false, warnings: [] },
      export: { formats: [{ value: 'srt', label: 'SRT' }], naming_modes: [{ value: 'language', label: '按语言命名' }] },
    }
    const registry = createNodeRegistry(readCapabilities(backendShape))
    expect(registry.kinds.map((kind) => kind.type)).toEqual(['audio_input', 'audio_output'])
    expect(registry.outputPortForNode('audio_input')).toBe('audio')
    expect(registry.createDefaultConfig('audio_output')).toEqual({ filename_stem: 'audio-copy' })
  })

  it('reads node capabilities and graph policy from the live capability contract', () => {
    const live = {
      schema_version: '1.0',
      workflow: {
        schema_version: '1.2',
        ports: ['audio', 'transcript', 'text'],
        nodes: [
          { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
          { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
        ],
        constraints: { max_nodes: 8, max_edges: 12, min_sources: 1, max_sources: 2, min_terminals: 2 },
        default_graph: testCapabilities.workflow.default_graph,
      },
      asr: { providers: [{ id: 'mock', label: 'Mock' }] },
      export: {
        formats: [{ id: 'txt', label: 'TXT' }, { id: 'srt', label: 'SRT' }, { id: 'vtt', label: 'VTT' }, { id: 'lrc', label: 'LRC' }],
        naming_modes: [],
      },
    }
    const parsed = readCapabilities(live)
    expect(parsed.nodeTypes).toEqual(['audio_input', 'audio_output'])
    expect(parsed.asr.providers.map((item) => item.value)).toEqual(['mock'])
    expect(parsed.constraints).toEqual({ maxNodes: 8, maxEdges: 12, minSources: 1, maxSources: 2, minTerminals: 2 })
    expect(parsed.defaultGraph).toEqual(testCapabilities.workflow.default_graph)
  })

  it('rejects a non-current capabilities schema version', () => {
    expect(() => readCapabilities({ ...testCapabilities, schema_version: '2.0' })).toThrow('schema_version')
  })

  it('rejects malformed or missing catalog data instead of falling back to local node defaults', () => {
    const brokenPort = structuredClone(testCapabilities)
    brokenPort.workflow.nodes[1].output_ports = ['video']
    expect(() => readCapabilities(brokenPort)).toThrow('端口未声明')
    const duplicate = structuredClone(testCapabilities)
    duplicate.workflow.nodes.push({ ...duplicate.workflow.nodes[0] })
    expect(() => readCapabilities(duplicate)).toThrow('节点类型重复')
  })

  it('blocks workflow loading with an actionable error when capability discovery is unavailable', async () => {
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response({ error: { message: '能力发现失败' } }, 503)) : undefined)
    await settle()
    const store = useWorkflowStore()
    expect(store.capabilities).toBeNull()
    expect(store.error).toContain('能力发现失败')
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow'))).toBe(false)
    expect(store.draft.nodes).toEqual([])
  })
})

describe('inline catalog field editing', () => {
  async function mountFields() {
    const catalog = structuredClone(testCapabilities)
    catalog.workflow.nodes.find((entry: any) => entry.type === 'audio_output').config.fields.push(
      { key: 'count', label: 'Count', type: 'integer', default: 2, minimum: 1, maximum: 4 },
      { key: 'ratio', label: 'Ratio', type: 'number', default: 0.5, minimum: 0, maximum: 1 },
      { key: 'enabled', label: 'Enabled', type: 'boolean', default: true },
      { key: 'mode', label: 'Mode', type: 'select', default: 'a', options: [{ value: 'a', label: 'Choice A' }, { value: 'b', label: 'Choice B' }] },
      { key: 'details', label: 'Details', type: 'json', default: {} },
    )
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(catalog)) : undefined)
    await settle()
    return useWorkflowStore()
  }
  function control(key: string) { return node('audio_output').querySelector<HTMLInputElement>(`[data-field="${key}"]`)! }
  async function edit(key: string, value: string, event = 'input') {
    control(key).value = value
    control(key).dispatchEvent(new Event(event, { bubbles: true }))
    await settle()
  }
  function config() { return useWorkflowStore().draft.nodes.find((entry) => entry.type === 'audio_output')!.config }

  it('preserves empty and incomplete numbers, checks catalog bounds and integer type before any HTTP', async () => {
    const store = await mountFields()
    for (const [text, expected] of [['', '类型无效'], ['-', '类型无效'], ['1.5', '类型无效'], ['0', '小于最小值'], ['5', '大于最大值']]) {
      await edit('count', text)
      expect(control('count').value).toBe(text)
      expect(control('count').getAttribute('aria-invalid')).toBe('true')
      expect(node('audio_output').querySelector('.node-field-error')?.textContent).toContain(expected)
      expect(store.dirty).toBe(true)
      const calls = fetchMock.mock.calls.length
      await store.validate(); await store.save()
      expect(fetchMock.mock.calls.length).toBe(calls)
    }
    expect(config().count).toBe(5)
    await edit('count', '3')
    await edit('ratio', '0.75')
    expect(config()).toMatchObject({ count: 3, ratio: 0.75 })
    expect(control('count').getAttribute('aria-invalid')).toBe('false')
    await store.save()
    expect(JSON.parse(String((putCalls()[0][1] as RequestInit).body)).nodes.find((entry: WorkflowNode) => entry.type === 'audio_output').config).toMatchObject({ count: 3, ratio: 0.75 })
  })

  it('keeps partial JSON across selection and locale changes, blocks save/run, then saves a repaired object', async () => {
    const store = await mountFields()
    expect(store.dirty).toBe(false)
    await edit('details', '{ "incomplete":')
    expect(config().details).toBe('{ "incomplete":')
    expect(store.dirty).toBe(true)
    const leave = vi.fn()
    expect(app!._instance!.exposed!.requestClose(leave)).toBe('pending')
    expect(leave).not.toHaveBeenCalled()
    await settle()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    button(t('workflow.continueEditing'))!.click(); await settle()
    store.selectNode('input')
    setLocale('en'); await settle()
    expect(control('details').value).toBe('{ "incomplete":')
    expect(node('audio_output').querySelector('.node-field-error')?.textContent).toContain('JSON')
    await openPanel('run')
    expect(button('Save before running') || button('Save to run')).toBeTruthy()
    await closePanel()
    const calls = fetchMock.mock.calls.length
    await store.validate(); await store.save(); await store.startRun()
    expect(fetchMock.mock.calls.length).toBe(calls)
    for (const value of ['[]', '42', 'null', '"text"']) {
      await edit('details', value)
      expect(config().details).toBe(value)
      expect(control('details').getAttribute('aria-invalid')).toBe('true')
    }
    const formatted = '{\n  "done": true\n}'
    await edit('details', formatted)
    expect(config().details).toEqual({ done: true })
    expect(control('details').value).toBe(formatted)
    setLocale('zh-CN'); await settle()
    expect(control('details').value).toBe(formatted)
    await store.validate(); await store.save()
    expect(JSON.parse(String((putCalls()[0][1] as RequestInit).body)).nodes.find((entry: WorkflowNode) => entry.type === 'audio_output').config.details).toEqual({ done: true })
  })

  it('edits catalog booleans/options and contains pointer, keyboard and wheel events inside controls', async () => {
    const store = await mountFields()
    await edit('enabled', 'false', 'change'); await edit('mode', 'b', 'change')
    expect(config()).toMatchObject({ enabled: false, mode: 'b' })
    const target = control('filename_stem')
    target.focus(); await settle()
    expect(store.selectedNodeId).toBe('output')
    const original = { ...store.draft.nodes.find((entry) => entry.id === 'output')!.position }
    const confirm = vi.spyOn(window, 'confirm')
    for (const key of ['Delete', ' ', 'Enter', 'ArrowLeft', 'ArrowDown']) target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 20, clientY: 20 }))
    root!.querySelector('.workflow-svg')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 90, clientY: 90 }))
    control('details').dispatchEvent(new WheelEvent('wheel', { bubbles: true, ctrlKey: true, deltaY: 100 }))
    await settle()
    expect(store.zoom).toBe(1)
    expect(store.draft.nodes.find((entry) => entry.id === 'output')!.position).toEqual(original)
    expect(confirm).not.toHaveBeenCalled()
    expect(root!.querySelector('.workflow-settings-modal')).toBeNull()
    node('audio_output').dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    expect(root!.querySelector('[role="dialog"]')).toBeNull()
  })

  it('cancels port drags released on inputs or JSON editors without changing edges', async () => {
    const store = await mountFields()
    const edges = JSON.stringify(store.draft.edges)
    for (const key of ['filename_stem', 'details']) {
      node('audio_input').querySelector('.node-port-out')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
      root!.querySelector('.workflow-svg')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 300, clientY: 160 }))
      await settle()
      expect(root!.querySelector('.workflow-edge-preview')).not.toBeNull()
      control(key).dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
      await settle()
      expect(root!.querySelector('.workflow-edge-preview')).toBeNull()
      expect(JSON.stringify(store.draft.edges)).toBe(edges)
    }
  })

  it('disables controls and rejects synthetic edits while saving, validating or running', async () => {
    const store = await mountFields()
    for (const state of ['saving', 'validating', 'running']) {
      store.saving = state === 'saving'
      store.validating = state === 'validating'
      store.run = state === 'running' ? runBody({ status: 'running' }) : null
      await settle()
      expect(control('filename_stem').disabled).toBe(true)
      await edit('filename_stem', 'blocked')
      expect(config().filename_stem).toBe('audio-copy')
      store.updateNode('output', { config: { filename_stem: 'also-blocked' } })
      expect(config().filename_stem).toBe('audio-copy')
    }
    store.run = null; store.saving = false; store.validating = false
  })
})

describe('catalog resources and preview workflows', () => {
  function catalog() {
    const value = structuredClone(testCapabilities)
    value.workflow.schema_version = '1.3'
    const source = value.workflow.nodes.find((entry: any) => entry.type === 'audio_input')
    source.presentation = { resource_input: { resource_type: 'audio', config_key: 'audio_id' } }
    source.config.fields = [{ key: 'audio_id', label: 'Audio ID', type: 'string', required: false, default: null }]
    value.workflow.nodes.push({ type: 'audio_preview', label: 'Audio preview', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, config: { fields: [] }, presentation: { preview: { renderer: 'audio' } } })
    return value
  }
  const project = { id: 'p1', name: 'Empty', created_at: '', updated_at: '', source: null, source_language: 'ja', target_language: 'en', audios: [] }

  it('requires a source language in the input node for a name-only project and preserves the explicit value across interface changes', async () => {
    const newProject = { ...project, source_language: null, target_language: null }
    setup((path, init) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(catalog()))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/audios') && init?.method === 'POST') return Promise.resolve(response({ audio: { id: 'new-audio', source: { original_name: 'new.wav', language: 'xx-CUSTOM', size_bytes: 4 } } }, 201))
      return undefined
    }, createPinia(), { audioId: null, project: newProject, audios: [] })
    await settle()
    const language = node('audio_input').querySelector<HTMLInputElement>('[data-resource-language]')!
    expect(language.value).toBe('')
    const file = node('audio_input').querySelector<HTMLInputElement>('input[type=file]')!
    Object.defineProperty(file, 'files', { configurable: true, value: [new File(['RIFF'], 'new.wav')] })
    file.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect(node('audio_input').textContent).toContain('请选择或输入源语言')
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).endsWith('/audios') && (init as RequestInit)?.method === 'POST')).toBe(false)
    language.value = 'xx-CUSTOM'; language.dispatchEvent(new Event('input', { bubbles: true }))
    setLocale('en'); await settle()
    expect(language.value).toBe('xx-CUSTOM')
    expect(newProject.source_language).toBeNull()
    expect(newProject.target_language).toBeNull()
    node('audio_input').querySelectorAll<HTMLButtonElement>('button')[1]!.click(); await settle()
    const uploads = fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/audios') && (init as RequestInit)?.method === 'POST')
    expect(uploads).toHaveLength(1)
    expect(((uploads[0]![1] as RequestInit).body as FormData).get('source_language')).toBe('xx-CUSTOM')
    expect(((uploads[0]![1] as RequestInit).body as FormData).has('target_language')).toBe(false)
    expect(useWorkflowStore().boundAudioId).toBe('new-audio')
  })

  it('prefills a legacy project source language without changing its persisted metadata', async () => {
    const legacy = { ...project, source_language: null, source: { original_name: 'legacy.wav', language: 'ko', size_bytes: 4 } }
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(catalog())) : path.endsWith('/upload-policy') ? Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 })) : undefined, createPinia(), { audioId: null, project: legacy, audios: [] })
    await settle()
    const language = node('audio_input').querySelector<HTMLInputElement>('[data-resource-language]')!
    expect(language.value).toBe('ko')
    language.value = 'en'; language.dispatchEvent(new Event('input', { bubbles: true }))
    setLocale('en'); await settle()
    expect(language.value).toBe('en')
    expect(legacy.source_language).toBeNull()
    expect(legacy.source.language).toBe('ko')
  })

  it('loads and saves an empty-audio project graph without requesting empty history routes', async () => {
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(catalog())) : path.endsWith('/upload-policy') ? Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 })) : undefined, createPinia(), { audioId: null, project, audios: [] })
    await settle()
    const store = useWorkflowStore()
    expect(store.document).not.toBeNull()
    expect(node('audio_input').querySelector('[data-resource="audio"]')).not.toBeNull()
    expect(node('audio_input').querySelector('[data-field="audio_id"]')).toBeNull()
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/audios/'))).toBe(false)
    store.updateNode('output', { config: { filename_stem: 'empty-project-copy' } })
    await store.validate(); await store.save()
    expect(putCalls()).toHaveLength(1)
    await openPanel('run')
    expect(button('运行节点工作流')!.disabled).toBe(true)
  })

  it('retries an input-node upload, preserves the graph, and saves the new resource binding', async () => {
    let attempts = 0
    setup((path, init) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(catalog()))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/audios') && init?.method === 'POST') {
        attempts++
        return Promise.resolve(attempts === 1 ? response({ error: { code: 'UPLOAD_FAILED', message: 'Please retry' } }, 500) : response({ audio: { id: 'new-audio', source: { original_name: 'new.wav', language: 'ko', size_bytes: 4 } } }, 201))
      }
      return undefined
    }, createPinia(), { audioId: null, project, audios: [] })
    await settle()
    const store = useWorkflowStore()
    store.updateNode('output', { config: { filename_stem: 'keep-my-draft' } })
    const language = node('audio_input').querySelector<HTMLInputElement>('[data-resource-language]')!
    expect(language.value).toBe('ja')
    language.value = 'ko'; language.dispatchEvent(new Event('input', { bubbles: true }))
    const input = node('audio_input').querySelector<HTMLInputElement>('input[type=file]')!
    Object.defineProperty(input, 'files', { configurable: true, value: [new File(['RIFF'], 'new.wav', { type: 'audio/wav' })] })
    input.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect(node('audio_input').textContent).toContain('Please retry')
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.config).toEqual({})
    node('audio_input').querySelectorAll<HTMLButtonElement>('button')[1]!.click(); await settle()
    expect(attempts).toBe(2)
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.config.audio_id).toBe('new-audio')
    expect(store.draft.nodes.find((entry) => entry.id === 'output')!.config.filename_stem).toBe('keep-my-draft')
    expect(store.audioId).toBe('new-audio')
    const request = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/audios') && (init as RequestInit)?.method === 'POST')!
    expect(((request[1] as RequestInit).body as FormData).get('source_language')).toBe('ko')
    await store.save()
    expect(JSON.parse(String((putCalls()[0][1] as RequestInit).body)).nodes.find((entry: WorkflowNode) => entry.id === 'input').config.audio_id).toBe('new-audio')
  })

  it('selects existing resources as draft bindings and locks navigation while an upload is pending', async () => {
    const pending = deferred<Response>()
    const audios = [{ id: 'existing', source: { original_name: 'old.wav', language: 'ja', size_bytes: 4 } }]
    setup((path, init) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(catalog()))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/audios') && init?.method === 'POST') return pending.promise
      return undefined
    }, createPinia(), { audioId: null, project, audios })
    await settle()
    const store = useWorkflowStore()
    const select = node('audio_input').querySelector<HTMLSelectElement>('[data-resource="audio"]')!
    select.value = 'existing'; select.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.config.audio_id).toBe('existing')
    expect(store.audioId).toBe('existing')
    const file = node('audio_input').querySelector<HTMLInputElement>('input[type=file]')!
    Object.defineProperty(file, 'files', { configurable: true, value: [new File(['x'], 'pending.wav')] })
    file.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect(store.resourceUploading).toBe(true)
    expect(node('audio_input').querySelector('progress')).not.toBeNull()
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(app!._instance!.exposed!.requestClose(vi.fn())).toBe('blocked')
    expect(await store.setAudioContext('other')).toBe(false)
    const calls = fetchMock.mock.calls.length
    await store.validate(); await store.save(); await store.startRun()
    expect(fetchMock.mock.calls.length).toBe(calls)
    pending.resolve(response({ error: { code: 'FAILED', message: 'Upload failed' } }, 500)); await settle()
    expect(store.resourceUploading).toBe(false)
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.config.audio_id).toBe('existing')
  })

  it('describes the bound input resource separately from the selected history audio with legacy fallback', async () => {
    const bound = { id: 'bound', source: { original_name: 'input-resource.wav', language: 'ja', size_bytes: 4 } }
    setup((path) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(catalog()))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/workflow')) { const graph = documentBody(); graph.nodes[0]!.config = { audio_id: 'bound' }; return Promise.resolve(response(graph)) }
      return undefined
    }, createPinia(), { audioId: 'a1', audioName: 'history-resource.wav', project, audios: [bound] })
    await settle()
    expect(node('audio_input').querySelector<HTMLSelectElement>('[data-resource="audio"]')!.value).toBe('bound')
    expect(node('audio_input').textContent).toContain('input-resource.wav')
    expect(useWorkflowStore().audioId).toBe('a1')
    expect(useWorkflowStore().boundAudioId).toBe('bound')
    expect(root!.querySelector('.workflow-inspector')).toBeNull()
    setLocale('en'); await settle()
    expect(node('audio_input').textContent).toContain('input-resource.wav')
    useWorkflowStore().updateNode('input', { config: {} }); await settle()
    expect(node('audio_input').querySelector<HTMLSelectElement>('[data-resource="audio"]')!.value).toBe('')
    expect(useWorkflowStore().boundAudioId).toBeNull()
  })

  it('changes history context without reloading or discarding the graph and ignores stale run responses', async () => {
    const slow = deferred<Response>()
    setup((path) => path.endsWith('/run-old') ? slow.promise : undefined)
    await settle()
    const store = useWorkflowStore()
    store.updateNode('output', { config: { filename_stem: 'draft-stays' } })
    const draft = JSON.stringify(store.draft)
    const old = store.loadRun('run-old')
    await store.setAudioContext('a2')
    slow.resolve(response(runBody({ id: 'run-old', status: 'completed', audio_id: 'a1' })))
    await old; await settle()
    expect(store.run).toBeNull()
    expect(store.audioId).toBe('a2')
    expect(JSON.stringify(store.draft)).toBe(draft)
    expect(store.dirty).toBe(true)
    expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/workflow'))).toHaveLength(1)
  })

  it('runs a preview-only graph on its bound resource and renders the selected run preview without download controls', async () => {
    const source = { ...input, config: { audio_id: 'bound-audio' } }
    const preview = { id: 'preview', type: 'audio_preview', config: {}, position: { x: 420, y: 120 } }
    const nodes = [source, preview]
    const edges = [{ ...edge, target: 'preview' }]
    const completed = runBody({ status: 'completed', audio_id: 'bound-audio', workflow: { nodes, edges }, nodes: [
      { node_id: 'preview', node_type: 'audio_preview', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: { ...outputArtifact, download_url: null }, error: null, preview: { renderer: 'audio', content_url: '/api/v1/projects/p1/audios/bound-audio/workflow-runs/run-1/outputs/preview/content', media_type: 'audio/wav' } },
    ] })
    setup((path, init) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(catalog()))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/workflow')) return Promise.resolve(response({ ...documentBody(), nodes, edges }))
      if (path.endsWith('/workflow-runs') && init?.method === 'POST') return Promise.resolve(response(completed, 202))
      return undefined
    })
    await settle()
    const store = useWorkflowStore()
    await store.startRun(); await settle()
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).includes('/audios/bound-audio/workflow-runs') && (init as RequestInit)?.method === 'POST')).toBe(true)
    expect(node('audio_preview').querySelector('audio')?.getAttribute('src')).toContain('/audios/bound-audio/')
    expect(root!.querySelector('.workflow-run-nodes button')).toBeNull()
    store.updateNode('input', { position: { x: 90, y: 130 } }); await settle()
    // Position alone does not change execution snapshot consistency.
    expect(node('audio_preview').textContent).toContain('与画布一致')
    store.updateNode('input', { config: { audio_id: 'other' } }); await settle()
    expect(node('audio_preview').textContent).toContain('历史版本')
  })
})

describe('workflow viewport coordinates and gestures', () => {
  function pointer(target: EventTarget, type: string, x: number, y: number, id = 7) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 })
    Object.defineProperty(event, 'pointerId', { value: id })
    target.dispatchEvent(event)
  }
  async function viewport() {
    setup(); await settle()
    const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ x: 100, y: 60, left: 100, top: 60, right: 1500, bottom: 960, width: 1400, height: 900, toJSON: () => ({}) })
    return svg
  }

  it('pans in newly exposed viewport space and keeps the world grid aligned at very low zoom', async () => {
    const svg = await viewport()
    const store = useWorkflowStore()
    store.pan = { x: -1000, y: -600 }; store.zoom = .5
    const capture = vi.fn(); const release = vi.fn()
    Object.defineProperties(svg, { setPointerCapture: { value: capture }, hasPointerCapture: { value: () => true }, releasePointerCapture: { value: release } })
    pointer(svg, 'pointerdown', 1400, 900)
    pointer(window, 'pointermove', 1470, 930)
    await settle()
    expect(store.pan).toEqual({ x: -930, y: -570 })
    expect(capture).toHaveBeenCalledWith(7)
    pointer(window, 'pointerup', 1700, 1050)
    pointer(window, 'pointermove', 1800, 1200)
    expect(store.pan).toEqual({ x: -930, y: -570 })
    expect(release).toHaveBeenCalledWith(7)
    store.zoom = .01; await settle()
    const canvas = root!.querySelector<HTMLElement>('.workflow-canvas')!
    const spacing = Number.parseFloat(canvas.style.backgroundSize)
    expect(spacing).toBeGreaterThanOrEqual(8)
    expect(spacing).toBeLessThan(16)
    expect(spacing / (24 * store.zoom)).toBe(64)
    expect(canvas.style.backgroundPosition).toBe('-930px -570px')
    expect(root!.querySelector('.workflow-content')?.getAttribute('transform')).toBe('translate(-930 -570) scale(0.01)')
    expect(store.dirty).toBe(false)
  })

  it('converts node drag and connection preview coordinates once against the stable viewport', async () => {
    const svg = await viewport()
    const store = useWorkflowStore()
    store.pan = { x: 150, y: -30 }; store.zoom = .5
    // Pointer starts at world (100, 140), then moves 40x30 CSS pixels.
    pointer(node('audio_input'), 'pointerdown', 300, 100)
    pointer(svg, 'pointermove', 340, 130)
    pointer(window, 'pointerup', 340, 130)
    await settle()
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.position).toEqual({ x: 160, y: 180 })
    const source = node('audio_input').querySelector('.node-port-out')!
    pointer(source, 'pointerdown', 424, 142.5)
    // Stable viewport (100,60), pan (150,-30), zoom .5 -> world (500,300).
    pointer(svg, 'pointermove', 500, 180)
    await settle()
    const path = root!.querySelector('.workflow-edge-preview')!.getAttribute('d')!
    expect(path).toMatch(/^M 348 225 /)
    expect(path).toMatch(/, 500 300$/)
    const edges = JSON.stringify(store.draft.edges)
    pointer(window, 'pointerup', 1600, 1000)
    await settle()
    expect(root!.querySelector('.workflow-edge-preview')).toBeNull()
    expect(JSON.stringify(store.draft.edges)).toBe(edges)
  })

  it('ignores other pointers, clears on blur, and keeps running canvases locked', async () => {
    const svg = await viewport()
    const store = useWorkflowStore()
    pointer(svg, 'pointerdown', 1200, 700)
    pointer(window, 'pointermove', 1300, 800, 8)
    expect(store.pan).toEqual({ x: 0, y: 0 })
    window.dispatchEvent(new Event('blur'))
    pointer(window, 'pointermove', 1300, 800)
    expect(store.pan).toEqual({ x: 0, y: 0 })
    store.run = runBody({ status: 'running' }); await settle()
    pointer(svg, 'pointerdown', 1200, 700)
    pointer(svg, 'pointermove', 1400, 900)
    pointer(node('audio_input'), 'pointerdown', 200, 200)
    pointer(svg, 'pointermove', 400, 400)
    expect(store.pan).toEqual({ x: 0, y: 0 })
    expect(store.draft.nodes.find((entry) => entry.id === 'input')!.position).toEqual(input.position)
  })
})

describe('canvas context menus', () => {
  function categorizedCatalog(): any {
    const value: any = structuredClone(testCapabilities)
    value.workflow.schema_version = '1.8'
    const categories = [{ id: 'input', label: '输入' }, { id: 'plugin_tools', label: 'Plugin tools' }, { id: 'speech_recognition', label: '语音识别' }, { id: 'output', label: '输出' }]
    value.workflow.nodes.forEach((node: any, index: number) => {
      node.category = categories[Math.min(index, 3)]
      node.is_parameter_source = false; node.required_input_ports = node.required_inbound ? [...node.input_ports] : []
      node.config.fields.forEach((field: any) => { field.input_port = null })
    })
    value.workflow.nodes.push({ ...value.workflow.nodes[2], type: 'qwen_asr', label: 'Qwen ASR', add_label: 'Qwen ASR', category: categories[2] })
    value.workflow.nodes.push({ ...value.workflow.nodes[1], type: 'extension_uncategorized', label: 'Extension without category', category: null })
    value.workflow.nodes.push({ ...value.workflow.nodes[1], type: 'extension_named_uncategorized', label: 'Explicit extension category', category: { id: 'uncategorized', label: 'Extension tools' } })
    return value
  }
  it('groups both entry points in catalog order, retains unknown categories, and never changes the draft', async () => {
    const capabilities = categorizedCatalog()
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(capabilities)) : undefined); await settle()
    const draft = JSON.stringify(useWorkflowStore().draft)
    root!.querySelector<HTMLButtonElement>('[data-add-node]')!.click(); await settle()
    expect([...root!.querySelectorAll('[data-node-category]')].map((group) => group.getAttribute('data-node-category'))).toEqual(['input', 'plugin_tools', 'speech_recognition', 'output', '', 'uncategorized'])
    expect(root!.querySelector('[data-node-category=plugin_tools] h3')!.textContent).toBe('Plugin tools')
    expect(root!.querySelector('[data-node-category=""] h3')!.textContent).toBe('未分类')
    expect(root!.querySelector('[data-node-category=uncategorized] h3')!.textContent).toBe('Extension tools')
    expect([...root!.querySelectorAll('[data-node-category=speech_recognition] [data-add-kind]')].map((item) => item.getAttribute('data-add-kind'))).toEqual(['asr', 'qwen_asr'])
    expect(root!.querySelector('[data-add-kind=asr]')!.textContent).toContain('语音识别（Mock）')
    expect(root!.querySelector('[data-node-category=output] [data-add-kind=audio_output]')!.textContent).toContain('音频输出')
    setLocale('en'); await settle()
    expect(root!.querySelector('[data-node-category=output] [data-add-kind=audio_output]')!.textContent).toContain('Audio output')
    setLocale('zh-CN'); await settle()
    root!.querySelector('.canvas-context-menu')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    menu(root!.querySelector('.workflow-svg')!); await settle()
    expect(root!.querySelectorAll('[data-node-category]')).toHaveLength(6)
    expect(JSON.stringify(useWorkflowStore().draft)).toBe(draft)
  })

  it('searches category IDs and both friendly languages, hides empty groups, and preserves the query across locale changes', async () => {
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(categorizedCatalog())) : undefined); await settle()
    menu(root!.querySelector('.workflow-svg')!); await settle()
    const search = root!.querySelector<HTMLInputElement>('.canvas-node-search input')!
    for (const query of ['speech recognition', '语音识别', 'speech_recognition']) {
      search.value = query; search.dispatchEvent(new Event('input', { bubbles: true })); await settle()
      expect(root!.querySelectorAll('[data-node-category]')).toHaveLength(1)
      expect(root!.querySelectorAll('[data-add-kind]')).toHaveLength(2)
    }
    setLocale('en'); await settle()
    expect(search.value).toBe('speech_recognition')
    expect(root!.querySelector('[data-node-category=speech_recognition] h3')!.textContent).toBe('Speech recognition')
    search.value = 'Plugin tools'; search.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(root!.querySelectorAll('[data-add-kind]')).toHaveLength(1)
    search.value = 'no matching category'; search.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(root!.querySelectorAll('[data-node-category]')).toHaveLength(0)
    expect(root!.querySelector('.canvas-context-menu [role=status]')!.textContent).toBe('No matching nodes')
  })

  it('skips headings and disabled nodes with arrow, Home and End keys in grouped menus', async () => {
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(categorizedCatalog())) : undefined); await settle()
    menu(root!.querySelector('.workflow-svg')!); await settle()
    const menuElement = root!.querySelector('.canvas-context-menu')!
    const items = [...menuElement.querySelectorAll<HTMLButtonElement>('button:not([disabled])')]
    for (const [key, expected] of [['ArrowUp', items[items.length - 1]], ['Home', items[0]], ['End', items[items.length - 1]], ['ArrowDown', items[0]], ['ArrowDown', items[1]], ['ArrowUp', items[0]]] as const) {
      menuElement.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })); await settle()
      expect(document.activeElement).toBe(expected)
      expect(document.activeElement!.getAttribute('role')).toBe('menuitem')
    }
    menuElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
  })

  it('shows legacy nodes together as unclassified instead of inferring categories', async () => {
    setup(); await settle(); menu(root!.querySelector('.workflow-svg')!); await settle()
    expect(root!.querySelectorAll('[data-node-category]')).toHaveLength(1)
    expect(root!.querySelector('[data-node-category=""] h3')!.textContent).toBe('未分类')
    expect(root!.querySelectorAll('[data-add-kind]')).toHaveLength(testCapabilities.workflow.nodes.length)
  })

  function menu(target: Element, x = 300, y = 200) {
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y })
    target.dispatchEvent(event)
    return event
  }
  it('places searched nodes at the clicked world coordinate including negative coordinates and avoids existing nodes', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    store.pan = { x: 600, y: 400 }; store.zoom = .5
    const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 60, right: 1100, bottom: 560, width: 1000, height: 500, x: 100, y: 60, toJSON: () => ({}) })
    menu(svg, 300, 200); await settle()
    const search = root!.querySelector<HTMLInputElement>('.canvas-node-search input')!
    search.value = 'preprocess'; search.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(root!.querySelectorAll('[data-add-kind]')).toHaveLength(1)
    search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); await settle()
    expect(store.draft.nodes.find((node) => node.type === 'preprocess')?.position).toEqual({ x: -800, y: -520 })
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    menu(svg, 300, 200); await settle()
    root!.querySelector<HTMLButtonElement>('[data-add-kind=preprocess]')!.click(); await settle()
    const nodes = store.draft.nodes.filter((node) => node.type === 'preprocess')
    expect(nodes[1]!.position.y).toBeGreaterThanOrEqual(nodes[0]!.position.y + store.getNodeSize(nodes[0]!).height)
  })

  it('opens node deletion without moving it, obeys removable metadata and retains confirmation', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    const position = { ...store.draft.nodes[1]!.position }
    node('audio_output').dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 2, clientX: 500, clientY: 200 }))
    menu(node('audio_output')); await settle()
    root!.querySelector('.workflow-svg')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 700, clientY: 500 }))
    expect(store.draft.nodes[1]!.position).toEqual(position)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    root!.querySelector<HTMLButtonElement>('[data-delete-node]')!.click(); await settle()
    expect(store.draft.nodes).toHaveLength(2)
    confirm.mockReturnValue(true)
    menu(node('audio_output')); await settle()
    root!.querySelector<HTMLButtonElement>('[data-delete-node]')!.click(); await settle()
    expect(store.draft.nodes.map((node) => node.id)).toEqual(['input'])
    menu(node('audio_input')); await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-delete-node]')!.disabled).toBe(true)
    expect(document.activeElement).toBe(root!.querySelector('[data-context-command=copy]'))
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
  })

  it('derives source and instance menu availability from the capability catalog', async () => {
    const capabilities = structuredClone(testCapabilities)
    capabilities.workflow.nodes.push({ ...capabilities.workflow.nodes[0], type: 'another_input', label: 'Another input', add_label: 'Another input', max_instances: null })
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(capabilities)) : undefined)
    await settle()
    menu(root!.querySelector('.workflow-svg')!); await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-add-kind=audio_input]')!.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('[data-add-kind=another_input]')!.disabled).toBe(true)
    const before = useWorkflowStore().draft.nodes.length
    root!.querySelector<HTMLButtonElement>('[data-add-kind=another_input]')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(useWorkflowStore().draft.nodes).toHaveLength(before)
  })

  it('preserves native editable/media menus and supports keyboard entry, escape, outside clicks and view/lock dismissal', async () => {
    setup(); await settle()
    const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
    const input = node('audio_output').querySelector('input')!
    expect(menu(input).defaultPrevented).toBe(false)
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    for (const tag of ['textarea', 'select', 'audio', 'video']) {
      const native = document.createElement(tag); root!.querySelector('.workflow-canvas')!.append(native)
      expect(menu(native).defaultPrevented).toBe(false); native.remove()
    }
    svg.dispatchEvent(new KeyboardEvent('keydown', { key: 'F10', shiftKey: true, bubbles: true, cancelable: true })); await settle()
    expect(document.activeElement).toBe(root!.querySelector('.canvas-node-search input'))
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })); await settle()
    expect((document.activeElement as HTMLElement).dataset.addKind).toBeTruthy()
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    menu(svg); await settle()
    window.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })); await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    menu(svg); await settle()
    useWorkflowStore().zoom = .5; await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    menu(svg); await settle()
    useWorkflowStore().saving = true; await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
    menu(svg); await settle()
    expect(root!.querySelector('.canvas-context-menu')).toBeNull()
  })

  it('rejects nonfinite or exhausted placement coordinates without inserting illegal graph positions', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    store.addNode('preprocess', { x: Number.NaN, y: 0 })
    expect(store.draft.nodes).toHaveLength(2)
    store.addNode('preprocess', { x: 2_000_000, y: -2_000_000 })
    expect(store.draft.nodes.find((node) => node.type === 'preprocess')?.position).toEqual({ x: 1_000_000, y: -1_000_000 })
    store.updateNode('output', { position: { x: 0, y: 999_950 }, size: { width: 500, height: 500 } })
    const count = store.draft.nodes.length
    store.addNode('preprocess', { x: 0, y: 999_950 })
    expect(store.draft.nodes).toHaveLength(count)
    expect(store.error).toContain('没有可用的节点空间')
    expect(store.draft.nodes.every((node) => Number.isFinite(node.position.y) && Math.abs(node.position.y) <= 1_000_000)).toBe(true)
  })

  it('keeps the initial valid coordinate if first-paint content expansion exhausts available placement space', async () => {
    Object.defineProperty(SVGElement.prototype, 'getComputedTextLength', { configurable: true, value: function(this: SVGTextElement) { return this.classList.contains('node-name') ? 500 : 20 } })
    try {
      setup(); await settle()
      const store = useWorkflowStore()
      store.updateNode('output', { position: { x: 300, y: 999_950 } })
      store.addNode('preprocess', { x: 0, y: 999_950 })
      await settle(); await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); await settle()
      const inserted = store.draft.nodes.find((node) => node.type === 'preprocess')!
      expect(inserted.position.y).toBe(999_950)
      expect(store.error).toContain('没有可用的节点空间')
    } finally { delete (SVGElement.prototype as any).getComputedTextLength }
  })
})

describe('bidirectional socket drags', () => {
  function socket(type: string, direction: 'input' | 'output', index = 0) { return node(type, index).querySelector<SVGCircleElement>(`[data-port-direction="${direction}"]`)! }
  function pointer(target: EventTarget, type: string, x = 0, y = 0, id = 7, pointerType = 'mouse') {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y })
    Object.defineProperties(event, { pointerId: { value: id }, pointerType: { value: pointerType } })
    target.dispatchEvent(event)
  }
  async function nodes() {
    setup(); await settle()
    const store = useWorkflowStore()
    store.addNode('preprocess'); store.addNode('preprocess'); store.addNode('asr'); store.addNode('text_output')
    await settle()
    return store
  }

  it('normalizes forward and reverse drags through the same connect rules and preserves click semantics', async () => {
    const store = await nodes()
    const connect = vi.spyOn(store, 'connect')
    const [first, second] = store.draft.nodes.filter((entry) => entry.type === 'preprocess')
    const positions = store.draft.nodes.map((entry) => ({ ...entry.position }))
    pointer(socket('preprocess', 'input', 1), 'pointerdown')
    pointer(socket('preprocess', 'output', 0), 'pointerup')
    expect(connect).toHaveBeenLastCalledWith(first.id, second.id, 'audio', 'audio')
    pointer(socket('audio_input', 'output'), 'pointerdown')
    pointer(socket('preprocess', 'input', 0), 'pointerup')
    expect(connect).toHaveBeenLastCalledWith('input', first.id, 'audio', 'audio')
    const calls = connect.mock.calls.length
    pointer(socket('preprocess', 'input', 0), 'pointerdown')
    pointer(socket('preprocess', 'input', 0), 'pointerup')
    expect(connect).toHaveBeenCalledTimes(calls)
    expect(store.draft.nodes.map((entry) => entry.position)).toEqual(positions)
  })

  it('reverses the preview source/target in world coordinates with nonzero pan/zoom and custom widths', async () => {
    const store = await nodes()
    store.updateNode('input', { size: { width: 420, height: 150 } })
    store.updateNode('output', { size: { width: 500, height: 180 } })
    store.pan = { x: 150, y: -30 }; store.zoom = .5
    const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 60, right: 1100, bottom: 560, width: 1000, height: 500, x: 100, y: 60, toJSON: () => ({}) })
    pointer(socket('audio_output', 'input'), 'pointerdown', 460, 112.5)
    pointer(svg, 'pointermove', 500, 112.5); await settle()
    expect(root!.querySelector('.workflow-edge-preview')?.getAttribute('d')).toBe('M 500 165 C 548 165, 372 165, 420 165')
    pointer(window, 'pointerup', 1300, 900); await settle()
    expect(root!.querySelector('.workflow-edge-preview')).toBeNull()
  })

  it('keeps existing inputs on cancel, rejects same-direction/mismatched/self/cyclic drags and replaces only after success', async () => {
    const store = await nodes()
    const original = JSON.stringify(store.draft.edges)
    pointer(socket('audio_output', 'input'), 'pointerdown')
    pointer(root!.querySelector('.workflow-svg')!, 'pointerup')
    expect(JSON.stringify(store.draft.edges)).toBe(original)
    pointer(socket('audio_output', 'input'), 'pointerdown')
    pointer(socket('preprocess', 'input'), 'pointerup')
    expect(JSON.stringify(store.draft.edges)).toBe(original)
    pointer(socket('text_output', 'input'), 'pointerdown')
    pointer(socket('audio_input', 'output'), 'pointerup')
    expect(store.error).toContain('端口类型不匹配')
    expect(JSON.stringify(store.draft.edges)).toBe(original)
    pointer(socket('text_output', 'input'), 'pointerdown')
    pointer(socket('asr', 'output'), 'pointerup')
    expect(store.draft.edges.some((entry) => entry.source === store.draft.nodes.find((entry) => entry.type === 'asr')!.id && entry.target === store.draft.nodes.find((entry) => entry.type === 'text_output')!.id)).toBe(true)
    pointer(socket('preprocess', 'input'), 'pointerdown'); pointer(socket('preprocess', 'output'), 'pointerup')
    expect(store.error).toContain('自身')
    const [first, second] = store.draft.nodes.filter((entry) => entry.type === 'preprocess')
    store.connect(first.id, second.id)
    const edges = JSON.stringify(store.draft.edges)
    pointer(socket('preprocess', 'input', 0), 'pointerdown'); pointer(socket('preprocess', 'output', 1), 'pointerup')
    expect(store.error).toContain('循环')
    expect(JSON.stringify(store.draft.edges)).toBe(edges)
    pointer(socket('audio_output', 'input'), 'pointerdown'); pointer(socket('preprocess', 'output', 1), 'pointerup')
    expect(store.draft.edges.filter((entry) => entry.target === 'output')).toEqual([expect.objectContaining({ source: second.id, target: 'output' })])
  })

  it('filters other pointers and cancels on locks, pointercancel and blur', async () => {
    const store = await nodes()
    const edges = JSON.stringify(store.draft.edges)
    pointer(socket('audio_output', 'input'), 'pointerdown')
    pointer(socket('preprocess', 'output'), 'pointerdown', 10, 10, 8)
    pointer(socket('preprocess', 'output'), 'pointerup', 10, 10, 8)
    await settle()
    expect(root!.querySelector('.workflow-edge-preview')).not.toBeNull()
    pointer(window, 'pointercancel')
    await settle()
    expect(root!.querySelector('.workflow-edge-preview')).toBeNull()
    pointer(socket('audio_output', 'input'), 'pointerdown'); window.dispatchEvent(new Event('blur'))
    pointer(socket('preprocess', 'output'), 'pointerup')
    expect(JSON.stringify(store.draft.edges)).toBe(edges)
    for (const lock of ['saving', 'validating', 'resourceUploading'] as const) {
      store[lock] = true
      pointer(socket('audio_output', 'input'), 'pointerdown')
      pointer(socket('preprocess', 'output'), 'pointerup')
      expect(JSON.stringify(store.draft.edges)).toBe(edges)
      store[lock] = false
    }
    pointer(socket('audio_output', 'input'), 'pointerdown')
    store.saving = true
    pointer(socket('preprocess', 'output'), 'pointerup')
    expect(JSON.stringify(store.draft.edges)).toBe(edges)
  })

  it('resolves touch/pen implicit capture by final coordinates and reads direction from data rather than port ID classes', async () => {
    const store = await nodes()
    const descriptor = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
    const destination = socket('preprocess', 'output')
    const hit = vi.fn().mockReturnValue(destination)
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: hit })
    try {
      const start = socket('audio_output', 'input')
      start.classList.add('node-port-out') // A valid extension port ID may itself be "out".
      const connect = vi.spyOn(store, 'connect')
      for (const type of ['touch', 'pen']) {
        pointer(start, 'pointerdown', 100, 100, 7, type)
        pointer(start, 'pointerup', 600, 200, 7, type)
        expect(hit).toHaveBeenLastCalledWith(600, 200)
        expect(connect).toHaveBeenLastCalledWith(store.draft.nodes.find((entry) => entry.type === 'preprocess')!.id, 'output', 'audio', 'audio')
      }
      // Forward completion must also treat an input with the CSS class
      // node-port-out as an input; only its explicit direction is authoritative.
      const calls = connect.mock.calls.length
      hit.mockReturnValue(start)
      pointer(destination, 'pointerdown', 600, 200, 7, 'touch')
      pointer(destination, 'pointerup', 100, 100, 7, 'touch')
      expect(connect).toHaveBeenCalledTimes(calls + 1)
      expect(connect).toHaveBeenLastCalledWith(store.draft.nodes.find((entry) => entry.type === 'preprocess')!.id, 'output', 'audio', 'audio')
      hit.mockReturnValue(null)
      const edges = JSON.stringify(store.draft.edges)
      pointer(start, 'pointerdown', 100, 100, 7, 'touch')
      pointer(start, 'pointerup', 1600, 900, 7, 'touch')
      expect(JSON.stringify(store.draft.edges)).toBe(edges)
    } finally {
      if (descriptor) Object.defineProperty(document, 'elementFromPoint', descriptor)
      else delete (document as any).elementFromPoint
    }
  })
})

describe('node resize gestures and measured minima', () => {
  function pointer(target: EventTarget, type: string, x: number, y: number) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 })
    Object.defineProperty(event, 'pointerId', { value: 9 })
    target.dispatchEvent(event)
  }
  function handle(type = 'audio_input', axis = 'corner') { return node(type).querySelector<SVGRectElement>(`[data-resize="${axis}"]`)! }
  async function frames() { await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); await settle() }

  it('resizes once in world coordinates with nonzero pan/zoom and keeps movement, sockets and preview endpoints aligned', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    const svg = root!.querySelector<SVGSVGElement>('.workflow-svg')!
    vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 60, width: 1000, height: 500, right: 1100, bottom: 560, x: 100, y: 60, toJSON: () => ({}) })
    store.pan = { x: 150, y: -30 }; store.zoom = .5
    const original = { ...store.draft.nodes[0]!.position }
    pointer(handle(), 'pointerdown', 400, 200)
    pointer(window, 'pointermove', 450, 230)
    pointer(window, 'pointerup', 1200, 700)
    await settle()
    expect(store.draft.nodes[0]!.size).toEqual({ width: WORKFLOW_NODE_WIDTH + 100, height: WORKFLOW_NODE_HEIGHT + 60 })
    expect(store.draft.nodes[0]!.position).toEqual(original)
    expect(node('audio_input').querySelector('.node-port-out')?.getAttribute('cx')).toBe('288')
    expect(root!.querySelector('.workflow-edge')?.getAttribute('d')).toMatch(/^M 368 165 /)
    const size = { ...store.draft.nodes[0]!.size! }
    pointer(window, 'pointermove', 1300, 800); await settle()
    expect(store.draft.nodes[0]!.size).toEqual(size)
    pointer(node('audio_input').querySelector('.node-port-out')!, 'pointerdown', 400, 200)
    pointer(svg, 'pointermove', 500, 180); await settle()
    expect(root!.querySelector('.workflow-edge-preview')?.getAttribute('d')).toMatch(/^M 368 165 /)
    pointer(window, 'pointerup', 1300, 800)
  })

  it('provides keyboard resize/reset, clamps measured content, and leaves execution results current', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    store.run = runBody({ status: 'completed' })
    store.setNodeMinimum('input', { width: 260, height: 180 })
    handle().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    await settle()
    expect(store.draft.nodes[0]!.size).toEqual({ width: 270, height: 180 })
    handle('audio_input', 'bottom').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', shiftKey: true, bubbles: true }))
    await settle()
    expect(store.draft.nodes[0]!.size).toEqual({ width: 270, height: 230 })
    expect(store.runMatchesDraft).toBe(true)
    handle().dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    await settle()
    expect(store.draft.nodes[0]!.size).toBeNull()
    expect(node('audio_input').querySelector('.node-card')?.getAttribute('width')).toBe('260')
    expect(store.runMatchesDraft).toBe(true)
  })

  it('fits persisted custom dimensions and clamps undersized requests without moving existing nodes', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    const original = { ...store.draft.nodes[0]!.position }
    store.updateNode('input', { size: { width: 600, height: 500 } })
    const canvas = root!.querySelector<HTMLElement>('.workflow-canvas')!
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 343, bottom: 390, width: 343, height: 390, toJSON: () => ({}) })
    button('适配')!.click(); await settle()
    const size = store.getNodeSize(store.draft.nodes[0]!)
    expect((original.x + size.width) * store.zoom + store.pan.x).toBeLessThanOrEqual(343)
    expect((original.y + size.height) * store.zoom + store.pan.y).toBeLessThanOrEqual(390)
    store.setNodeMinimum('input', { width: 280, height: 160 })
    pointer(handle(), 'pointerdown', 300, 200)
    pointer(window, 'pointermove', -1000, -1000)
    pointer(window, 'pointerup', -1000, -1000)
    expect(store.draft.nodes[0]!.size).toEqual({ width: 280, height: 160 })
    expect(store.draft.nodes[0]!.position).toEqual(original)
  })

  it('ignores locked handles and clears active resizing on blur or a newly active lock', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    pointer(handle(), 'pointerdown', 200, 200)
    window.dispatchEvent(new Event('blur'))
    pointer(window, 'pointermove', 400, 400)
    expect(store.draft.nodes[0]!.size).toBeUndefined()
    pointer(handle(), 'pointerdown', 200, 200)
    store.saving = true; await settle()
    pointer(window, 'pointermove', 400, 400)
    handle().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    expect(store.draft.nodes[0]!.size).toBeUndefined()
    expect(handle().getAttribute('aria-disabled')).toBe('true')
  })

  it('updates title minima for locale changes without measuring user dimensions into the minimum or rewriting old sizes', async () => {
    const metric = vi.fn(function(this: SVGTextElement) { return this.classList.contains('node-name') ? (this.textContent?.includes('Audio') ? 390 : 180) : 30 })
    Object.defineProperty(SVGElement.prototype, 'getComputedTextLength', { configurable: true, value: metric })
    try {
      setup(); await settle(); await frames()
      const store = useWorkflowStore()
      store.updateNode('input', { size: { width: 190, height: 100 } })
      setLocale('en'); await settle(); await frames()
      expect(store.getNodeSize(store.draft.nodes[0]!).width).toBe(454)
      expect(store.draft.nodes[0]!.size).toEqual({ width: 190, height: 100 })
      store.updateNode('input', { size: { width: 900, height: 600 } }); await settle(); await frames()
      expect(store.nodeMinimums.input!.width).toBe(454)
      setLocale('zh-CN'); await settle(); await frames()
      expect(store.nodeMinimums.input!.width).toBe(244)
      handle().dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true })); await settle()
      expect(store.getNodeSize(store.draft.nodes[0]!).width).toBe(244)
    } finally { delete (SVGElement.prototype as any).getComputedTextLength }
  })
})

describe('workflow editor interactions', () => {
  it('renders compact typed nodes with colored headers and side sockets', async () => {
    setup()
    await settle()

    expect(root!.querySelectorAll('.node-header')).toHaveLength(2)
    expect(root!.querySelectorAll('.node-name')).toHaveLength(2)
    expect(root!.querySelector('.node-port-label-in')).not.toBeNull()
    expect(root!.querySelector('.node-accent')).toBeNull()
  })

  it('previews every catalog field and keeps fixed socket endpoints as nodes grow', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    store.capabilities!.ports.find((entry) => entry.id === 'audio')!.label = 'Friendly audio extension port with a long name'
    const kind = store.capabilities!.nodes.find((entry) => entry.type === 'audio_output')!
    kind.fields = [...kind.fields,
      { ...kind.fields[0]!, key: 'enabled', label: 'Enabled extension', type: 'boolean', default: true, options: [] },
      { ...kind.fields[0]!, key: 'mode', label: 'Mode extension', type: 'select', default: 'custom', options: [{ value: 'custom', label: 'Friendly mode' }] },
      { ...kind.fields[0]!, key: 'details', label: 'Details extension', type: 'json', default: { nested: true }, options: [] },
    ]
    await settle()
    const outputNode = node('audio_output')!
    expect(outputNode.querySelectorAll('.node-config-row')).toHaveLength(4)
    const values = [...outputNode.querySelectorAll<HTMLInputElement>('input, select, textarea')].map((entry) => entry.value)
    expect(values).toEqual(['audio-copy', 'true', 'custom', '{\n  "nested": true\n}'])
    expect(outputNode.textContent).toContain('Friendly mode')
    const height = workflowNodeHeight('audio_output', store.registry!)
    expect(height).toBeGreaterThan(WORKFLOW_NODE_HEIGHT)
    expect(outputNode.querySelector('.node-card')?.getAttribute('height')).toBe(String(height))
    expect(outputNode.querySelector('.node-port-in')?.getAttribute('cy')).toBe(String(WORKFLOW_NODE_PORT_Y))
    expect(outputNode.querySelector('.node-port-label-in title')?.textContent).toBe('Friendly audio extension port with a long name')
    expect(outputNode.querySelector('.node-port-label-in')?.getAttribute('clip-path')).toContain('port-in')
    expect(root!.querySelector('.workflow-edge')?.getAttribute('d')).toContain(`420 ${120 + WORKFLOW_NODE_PORT_Y}`)
    const position = nextNodePosition('audio_output', store.draft.nodes, store.registry!)
    expect(position.y).toBeGreaterThanOrEqual(120 + height)
    expect(store.draft.nodes.find((entry) => entry.type === 'audio_output')?.position).toEqual({ x: 420, y: 120 })
  })

  it('keeps the viewport clear of permanent property and result panels', async () => {
    setup(); await settle()
    expect(root!.querySelector('.workflow-inspector')).toBeNull()
    expect(root!.querySelector('.workflow-run-panel')).toBeNull()
    expect(root!.querySelector('.node-workflow-heading')).toBeNull()
    expect(root!.querySelector('[data-add-node]')).not.toBeNull()
  })

  it('adds an output, edits its filename, validates and saves the expected payload', async () => {
    setup()
    await settle()
    expect(root!.querySelector('svg.workflow-svg')).not.toBeNull()
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(2)
    await addNode('audio_output')
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
    node('audio_output', 0)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    await settle()
    const filename = node('audio_output').querySelector<HTMLInputElement>('input[data-field="filename_stem"]')!
    filename.value = 'chapter-1'
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow/validate'))).toBe(true)
    const [path, init] = putCalls()[0]
    expect(String(path)).toContain('/projects/p1/workflow')
    const body = JSON.parse(String((init as RequestInit).body)) as { expected_revision: number; nodes: WorkflowDocumentInput['nodes'] }
    expect(body.expected_revision).toBe(1)
    expect(body.nodes.filter((item) => item.type === 'audio_output')).toHaveLength(2)
    expect(body.nodes.find((item) => item.id === 'output')?.config.filename_stem).toBe('chapter-1')
  })

  it('allows filename editing and defers complex filename validation to the backend', async () => {
    setup((path) => path.endsWith('/workflow/validate')
      ? Promise.resolve(response({ valid: false, errors: [{ code: 'INVALID_FILENAME_STEM', message: '文件名含路径分隔符', node_id: 'output' }] }))
      : undefined)
    await settle()
    await addNode('audio_output')
    node('audio_output', 0)!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    const filename = node('audio_output').querySelector<HTMLInputElement>('input[data-field="filename_stem"]')!
    filename.value = 'bad' + String.fromCharCode(92) + 'name'
    filename.dispatchEvent(new Event('input', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.node-field-error')).toBeNull()
    expect(useWorkflowStore().draft.nodes.find((item) => item.id === 'output')!.config.filename_stem).toBe('bad' + String.fromCharCode(92) + 'name')
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/workflow/validate'))).toBe(true)
    expect(putCalls()).toHaveLength(0)
    expect(root!.querySelector('.workflow-issues')?.textContent).toContain('输出文件名必须是安全的文件名主体')
  })

  it('adds a preprocess node from catalog defaults and renders only its Mock provider field', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const beforeEdges = JSON.parse(JSON.stringify(store.draft.edges))
    await addNode('preprocess')
    expect(store.draft.nodes.filter((item) => item.type === 'preprocess')).toHaveLength(1)
    expect(store.draft.edges).toEqual(beforeEdges)
    expect(store.draft.nodes.find((item) => item.type === 'preprocess')?.config).toEqual({ provider: 'mock' })
    node('preprocess')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    expect(root!.querySelector('.workflow-settings-modal')).toBeNull()
    expect(root!.querySelector('#workflow-sample-rate')).toBeNull()
    expect(root!.querySelector('#workflow-channels')).toBeNull()
    expect(node('preprocess').querySelectorAll('.node-config-row')).toHaveLength(1)
    await settle()
    expect(store.draft.nodes.find((item) => item.type === 'preprocess')?.config).toEqual({ provider: 'mock' })
    expect(store.draft.edges).toEqual(beforeEdges)
  })

  it('creates chained preprocessing through actual source-right and target-left port clicks', async () => {
    setup()
    await settle()
    await addNode('preprocess')
    await addNode('preprocess')
    const store = useWorkflowStore()
    const first = store.draft.nodes.filter((item) => item.type === 'preprocess')[0]
    const second = store.draft.nodes.filter((item) => item.type === 'preprocess')[1]
    const dragConnection = (source: SVGCircleElement, target: SVGCircleElement) => {
      source.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
      target.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    }
    const inputRight = root!.querySelector<SVGCircleElement>('.workflow-node.is-audio_input .node-port-out')!
    dragConnection(inputRight, port(0, 'in')); await settle()
    expect(store.draft.edges.some((item) => item.source === 'input' && item.target === first.id)).toBe(true)
    dragConnection(port(0, 'out'), port(1, 'in')); await settle()
    expect(store.draft.edges.some((item) => item.source === first.id && item.target === second.id)).toBe(true)
    dragConnection(port(1, 'out'), outputPort('in')); await settle()
    expect(store.draft.edges.filter((item) => item.target === 'output')).toEqual([expect.objectContaining({ source: second.id })])
    dragConnection(port(1, 'out'), port(0, 'in')); await settle()
    expect(store.error).toContain('循环')
    expect(store.draft.edges.some((item) => item.source === second.id && item.target === first.id)).toBe(false)
    expect(store.draft.edges.filter((item) => item.target === first.id)).toHaveLength(1)
  })

  it('shows a preview while dragging from a source port and cancels on blank release', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const sourcePort = root!.querySelector<SVGCircleElement>('.workflow-node.is-audio_input .node-port-out')!
    sourcePort.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true }))
    root!.querySelector<SVGElement>('.workflow-svg')!.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 320, clientY: 160 }))
    await settle()

    expect(store.error).toBe('')
    expect(root!.querySelector('.workflow-edge-preview')).not.toBeNull()
    expect(store.draft.edges).toEqual([edge])
    root!.querySelector<SVGElement>('.workflow-svg')!.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.workflow-edge-preview')).toBeNull()
  })

  it('removes a preprocess and its incident edges without touching unrelated branches', async () => {
    setup()
    await settle()
    await addNode('preprocess')
    await addNode('audio_output')
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
    await addNode('preprocess')
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
    await addNode('preprocess')
    await addNode('preprocess')
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
    await addNode('audio_output')
    await addNode('audio_output')
    const transforms = Array.from(root!.querySelectorAll<SVGGElement>('.workflow-node.is-audio_output'))
      .map((item) => item.getAttribute('transform'))
    const step = workflowNodeHeight('audio_output', useWorkflowStore().registry!) + WORKFLOW_NODE_ROW_GAP
    expect(transforms).toEqual([`translate(420 120)`, `translate(420 ${120 + step})`, `translate(420 ${120 + step * 2})`])
  })

  it('fits all nodes inside a narrow canvas using both dimensions', async () => {
    setup()
    await settle()
    const store = useWorkflowStore()
    const kind = store.capabilities!.nodes.find((entry) => entry.type === 'audio_output')!
    kind.fields = [...kind.fields, ...Array.from({ length: 8 }, (_, index) => ({ ...kind.fields[0]!, key: `extension_${index}`, type: index < 3 ? 'json' as const : 'string' as const, default: index < 3 ? {} : 'text' }))]
    await addNode('audio_output')
    const canvas = root!.querySelector<HTMLElement>('.workflow-canvas')!
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
      x: 0, y: 0, top: 0, left: 0, right: 343, bottom: 390, width: 343, height: 390,
      toJSON: () => ({}),
    })
    button('适配')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const minX = Math.min(...store.draft.nodes.map((item) => item.position.x))
    const minY = Math.min(...store.draft.nodes.map((item) => item.position.y))
    const maxX = Math.max(...store.draft.nodes.map((item) => item.position.x + WORKFLOW_NODE_WIDTH))
    const maxY = Math.max(...store.draft.nodes.map((item) => item.position.y + workflowNodeHeight(item.type, store.registry!)))
    const left = minX * store.zoom + store.pan.x
    const right = maxX * store.zoom + store.pan.x
    const top = minY * store.zoom + store.pan.y
    const bottom = maxY * store.zoom + store.pan.y
    expect(store.zoom).toBeGreaterThan(0)
    expect(store.zoom).toBeLessThan(.6)
    expect(left).toBeGreaterThanOrEqual(0)
    expect(right).toBeLessThanOrEqual(343)
    expect(top).toBeGreaterThanOrEqual(0)
    expect(bottom).toBeLessThanOrEqual(390)
    const fitZoom = store.zoom
    root!.querySelector<HTMLButtonElement>('[aria-label="缩小画布"]')!.click(); await settle()
    expect(store.zoom).toBeLessThan(fitZoom)
    root!.querySelector<HTMLButtonElement>('[aria-label="放大画布"]')!.click(); await settle()
    expect(store.zoom).toBeGreaterThan(0.01)
  })

  it('does not save after validation fails and keeps a locatable draft issue', async () => {
    setup((path) => path.endsWith('/workflow/validate')
      ? Promise.resolve(response({ valid: false, errors: [{ code: 'OUTPUT_INPUT_COUNT', message: '输出需要一个音频输入', node_id: 'output' }] }))
      : undefined)
    await settle()
    await addNode('audio_output')
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
    await startRun()
    await openPanel('artifacts')
    const post = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST')
    expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({ workflow_revision: 1 })
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/outputs/input'))).toBe(true)
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
    await startRun()
    await openPanel('artifacts')
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/outputs/preprocess-1'))).toBe(true)
    expect(fetchMock.mock.calls.some(([path]) => String(path).endsWith('/outputs/input'))).toBe(false)
    const download = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('audio-pre.wav'))
    expect(download).toBeTruthy()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    download!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    const anchor = click.mock.instances[click.mock.instances.length - 1] as unknown as HTMLAnchorElement
    expect(anchor.href).toContain('/workflow-runs/run-1/outputs/preprocess-1/download')
  })

  it('adds and edits Mock ASR and text output using catalog fields and options', async () => {
    setup()
    await settle()
    await addNode('asr')
    const store = useWorkflowStore()
    const asrNode = store.draft.nodes.find((item) => item.type === 'asr')!
    expect(asrNode.config).toEqual({ provider: 'mock', language: 'ja' })
    expect(store.draft.edges.some((item) => item.source === 'input' && item.target === asrNode.id)).toBe(false)
    node('asr')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    expect(node('asr')?.textContent).toContain('语言')
    expect(node('asr').querySelectorAll('.node-config-row')).toHaveLength(2)
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/settings/local-asr'))).toBe(false)
    const language = node('asr').querySelector<HTMLInputElement>('input[data-field="language"]')!
    language.value = 'en'; language.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    await settle()
    const applied = store.draft.nodes.find((item) => item.id === asrNode.id)!
    expect(applied.config).toEqual({ provider: 'mock', language: 'en' })
    await addNode('text_output')
    const textNode = store.draft.nodes.find((item) => item.type === 'text_output')!
    node('text_output')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    const format = node('text_output').querySelector<HTMLSelectElement>('select[data-field="format"]')!
    format.value = 'srt'; format.dispatchEvent(new Event('change', { bubbles: true }))
    const stem = node('text_output').querySelector<HTMLInputElement>('input[data-field="filename_stem"]')!
    stem.value = 'subtitles'; stem.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    await settle()
    expect(store.draft.nodes.find((item) => item.id === textNode.id)?.config).toEqual({ filename_stem: 'subtitles', format: 'srt' })
  })

  it('rejects a non-catalog ASR provider locally and preserves the original config', async () => {
    setup()
    await settle()
    await addNode('asr')
    const store = useWorkflowStore()
    const asrId = store.draft.nodes.find((item) => item.type === 'asr')!.id
    store.updateNode(asrId, { config: { provider: 'custom', language: 'ja' } })
    const issues = validateNodeConfig(store.draft.nodes.find((item) => item.id === asrId)!, testRegistry)
    expect(issues.map((issue) => issue.code)).toContain('INVALID_NODE_CONFIG')
    node('asr')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); await settle()
    expect(node('asr').querySelectorAll('.node-config-row')).toHaveLength(2)
    expect(node('asr').querySelector('select')).toBeTruthy()
  })

  it('displays and downloads transcript and text artifacts with distinct ports', async () => {
    const completed = {
      id: 'run-1', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed' as const,
      created_at: '2026-10-28T00:00:00Z', started_at: '2026-10-28T00:00:00Z', finished_at: '2026-10-28T00:00:01Z', cancel_requested: false,
      error: null,
      workflow: { nodes: [input, asr, textOutput, output], edges: [inputAsr, asrText, edge] },
      nodes: [
        { node_id: 'input', node_type: 'audio_input' as const, config: {}, status: 'completed' as const, started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'asr-1', node_type: 'asr' as const, config: structuredClone(asr.config), status: 'completed' as const, started_at: null, finished_at: null, artifact: transcriptArtifact, error: null },
        { node_id: 'text-1', node_type: 'text_output' as const, config: structuredClone(textOutput.config), status: 'completed' as const, started_at: null, finished_at: null, artifact: textArtifact, error: null },
        { node_id: 'output', node_type: 'audio_output' as const, config: { filename_stem: 'audio-copy' }, status: 'completed' as const, started_at: null, finished_at: null, artifact: outputArtifact, error: null },
      ],
    }
    setup((path, init) => {
      if (path.endsWith('/workflow')) return Promise.resolve(response({ ...documentBody(), nodes: structuredClone([input, asr, textOutput, output]), edges: structuredClone([inputAsr, asrText, edge]) }))
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(completed, 202))
      if (path.endsWith('/outputs/asr-1')) return Promise.resolve(response(transcriptArtifact))
      if (path.endsWith('/outputs/text-1')) return Promise.resolve(response(textArtifact))
      if (path.endsWith('/outputs/output')) return Promise.resolve(response(outputArtifact))
      return undefined
    })
    await settle()
    await startRun()
    await openPanel('artifacts')
    expect(root!.textContent).toContain('TRANSCRIPT')
    expect(root!.textContent).toContain('application/json')
    expect(root!.textContent).toContain('TEXT')
    expect(root!.textContent).toContain('application/x-subrip')
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const transcriptDownload = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('transcript.json'))
    const textDownload = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.includes('transcript.srt'))
    expect(transcriptDownload).toBeTruthy()
    expect(textDownload).toBeTruthy()
    transcriptDownload!.click(); await settle()
    textDownload!.click(); await settle()
    const hrefs = click.mock.instances.map((item) => (item as unknown as HTMLAnchorElement).href)
    expect(hrefs.some((href) => href.includes('/outputs/asr-1/download'))).toBe(true)
    expect(hrefs.some((href) => href.includes('/outputs/text-1/download'))).toBe(true)
  })

  it('keeps a processed run read-only and exposes partial and blocked preprocessing states', async () => {
    const partial = runBody({
      status: 'running',
      workflow: { nodes: structuredClone([input, preprocess, output]), edges: [structuredClone(preprocessEdge), structuredClone(preprocessToOutput)] },
      nodes: [
        { node_id: 'input', node_type: 'audio_input', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null },
        { node_id: 'preprocess-1', node_type: 'preprocess', config: structuredClone(preprocess.config), status: 'failed', started_at: null, finished_at: null, artifact: null, error: { type: 'PREPROCESS_FAILED', message: '预处理节点失败' } },
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
    await startRun()
    const store = useWorkflowStore(pinia)
    expect(store.activeRun).toBeTruthy()
    expect(root!.querySelectorAll('.workflow-node.is-preprocess')).toHaveLength(1)
    expect(root!.textContent).toContain('预处理节点失败')
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
    expect(root!.querySelector<HTMLButtonElement>('[data-add-node]')!.disabled).toBe(true)
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
    await startRun()
    await openPanel('artifacts')
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
    await startRun()
    await openPanel('artifacts')
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
    await addNode('preprocess')
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await nextTick()
    const store = useWorkflowStore()
    store.draft.nodes.push({ ...structuredClone(preprocess), id: 'external-preprocess', position: { x: 286, y: 600 } })
    store.markDirty()
    pendingValidation.resolve(response({ valid: true, errors: [] })); await settle()
    expect(putCalls()).toHaveLength(0)
    expect(store.draft.nodes.filter((item) => item.type === 'preprocess')).toHaveLength(2)
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(validationRequests).toBe(2)
    expect(putCalls()).toHaveLength(1)
    expect(JSON.parse(String((putCalls()[0][1] as RequestInit).body)).nodes.filter((item: WorkflowNode) => item.type === 'preprocess')).toHaveLength(2)
  })

  it('keeps the latest draft and returns a readable error when a save conflicts', async () => {
    setup((path, init) => path.endsWith('/workflow') && (init as RequestInit)?.method === 'PUT'
      ? Promise.resolve(response({ detail: { error: { code: 'WORKFLOW_REVISION_CONFLICT', message: '工作流已被修改，请刷新后重试' } } }, 409))
      : undefined)
    await settle()
    await addNode('audio_output')
    button('校验并保存')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(root!.querySelector('.node-workflow-alert')?.textContent).toContain('工作流已被修改')
    expect(root!.querySelectorAll('.workflow-node')).toHaveLength(3)
    expect(root!.querySelector('.workflow-save-state')?.textContent).toContain('未保存')
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
    await startRun()
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
    await startRun()
    expect(store.run?.nodes[0].artifact?.download_url).toBeNull()
    expect(fetchMock.mock.calls.some(([path]) => String(path).includes('/outputs/input'))).toBe(true)
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

  it('keeps expanded history read-only after starting the run', async () => {
    vi.useFakeTimers()
    const pinia = createPinia()
    let pollResponse: ((value: Response) => void) | undefined
    const activeRun = runBody({ status: 'running' })
    const historical = runBody({ id: 'run-old', status: 'completed' })
    setup((path, init) => {
      if (path.endsWith('/workflow-runs?limit=30&offset=0')) return Promise.resolve(response({ items: [summaryBody({ id: 'run-old', status: 'completed' })], total: 1, limit: 30, offset: 0 }))
      if (path.endsWith('/workflow-runs') && (init as RequestInit)?.method === 'POST') return Promise.resolve(response(activeRun, 202))
      if (path.endsWith('/workflow-runs/run-1')) return new Promise((resolve) => { pollResponse = resolve })
      if (path.endsWith('/workflow-runs/run-old')) return Promise.resolve(response(historical))
      return undefined
    }, pinia)
    await settle()
    const store = useWorkflowStore()
    await openPanel('run')
    const oldRow = Array.from(root!.querySelectorAll<HTMLButtonElement>('.workflow-history-row')).find((item) => item.textContent?.includes('run-old'))
    expect(oldRow).toBeTruthy()
    button('运行节点工作流')!.click()
    await settle()
    expect(store.activeRun).toBeTruthy()
    expect(button('取消运行')).toBeTruthy()
    expect(oldRow!.disabled).toBe(true)
    await store.loadRun('run-old')
    expect(store.run?.id).toBe('run-1')
    expect(store.activeRun).toBeTruthy()
    expect(store.runError).toContain('活动运行尚未结束')
    await vi.advanceTimersByTimeAsync(1900)
    await settle()
    expect(pollResponse).toBeTruthy()
    pollResponse!(response(activeRun))
    await settle()
    expect(store.run?.id).toBe('run-1')
    expect(store.activeRun).toBeTruthy()
    expect(button('取消运行')).toBeTruthy()
    store.stop()
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
    await startRun()
    expect(root!.textContent).toContain('历史快照，不映射当前画布状态')
    expect(root!.querySelector('.workflow-node')?.getAttribute('class')).toContain('is-state-draft')
  })
})

describe('editor operations UI', () => {
  function svg() { return root!.querySelector<SVGSVGElement>('.workflow-svg')! }
  function key(target: EventTarget, value: string, modifiers: KeyboardEventInit = {}) {
    const event = new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true, ...modifiers })
    target.dispatchEvent(event); return event
  }
  function pointer(target: EventTarget, type: string, x: number, y: number, shiftKey = false) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, shiftKey })
    Object.defineProperty(event, 'pointerId', { value: 7 }); target.dispatchEvent(event)
  }
  async function command(id: string) {
    root!.querySelector<HTMLButtonElement>('[data-editor-menu]')!.click(); await settle()
    const item = root!.querySelector<HTMLButtonElement>(`[data-editor-command="${id}"]`)!
    expect(item).toBeTruthy(); expect(item.disabled).toBe(false)
    item.click(); await settle()
  }
  async function frame() { await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); await settle() }
  function paste(text: string, target: EventTarget = svg()) {
    const event = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'clipboardData', { value: { getData: () => text } }); target.dispatchEvent(event)
    return event
  }
  async function file(text: string | Promise<string>, name = 'workflow.json', size = 100) {
    const input = root!.querySelector<HTMLInputElement>('[data-workflow-import]')!
    const textReader = vi.fn(() => Promise.resolve(text))
    Object.defineProperty(input, 'files', { configurable: true, value: [{ name, size, text: textReader }] })
    input.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    return textReader
  }

  it('selects with Shift, moves a group by one bounded delta and undoes or cancels the complete gesture', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    node('audio_input').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    node('audio_output').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })); await settle()
    expect(store.selectedNodeIds).toEqual(['input', 'output'])
    expect(root!.querySelectorAll('.workflow-node.is-selected')).toHaveLength(2)
    expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
    store.zoom = .5; store.pan = { x: 100, y: 50 }
    const original = cloneDocument(store.draft)
    pointer(node('audio_input').querySelector('.node-card')!, 'pointerdown', 150, 150)
    pointer(window, 'pointermove', 170, 160)
    pointer(window, 'pointermove', 190, 170)
    pointer(window, 'pointerup', 190, 170); await settle()
    expect(store.draft.nodes.map((entry) => entry.position)).toEqual(original.nodes.map((entry) => ({ x: entry.position.x + 80, y: entry.position.y + 40 })))
    expect(store.historyTransactionActive).toBe(false)
    key(svg(), 'z', { ctrlKey: true }); await settle()
    expect(store.draft).toEqual(original); expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
    key(svg(), 'Z', { ctrlKey: true, shiftKey: true }); await settle()
    const moved = cloneDocument(store.draft)
    pointer(node('audio_input').querySelector('.node-card')!, 'pointerdown', 190, 170)
    pointer(window, 'pointermove', 220, 200)
    key(svg(), 'Escape'); await settle()
    expect(store.draft).toEqual(moved)
    store.updateNodeUi('input', { position: { x: 999_980, y: 0 } })
    store.updateNodeUi('output', { position: { x: 999_990, y: 10 } })
    pointer(node('audio_input').querySelector('.node-card')!, 'pointerdown', 100, 100)
    pointer(window, 'pointermove', 150, 150)
    pointer(window, 'pointerup', 150, 150); await settle()
    expect(store.draft.nodes.map((entry) => entry.position.x)).toEqual([999_990, 1_000_000])
  })

  it('supports Shift marquee in world coordinates while cancel keeps the prior selection and normal dragging pans', async () => {
    setup(); await settle()
    const store = useWorkflowStore(); store.selectNode('output'); store.zoom = .5; store.pan = { x: 100, y: 50 }
    pointer(svg(), 'pointerdown', 110, 90, true)
    pointer(window, 'pointermove', 500, 350, true); await settle()
    expect(root!.querySelector('.workflow-marquee')).not.toBeNull()
    pointer(window, 'pointercancel', 500, 350, true); await settle()
    expect(store.selectedNodeIds).toEqual(['output'])
    pointer(svg(), 'pointerdown', 110, 90, true)
    pointer(window, 'pointermove', 500, 350, true)
    pointer(window, 'pointerup', 500, 350, true); await settle()
    expect(store.selectedNodeIds).toEqual(['input', 'output'])
    expect(root!.querySelector('.workflow-marquee')).toBeNull()
    expect(store.dirty).toBe(false)
    pointer(svg(), 'pointerdown', 110, 90)
    pointer(window, 'pointermove', 130, 120)
    pointer(window, 'pointerup', 130, 120)
    expect(store.pan).toEqual({ x: 120, y: 80 })
    expect(store.selectedNodeIds).toEqual(['input', 'output'])
  })

  it('collapses a plain node click on pointerup even when capture redirects the click to the SVG', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    store.setSelection(['input', 'output'])
    pointer(node('audio_input').querySelector('.node-card')!, 'pointerdown', 100, 100)
    pointer(svg(), 'pointerup', 100, 100)
    svg().dispatchEvent(new MouseEvent('click', { bubbles: true })); await settle()
    expect(store.selectedNodeIds).toEqual(['input'])
    expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
    pointer(node('audio_output').querySelector('.node-card')!, 'pointerdown', 500, 100, true)
    pointer(svg(), 'pointerup', 500, 100, true)
    svg().dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true })); await settle()
    expect(store.selectedNodeIds).toEqual(['input', 'output'])
    pointer(svg(), 'pointerdown', 50, 50)
    pointer(svg(), 'pointerup', 50, 50); await settle()
    expect(store.selectedNodeIds).toEqual([])
    expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
  })

  it('keeps copy/paste single-entry, uses the app buffer on clipboard failure and preserves pasted group layout after measurement', async () => {
    setup(); await settle()
    const store = useWorkflowStore()
    store.addNode('preprocess'); await settle(); await frame()
    const pre = store.draft.nodes.find((entry) => entry.type === 'preprocess')!
    store.connect(pre.id, 'output', 'audio', 'audio')
    store.setSelection([pre.id, 'output'])
    const selected = copySelection(store.draft, store.selectedNodeIds, store.registry!)
    const original = cloneDocument(store.draft)
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    const readText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { writeText, readText } })
    key(svg(), 'c', { ctrlKey: true }); await settle()
    expect(writeText).toHaveBeenCalledTimes(1); expect(store.draft).toEqual(original)
    expect(root!.textContent).toContain(t('workflow.editorCopyFallback'))
    const pasteKey = key(svg(), 'v', { ctrlKey: true }); await settle()
    expect(pasteKey.defaultPrevented).toBe(false); expect(readText).not.toHaveBeenCalled()
    const event = paste(selected); await settle(); await frame()
    expect(event.defaultPrevented).toBe(true)
    expect(store.draft.nodes).toHaveLength(5)
    const pasted = store.draft.nodes.filter((entry) => store.selectedNodeIds.includes(entry.id))
    const before = JSON.parse(selected).nodes as WorkflowNode[]
    expect(pasted[1].position.x - pasted[0].position.x).toBe(before[1].position.x - before[0].position.x)
    expect(pasted[1].position.y - pasted[0].position.y).toBe(before[1].position.y - before[0].position.y)
    expect(store.draft.edges).toContainEqual(expect.objectContaining({ source: pasted.find((entry) => entry.type === 'preprocess')!.id, target: pasted.find((entry) => entry.type === 'audio_output')!.id }))
    key(svg(), 'z', { ctrlKey: true }); await settle()
    expect(store.draft).toEqual(original)
    await command('paste'); await frame()
    expect(readText).toHaveBeenCalledTimes(1); expect(store.draft.nodes).toHaveLength(5)
    expect(root!.textContent).toContain(t('workflow.editorPasteFallback'))
  })

  it('cuts only removable selections once, retains protected sources and falls back to the application clipboard', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    const original = cloneDocument(store.draft)
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    vi.stubGlobal('navigator', { clipboard: { writeText, readText: vi.fn().mockRejectedValue(new Error('denied')) } })
    store.setSelection(['input', 'output'])
    expect(key(svg(), 'x', { metaKey: true }).defaultPrevented).toBe(true); await settle()
    expect(store.draft.nodes.map(node => node.id)).toEqual(['input'])
    expect(JSON.parse(writeText.mock.calls[0]![0]).nodes.map((node: WorkflowNode) => node.id)).toEqual(['output'])
    expect(root!.textContent).toContain(t('workflow.editorCutProtectedFallback', { count: 1 }))
    expect(store.undo()).toBe(true); expect(store.draft).toEqual(original); expect(store.canUndo).toBe(false)
    expect(store.redo()).toBe(true); await command('paste')
    expect(store.draft.nodes).toHaveLength(2)
    store.setSelection(['input']); const before = cloneDocument(store.draft)
    await command('cut'); expect(store.draft).toEqual(before)
    store.selectNode(store.draft.nodes.find(node => node.id !== 'input')!.id)
    store.updateNodeConfig(store.selectedNodeId!, { filename_stem: 'x'.repeat(5 * 1024 * 1024) })
    const unsafe = cloneDocument(store.draft); await command('cut')
    expect(store.draft).toEqual(unsafe)
  })

  it('keeps paste in edit and node menus while both add menus only offer node creation', async () => {
    setup(); await settle(); const store = useWorkflowStore(); store.selectNode('output')
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')), readText: vi.fn().mockRejectedValue(new Error('denied')) } })
    const menu = async (target: Element, x = 300, y = 220) => { target.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: x, clientY: y })); await settle() }
    const close = async () => { root!.querySelector('.canvas-context-menu')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle() }
    await menu(node('audio_output'))
    expect([...root!.querySelectorAll('[data-context-command]')].map(item => item.getAttribute('data-context-command'))).toEqual(['copy', 'cut', 'paste', 'duplicate'])
    root!.querySelector<HTMLButtonElement>('[data-context-command=copy]')!.click(); await settle()
    await menu(svg(), 610, 330)
    expect(root!.querySelector('[data-context-command=paste]')).toBeNull()
    expect(root!.querySelector('[data-add-kind=audio_output]')).not.toBeNull(); await close()
    root!.querySelector<HTMLButtonElement>('[data-add-node]')!.click(); await settle()
    expect(root!.querySelector('[data-context-command=paste]')).toBeNull(); await close()
    await command('paste'); expect(store.draft.nodes).toHaveLength(3)
    store.undo(); expect(store.draft.nodes).toHaveLength(2)
    await menu(node('audio_output'))
    root!.querySelector<HTMLButtonElement>('[data-context-command=paste]')!.click(); await settle()
    expect(store.draft.nodes).toHaveLength(3)
    const pasted = store.selectedNode!
    const bounds = svg().getBoundingClientRect()
    expect(pasted.position).toEqual({ x: Math.round((300 - bounds.left - store.pan.x) / store.zoom), y: Math.round((220 - bounds.top - store.pan.y) / store.zoom) })
  })

  it('duplicates and deletes by menu or shortcut, keeps protected nodes and never captures native editing or locked commands', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    store.selectNode('output')
    await command('duplicate')
    expect(store.draft.nodes).toHaveLength(3)
    key(svg(), 'z', { metaKey: true }); await settle()
    expect(store.draft.nodes).toHaveLength(2)
    const input = node('audio_output').querySelector<HTMLInputElement>('input')!
    const snapshot = cloneDocument(store.draft)
    const edit = vi.spyOn(store, 'undo')
    for (const value of ['z', 'c', 'x', 'v', 'a', 'd', 'Delete', 'Backspace']) {
      expect(key(input, value, { ctrlKey: true }).defaultPrevented).toBe(false)
    }
    expect(paste('native text', input).defaultPrevented).toBe(false)
    for (const tag of ['textarea', 'select', 'div', 'audio', 'video']) {
      const control = document.createElement(tag)
      if (tag === 'div') control.setAttribute('contenteditable', 'true')
      node('audio_output').querySelector('.node-body-fields')!.append(control)
      for (const value of ['z', 'c', 'x', 'v', 'Delete', 'Backspace']) expect(key(control, value, { ctrlKey: true }).defaultPrevented).toBe(false)
      expect(paste('native text', control).defaultPrevented).toBe(false)
      control.remove()
    }
    expect(edit).not.toHaveBeenCalled(); expect(store.draft).toEqual(snapshot)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    key(svg(), 'a', { ctrlKey: true }); key(svg(), 'Delete'); await settle()
    expect(store.draft.nodes.map((entry) => entry.id)).toEqual(['input'])
    expect(root!.textContent).toContain(t('workflow.protectedSelection', { count: 1 }))
    key(svg(), 'z', { ctrlKey: true }); await settle()
    expect(store.draft).toEqual(snapshot)
    store.run = runBody({ status: 'running' }); await settle()
    key(svg(), 'd', { ctrlKey: true }); key(svg(), 'x', { ctrlKey: true }); key(svg(), 'Delete'); paste('bad')
    expect(store.draft).toEqual(snapshot)
    expect(root!.querySelector<HTMLButtonElement>('[data-editor-menu]')!.disabled).toBe(true)
  })

  it('previews file imports, cancels without mutations, applies one undoable draft and completes unchanged round trips', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    const original = cloneDocument(store.draft)
    const unchanged = serializeWorkflow(original, store.registry!)
    await file(unchanged)
    expect(root!.querySelector('[data-editor-import-apply]')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('[data-editor-import-apply]')!.click(); await settle()
    expect(root!.querySelector('[data-editor-import-apply]')).toBeNull()
    expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
    const imported = { nodes: [{ ...structuredClone(output), id: 'imported-output', position: { x: 50, y: 60 } }], edges: [] }
    const text = serializeWorkflow(imported, store.registry!)
    await file(text, '用户工作流.json')
    expect(root!.textContent).toContain('用户工作流.json')
    expect(app!._instance!.exposed!.hasOpenDialog()).toBe(true)
    root!.querySelector<HTMLButtonElement>('[data-editor-import-cancel]')!.click(); await settle()
    expect(store.draft).toEqual(original); expect(store.canUndo).toBe(false)
    await file(text)
    const requests = fetchMock.mock.calls.length
    root!.querySelector<HTMLButtonElement>('[data-editor-import-apply]')!.click(); await settle(); await frame()
    expect(store.draft).toEqual(imported); expect(store.dirty).toBe(true)
    expect(fetchMock.mock.calls.length).toBe(requests)
    key(svg(), 'z', { ctrlKey: true }); await settle()
    expect(store.draft).toEqual(original); expect(store.dirty).toBe(false); expect(store.canUndo).toBe(false)
  })

  it('rejects oversized or malformed files before mutation and ignores reads completed after cancel, context changes or a run lock', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    const original = cloneDocument(store.draft)
    const reader = await file('{}', 'large.json', 4 * 1024 * 1024 + 1)
    expect(reader).not.toHaveBeenCalled(); expect(store.error).toBe(t('workflow.transferTooLarge'))
    await file('{ broken')
    expect(store.error).toBe(t('workflow.transferInvalidJson')); expect(store.draft).toEqual(original)
    const text = serializeWorkflow({ nodes: [], edges: [] }, store.registry!)
    for (const cancel of ['cancel', 'context', 'lock']) {
      const pending = deferred<string>()
      await file(pending.promise)
      expect(app!._instance!.exposed!.hasOpenDialog()).toBe(true)
      if (cancel === 'cancel') button(t('workflow.editorCancelImport'))!.click()
      else if (cancel === 'context') store.projectId = 'p2'
      else store.run = runBody({ status: 'running' })
      await settle(); pending.resolve(text); await settle()
      expect(root!.querySelector('[data-editor-import-apply]')).toBeNull()
      expect(store.draft).toEqual(original)
      store.run = null; store.projectId = 'p1'; await settle()
    }
  })

  it('keeps overlapping imported nodes near the coordinate boundary without placement errors or measurement edits', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    const original = cloneDocument(store.draft)
    const graph = { nodes: ['near-one', 'near-two'].map((id) => ({ ...structuredClone(output), id, position: { x: 999_990, y: 999_990 } })), edges: [] }
    await file(serializeWorkflow(graph, store.registry!))
    root!.querySelector<HTMLButtonElement>('[data-editor-import-apply]')!.click(); await settle(); await frame()
    expect(store.draft).toEqual(graph); expect(store.error).toBe('')
    expect(store.canUndo).toBe(true)
    key(svg(), 'z', { ctrlKey: true }); await settle(); await frame()
    expect(store.draft).toEqual(original); expect(store.canUndo).toBe(false)
    key(svg(), 'y', { ctrlKey: true }); await settle(); await frame()
    expect(store.draft).toEqual(graph); expect(store.error).toBe('')
  })

  it('exports the current portable draft without saves, history or selection changes and localizes menu commands', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    const original = cloneDocument(store.draft)
    const createObjectURL = vi.fn().mockReturnValue('blob:workflow')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const requests = fetchMock.mock.calls.length
    await command('export')
    expect(createObjectURL).toHaveBeenCalledTimes(1); expect(click).toHaveBeenCalledTimes(1)
    expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe('workflow.json')
    expect(store.draft).toEqual(original); expect(store.canUndo).toBe(false); expect(store.dirty).toBe(false)
    expect(fetchMock.mock.calls.length).toBe(requests)
    setLocale('en'); root!.querySelector<HTMLButtonElement>('[data-editor-menu]')!.click(); await settle()
    expect(root!.textContent).toContain('Import workflow JSON'); expect(root!.textContent).toContain('Ctrl/Cmd+Z')
    expect(app!._instance!.exposed!.hasOpenDialog()).toBe(true)
    key(root!.querySelector('.editor-command-menu')!, 'Escape'); await settle()
    expect(app!._instance!.exposed!.hasOpenDialog()).toBe(false)
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:workflow')
    delete (URL as any).createObjectURL; delete (URL as any).revokeObjectURL
  })
})

describe('catalog-driven parameter connections', () => {
  async function parameterNodes() {
    const catalog = structuredClone(testCapabilities) as any
    catalog.workflow.schema_version = '1.5'
    catalog.workflow.data_types = testRegistry.capabilities.dataTypes.map((type) => ({ id: type.id, label: type.label, parent_type_id: type.parentTypeId, capabilities: [...type.capabilities] }))
    for (const declaration of catalog.workflow.nodes) {
      declaration.required_input_ports = [...declaration.input_ports]
      declaration.is_parameter_source = false
      for (const field of declaration.config.fields) field.input_port = null
    }
    for (const type of ['string', 'number', 'integer', 'boolean']) {
      catalog.workflow.data_types.push({ id: type, label: type, parent_type_id: type === 'integer' ? 'number' : null, capabilities: [] })
      catalog.workflow.ports.push({ id: type, type_id: type, label: type })
      catalog.workflow.nodes.push({ type: `${type}_value`, label: type, input_ports: [], output_ports: [type], required_input_ports: [], required_inbound: 0, is_parameter_source: true, config: { kind: 'object', fields: [{ key: 'value', label: 'value', type, default: type === 'string' ? 'en' : type === 'boolean' ? false : 2, input_port: null }] } })
    }
    for (const [type, key, port] of [['asr', 'language', 'language'], ['audio_output', 'filename_stem', 'filename_stem'], ['text_output', 'filename_stem', 'filename_stem'], ['text_output', 'format', 'text_format']]) {
      if (!catalog.workflow.ports.some((entry: any) => entry.id === port)) catalog.workflow.ports.push({ id: port, type_id: 'string', label: port })
      const declaration = catalog.workflow.nodes.find((entry: any) => entry.type === type)
      declaration.input_ports.push(port)
      declaration.config.fields.find((field: any) => field.key === key).input_port = port
    }
    catalog.workflow.ports.push({ id: 'count_parameter', type_id: 'number', label: 'Extension socket' })
    const outputKind = catalog.workflow.nodes.find((entry: any) => entry.type === 'audio_output')
    outputKind.input_ports.push('count_parameter')
    outputKind.config.fields.push({ key: 'count', type: 'number', label: 'Extension count', default: 2, input_port: 'count_parameter' })
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(catalog)) : undefined)
    await settle()
    const store = useWorkflowStore()
    expect(store.capabilitiesError).toBe('')
    for (const type of ['string', 'integer', 'number', 'boolean']) store.addNode(`${type}_value`)
    store.addNode('string_value')
    store.addNode('asr')
    await settle()
    const source = store.draft.nodes.find((entry) => entry.type === 'string_value')!
    const other = store.draft.nodes.filter((entry) => entry.type === 'string_value')[1]!
    const target = store.draft.nodes.find((entry) => entry.type === 'asr')!
    return { store, source, other, target }
  }
  function socket(id: string, port: string, direction: string) { return root!.querySelector<SVGElement>(`[data-node-id="${id}"] [data-port-direction="${direction}"][data-port-id="${port}"]`)! }
  function pointer(target: EventTarget, type: string) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: 400, clientY: 250 })
    Object.defineProperty(event, 'pointerId', { value: 7 }); target.dispatchEvent(event)
  }

  it('distinguishes parameter sockets by mapped field labels and logical types in both locales', async () => {
    const { store, target } = await parameterNodes()
    store.addNode('text_output'); await settle()
    const text = store.draft.nodes.find((entry) => entry.type === 'text_output')!
    const snapshot = JSON.stringify(store.draft)
    for (const language of ['zh-CN', 'en']) {
      setLocale(language); await settle()
      for (const [id, port, fieldKey, typeId] of [
        [text.id, 'filename_stem', 'filename_stem', 'string'],
        [text.id, 'text_format', 'format', 'string'],
        [target.id, 'language', 'language', 'string'],
        ['output', 'count_parameter', 'count', 'number'],
      ]) {
        const definition = store.registry!.kind(store.draft.nodes.find((entry) => entry.id === id)!.type)!
        const field = definition.fields.find((entry) => entry.key === fieldKey)!
        const label = `${field.label} · ${t(`workflow.dataType_${typeId}`)} (${t('workflow.optionalPort')})`
        const circle = socket(id, port, 'input')
        expect(circle.parentElement!.querySelector('.node-port-label-in')!.childNodes[1].textContent).toBe(label)
        expect(circle.parentElement!.querySelector('.node-port-label-in title')!.textContent).toBe(label)
        expect(circle.getAttribute('aria-label')).toBe(t('workflow.inputPort', { port: `${label} · ${port}` }))
        expect(circle.querySelector('title')!.textContent).toBe(circle.getAttribute('aria-label'))
      }
      expect(node('text_output').textContent).toContain(language === 'en' ? 'Format · String (optional)' : '格式 · 字符串 (可选)')
      expect(node('audio_output').textContent).toContain(`Extension count · ${t('workflow.dataType_number')}`)
      expect(JSON.stringify(store.draft)).toBe(snapshot)
    }
  })

  it('drags parameter sockets, preserves fallback values on cancel, rewire and detach, and retains locale state', async () => {
    const { store, source, other, target } = await parameterNodes()
    expect(store.draft.nodes.filter((entry) => store.registry?.kind(entry.type)?.isSource)).toHaveLength(1)
    expect(node('string_value').textContent).toContain('字符串值')
    const control = () => node('asr').querySelector<HTMLInputElement>('[data-field="language"]')!
    control().value = 'ja-JP'; control().dispatchEvent(new Event('input', { bubbles: true })); await settle()
    pointer(socket(source.id, 'string', 'output'), 'pointerdown')
    pointer(socket(target.id, 'language', 'input'), 'pointerup'); await settle()
    expect(control().disabled).toBe(true); expect(control().value).toBe('ja-JP')
    expect(control().closest<HTMLElement>('.node-config-row')!.style.display).toBe('none')
    expect(node('asr').querySelectorAll('.node-config-row')).toHaveLength(2)
    expect((node('asr').querySelector('[data-field="provider"]')!.closest('.node-config-row') as HTMLElement).style.display).not.toBe('none')
    expect(target.config.language).toBe('ja-JP')
    expect(node('asr').textContent).toContain(`${source.id} · string`)
    const before = JSON.stringify(store.draft)
    for (const language of ['en', 'zh-CN']) {
      setLocale(language); store.selectNode('input'); await settle()
      expect(control().value).toBe('ja-JP'); expect(control().disabled).toBe(true)
      expect(control().closest<HTMLElement>('.node-config-row')!.style.display).toBe('none')
      expect(JSON.stringify(store.draft)).toBe(before)
    }
    pointer(socket(target.id, 'language', 'input'), 'pointerdown')
    pointer(root!.querySelector('.workflow-svg')!, 'pointerup'); await settle()
    expect(JSON.stringify(store.draft)).toBe(before)
    expect(store.connect(other.id, target.id, 'string', 'language')).toBe(true); await settle()
    expect(node('asr').textContent).toContain(`${other.id} · string`)
    store.disconnect(store.draft.edges.find((edge) => edge.target === target.id)!.id); await settle()
    expect(control().disabled).toBe(false); expect(control().value).toBe('ja-JP')
    expect(control().closest<HTMLElement>('.node-config-row')!.style.display).not.toBe('none')
    expect(target.config.language).toBe('ja-JP')
    expect(store.draft.edges).toContainEqual(expect.objectContaining({ target: 'output', target_port: 'audio' }))
  })

  it('keeps incomplete wired fallback input dirty and invalid, and uses inherited types for extension mappings', async () => {
    const { store } = await parameterNodes()
    const integer = store.draft.nodes.find((entry) => entry.type === 'integer_value')!
    const control = () => node('audio_output').querySelector<HTMLInputElement>('[data-field="count"]')!
    control().value = '-'; control().dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(store.connect(integer.id, 'output', 'integer', 'count_parameter')).toBe(true); await settle()
    expect(control().value).toBe('-'); expect(control().disabled).toBe(true)
    expect(control().closest<HTMLElement>('.node-config-row')!.style.display).toBe('none')
    expect(control().getAttribute('aria-invalid')).toBe('true')
    expect(store.dirty).toBe(true)
    const calls = fetchMock.mock.calls.length
    await store.save(); await store.validate()
    await settle()
    expect(fetchMock.mock.calls.length).toBe(calls)
    expect(store.validationIssues).toContainEqual(expect.objectContaining({ node_id: 'output', field: 'count' }))
    expect(node('audio_output').querySelector('.node-error-mark')).not.toBeNull()
    setLocale('en'); await settle()
    expect(control().value).toBe('-')
    store.disconnect(store.draft.edges.find((edge) => edge.target_port === 'count_parameter')!.id); await settle()
    expect(control().value).toBe('-'); expect(control().disabled).toBe(false)
    expect(store.draft.nodes.find((entry) => entry.id === 'output')!.config.count).toBe('-')
  })

  it('collapses every wired row and measured body, retaining raw numeric text, explicit size and clean layout through detach', async () => {
    const { store, source } = await parameterNodes()
    const integer = store.draft.nodes.find((entry) => entry.type === 'integer_value')!
    const output = () => store.draft.nodes.find((entry) => entry.id === 'output')!
    const control = () => node('audio_output').querySelector<HTMLInputElement>('[data-field="count"]')!
    const frames = async () => { await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); await settle() }
    control().value = '1e2'; control().dispatchEvent(new Event('input', { bubbles: true })); await settle(); await frames()
    expect(output().config.count).toBe(100)
    const originalHeight = Number(node('audio_output').querySelector('.node-card')!.getAttribute('height'))
    const position = { ...output().position }
    store.setNodeMinimum('output', { width: 500, height: 500 })
    expect(store.connect(source.id, 'output', 'string', 'filename_stem')).toBe(true)
    expect(store.connect(integer.id, 'output', 'integer', 'count_parameter')).toBe(true)
    await settle(); await frames()
    const collapsedHeight = workflowNodeHeight(output().type, store.registry!, 'output', store.draft.edges)
    expect(workflowFieldsHeight(output().type, store.registry!, 'output', store.draft.edges)).toBe(0)
    expect(store.getNodeSize(output()).height).toBe(collapsedHeight)
    expect(collapsedHeight).toBeLessThan(originalHeight)
    expect(Number(node('audio_output').querySelector('.node-card')!.getAttribute('height'))).toBe(collapsedHeight)
    expect(node('audio_output').querySelector<SVGElement>('foreignObject')!.style.display).toBe('none')
    expect(node('audio_output').querySelector<HTMLElement>('.node-body-fields')!.style.display).toBe('none')
    expect(node('audio_output').querySelector('.node-meta')).toBeNull()
    expect(control().value).toBe('1e2')
    expect(socket('output', 'count_parameter', 'input').getAttribute('aria-label')).toContain(`${integer.id} · integer`)
    expect(output().size).toBeUndefined(); expect(output().position).toEqual(position)
    store.updateNode('output', { size: { width: 800, height: 600 } })
    store.document = { ...store.document!, ...cloneDocument(store.draft) }
    store.setCleanBaseline(store.draft)
    const snapshot = JSON.stringify(store.draft)
    setLocale('en'); await settle(); await frames()
    expect(store.dirty).toBe(false); expect(JSON.stringify(store.draft)).toBe(snapshot)
    expect(store.getNodeSize(output())).toEqual({ width: 800, height: 600 })
    store.disconnect(store.draft.edges.find((edge) => edge.target_port === 'count_parameter')!.id)
    await settle(); await frames()
    expect(control().closest<HTMLElement>('.node-config-row')!.style.display).not.toBe('none')
    expect(control().value).toBe('1e2'); expect(control().disabled).toBe(false)
    expect(workflowFieldsHeight(output().type, store.registry!, 'output', store.draft.edges)).toBe(WORKFLOW_NODE_FIELD_HEIGHT)
    expect(node('audio_output').querySelector<SVGElement>('foreignObject')!.style.display).not.toBe('none')
    expect(output().size).toEqual({ width: 800, height: 600 }); expect(output().position).toEqual(position)
    store.run = runBody({ status: 'running' }); await settle()
    expect(control().disabled).toBe(true)
    expect(store.connect(integer.id, 'output', 'integer', 'count_parameter')).toBe(false)
    expect(control().closest<HTMLElement>('.node-config-row')!.style.display).not.toBe('none')
  })
})

describe('catalog-driven multiple input and output ports', () => {
  async function multiNodes() {
    const catalog = structuredClone(testCapabilities) as any
    catalog.workflow.schema_version = '1.4'
    catalog.workflow.data_types = testRegistry.capabilities.dataTypes.map((type) => ({
      id: type.id, label: type.label, parent_type_id: type.parentTypeId, capabilities: [...type.capabilities],
    }))
    for (const entry of catalog.workflow.nodes) entry.required_input_ports = [...entry.input_ports]
    for (const id of ['voice', 'background', 'reference', 'dry_audio', 'wet_audio']) {
      catalog.workflow.ports.push({ id, type_id: 'audio', label: id, capability_requirements: [] })
    }
    catalog.workflow.nodes.push({
      type: 'multi', label: 'Multi port', input_ports: ['voice', 'background', 'reference'],
      output_ports: ['dry_audio', 'wet_audio'], required_input_ports: ['voice', 'background'], required_inbound: 2,
      config: { kind: 'multi', fields: [{ key: 'strength', type: 'integer', default: 2 }] },
    })
    catalog.workflow.nodes.push({
      type: 'multi_split', label: 'Split', input_ports: ['audio'], output_ports: ['dry_audio', 'wet_audio'],
      required_input_ports: ['audio'], required_inbound: 1, config: { kind: 'none', fields: [] },
    })
    setup((path) => path.endsWith('/capabilities') ? Promise.resolve(response(catalog)) : undefined)
    await settle()
    const store = useWorkflowStore()
    expect(store.capabilitiesError).toBe('')
    store.addNode('multi', { x: 100, y: 200 })
    store.addNode('multi', { x: 500, y: 300 })
    await settle()
    const nodes = store.draft.nodes.filter((entry) => entry.type === 'multi')
    const socket = (id: string, port: string, direction: 'input' | 'output') => root!.querySelector<SVGElement>(`[data-node-id="${id}"] [data-port-direction="${direction}"][data-port-id="${port}"]`)!
    return { store, first: nodes[0]!, second: nodes[1]!, socket }
  }
  function pointer(target: EventTarget, type: string) {
    const event = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientX: 400, clientY: 250 })
    Object.defineProperty(event, 'pointerId', { value: 7 })
    target.dispatchEvent(event)
  }

  it('renders every socket in separate rows with settings below the final row', async () => {
    const { store, first, socket } = await multiNodes()
    const node = root!.querySelector(`[data-node-id="${first.id}"]`)!
    expect(node.querySelectorAll('.node-port-in')).toHaveLength(3)
    expect(node.querySelectorAll('.node-port-out')).toHaveLength(2)
    expect(['voice', 'background', 'reference'].map((port) => socket(first.id, port, 'input').getAttribute('cy'))).toEqual(['45', '69', '93'])
    expect(node.querySelector('foreignObject')!.getAttribute('y')).toBe('110')
    expect(Number(node.querySelector('.node-resize-handle.is-right')!.getAttribute('y'))).toBeGreaterThan(99)
    expect(Number(node.querySelector('.node-card')!.getAttribute('height'))).toBeGreaterThanOrEqual(164)
    expect(store.registry!.kind('multi')!.requiredInputPorts).toEqual(['voice', 'background'])
    expect(node.textContent).toContain('reference (可选)')
    expect(store.registry!.inputPortForNode('multi')).toBeNull()
    expect(store.registry!.outputPortForNode('multi')).toBeNull()
    store.addNode('multi_split')
    await settle()
    const split = root!.querySelector('.is-multi_split')!
    const cardHeight = Number(split.querySelector('.node-card')!.getAttribute('height'))
    const metaY = Number(split.querySelector('.node-meta')!.getAttribute('y'))
    expect(cardHeight).toBeGreaterThanOrEqual(metaY + 10)
    expect(Number(split.querySelector('.node-resize-handle.is-right')!.getAttribute('y'))).toBeGreaterThan(75)
  })

  it('normalizes either drag direction using exact socket IDs and keeps other inputs connected', async () => {
    const { store, first, second, socket } = await multiNodes()
    pointer(socket(first.id, 'dry_audio', 'output'), 'pointerdown')
    pointer(socket(second.id, 'voice', 'input'), 'pointerup')
    pointer(socket(second.id, 'background', 'input'), 'pointerdown')
    pointer(socket(first.id, 'wet_audio', 'output'), 'pointerup')
    await settle()
    expect(store.draft.edges.filter((edge) => edge.target === second.id)).toEqual([
      expect.objectContaining({ source: first.id, source_port: 'dry_audio', target_port: 'voice' }),
      expect.objectContaining({ source: first.id, source_port: 'wet_audio', target_port: 'background' }),
    ])
    const before = JSON.stringify(store.draft.edges)
    pointer(socket(second.id, 'voice', 'input'), 'pointerdown')
    pointer(root!.querySelector('.workflow-svg')!, 'pointerup')
    expect(JSON.stringify(store.draft.edges)).toBe(before)
    pointer(socket(second.id, 'voice', 'input'), 'pointerdown')
    pointer(socket('input', 'audio', 'output'), 'pointerup')
    await settle()
    const edges = store.draft.edges.filter((edge) => edge.target === second.id)
    expect(edges).toHaveLength(2)
    expect(edges).toContainEqual(expect.objectContaining({ source: first.id, source_port: 'wet_audio', target_port: 'background' }))
    expect(edges).toContainEqual(expect.objectContaining({ source: 'input', source_port: 'audio', target_port: 'voice' }))
    const from = store.draft.nodes.find((entry) => entry.id === first.id)!
    const to = store.draft.nodes.find((entry) => entry.id === second.id)!
    const line = Array.from(root!.querySelectorAll('.workflow-edge')).find((path) => path.getAttribute('d')?.startsWith(`M ${from.position.x + store.getNodeSize(from).width} ${from.position.y + 69}`))
    expect(line?.getAttribute('d')).toContain(`${to.position.x} ${to.position.y + 69}`)
  })

  it('requires explicit multi-port choices, preserves parallel edges, and reconnects at the edge limit', async () => {
    const { store, first, second } = await multiNodes()
    expect(store.connect(first.id, second.id)).toBe(false)
    expect(store.connect(first.id, second.id, 'dry_audio', 'voice')).toBe(true)
    expect(store.connect(first.id, second.id, 'wet_audio', 'background')).toBe(true)
    store.capabilities!.constraints.maxEdges = store.draft.edges.length
    expect(store.connect('input', second.id, 'audio', 'voice')).toBe(true)
    expect(store.draft.edges.filter((edge) => edge.target === second.id)).toHaveLength(2)
    const before = JSON.stringify(store.draft.edges)
    expect(store.connect(first.id, second.id, 'voice', 'reference')).toBe(false)
    expect(store.connect(first.id, second.id, 'dry_audio', 'reference')).toBe(false)
    expect(store.connect(second.id, first.id, 'dry_audio', 'voice')).toBe(false)
    expect(JSON.stringify(store.draft.edges)).toBe(before)
    for (const state of ['saving', 'validating', 'resourceUploading'] as const) {
      store[state] = true
      expect(store.connect('input', first.id, 'audio', 'voice')).toBe(false)
      store[state] = false
    }
  })
})

describe('resource collection and shared execution', () => {
  const audio = (id: string) => ({ id, source: { original_name: `${id}.wav`, language: 'ja', size_bytes: 4 } })
  function collectionCatalog(collectionKey = 'audio_ids') {
    const catalog = structuredClone(testCapabilities)
    catalog.workflow.schema_version = '1.6'
    for (const node of catalog.workflow.nodes) {
      node.is_parameter_source = false
      node.required_input_ports = node.required_inbound ? [...node.input_ports] : []
      for (const field of node.config.fields) field.input_port = null
    }
    const source = catalog.workflow.nodes.find((node: any) => node.type === 'audio_input')
    source.presentation = { resource_input: { resource_type: 'audio', config_key: 'audio_id', collection_key: collectionKey } }
    source.config.fields = [
      { key: 'audio_id', type: 'string', required: false, default: null, input_port: null },
      { key: collectionKey, type: 'json', required: false, default: null, input_port: null },
    ]
    const output = catalog.workflow.nodes.find((node: any) => node.type === 'audio_output')
    output.config.fields.push({ key: 'naming_mode', label: 'Naming mode', type: 'select', required: false, default: 'exact', creation_default: 'prefix_sequence', input_port: null,
      options: [{ value: 'exact', label: 'Exact' }, { value: 'prefix_sequence', label: 'Prefix' }] })
    return catalog
  }
  function setupCollection(config: Record<string, unknown> = {}, options: { upload?: Promise<Response>; failCreate?: boolean; collectionKey?: string } = {}) {
    const audios = reactive([audio('a1'), audio('a2')])
    let document = documentBody()
    document.nodes[0]!.config = config
    let batch: any = null
    let uploads = 0
    setup((path, init) => {
      if (path.endsWith('/capabilities')) return Promise.resolve(response(collectionCatalog(options.collectionKey)))
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 4096 }))
      if (path.endsWith('/workflow')) {
        if (init?.method === 'PUT') { const body = JSON.parse(init.body as string); document = { ...document, nodes: body.nodes, edges: body.edges, revision: document.revision + 1, workflow_revision: document.workflow_revision + 1 } }
        return Promise.resolve(response(document))
      }
      if (path.includes('/batches?')) return Promise.resolve(response({ items: batch ? [batch] : [], total: batch ? 1 : 0, limit: 20, offset: 0 }))
      if (path.endsWith('/batches') && init?.method === 'POST') {
        if (options.failCreate) return Promise.reject(new TypeError('Failed to fetch'))
        const body = JSON.parse(init.body as string)
        batch = { id: 'batch-1', project_id: 'p1', status: 'queued', workflow_revision: body.workflow_revision, created_at: '2026-11-06T00:00:00Z', started_at: null, finished_at: null,
          cancel_requested: false, retry_of: null, total: body.audio_ids.length, counts: { queued: body.audio_ids.length, running: 0, completed: 0, partial: 0, failed: 0, cancelled: 0, interrupted: 0 }, error: null }
        return Promise.resolve(response(batch, 202))
      }
      if (path.endsWith('/batches/batch-1')) return Promise.resolve(response({ ...batch, workflow: document }))
      if (path.includes('/batches/batch-1/items?')) return Promise.resolve(response({ items: [{ index: 0, sequence: 4, audio_id: 'a1', audio_name: 'a1.wav', source_language: 'ja', origin: null, status: 'queued', run_id: null, started_at: null, finished_at: null, error: null }], total: batch.total, limit: 20, offset: 0 }))
      if (path.endsWith('/audios') && init?.method === 'POST') return options.upload || Promise.resolve(response({ audio: { id: `uploaded-${++uploads}`, source: { original_name: ((init.body as FormData).get('file') as File).name, language: 'ja', size_bytes: 4 } } }, 201))
      if (path.endsWith('/workflow-runs') && init?.method === 'POST') return Promise.resolve(response(runBody({ audio_id: path.includes('/audios/a2/') ? 'a2' : 'a1', workflow: document }), 202))
      return undefined
    }, createPinia(), { audios, onBatchUploaded: (value: any) => audios.push(value) })
    return { audios }
  }
  async function enableCollection() { expect(root!.querySelector('[data-collection-enable]')).toBeNull(); const mode = root!.querySelector<HTMLSelectElement>('[data-collection-mode]')!; mode.value = 'collection'; mode.dispatchEvent(new Event('change', { bubbles: true })); await settle() }
  async function chooseFiles(files = [new File(['RIFF'], '录音.wav')]) {
    const language = root!.querySelector<HTMLInputElement>('[data-batch-language]')!
    language.value = 'ja'; language.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    const picker = root!.querySelector<HTMLInputElement>('[data-batch-file-input]')!
    Object.defineProperty(picker, 'files', { configurable: true, value: files })
    picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  }
  async function addExisting(id: string) {
    const select = root!.querySelector<HTMLSelectElement>('[data-manifest-existing]')!
    select.value = id; select.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    root!.querySelector<HTMLButtonElement>('[data-manifest-add-existing]')!.click(); await settle()
  }

  it('preserves legacy context until an explicit collection change and allows saving an empty list', async () => {
    setupCollection(); await settle()
    const store = useWorkflowStore()
    expect(store.boundAudioIds).toBeNull()
    expect(store.draft.nodes[0]!.config).toEqual({})
    expect(root!.querySelector('[data-field=audio_ids]')).toBeNull()
    await enableCollection()
    expect(store.draft.nodes[0]!.config).toEqual({ audio_ids: [] })
    expect(store.registry!.validateConfig('audio_input', store.draft.nodes[0]!.config)).toEqual([])
    await store.save(); await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.title).toBe(t('batch.empty'))
  })

  it('uploads inside the input node and synchronizes ordered IDs only after releasing the upload lock', async () => {
    const pending = deferred<Response>()
    setupCollection({ audio_ids: [] }, { upload: pending.promise }); await settle(); await chooseFiles()
    const store = useWorkflowStore()
    root!.querySelector<HTMLButtonElement>('[data-batch-upload]')!.click(); await settle()
    expect(store.resourceUploading).toBe(true)
    expect(store.boundAudioIds).toEqual([])
    expect(app!._instance!.exposed!.requestClose(vi.fn())).toBe('blocked')
    pending.resolve(response({ audio: audio('uploaded-1') }, 201)); await settle()
    expect(store.resourceUploading).toBe(false)
    expect(store.boundAudioIds).toEqual(['uploaded-1'])
    expect(store.dirty).toBe(true)
    expect(root!.querySelectorAll('[data-batch-item]')).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).endsWith('/workflow-runs') && init?.method === 'POST')).toBe(false)
  })

  it('keeps pending Files across undo, redo, locale changes and modal opens, and protects navigation', async () => {
    setupCollection({ audio_ids: ['a1'] }); await settle(); await chooseFiles()
    const store = useWorkflowStore()
    await addExisting('a2')
    expect(store.boundAudioIds).toEqual(['a1', 'a2'])
    store.undo(); await settle()
    expect(store.boundAudioIds).toEqual(['a1'])
    expect(root!.textContent).toContain('录音.wav')
    store.redo(); await settle()
    expect(store.boundAudioIds).toEqual(['a1', 'a2'])
    setLocale('en'); await settle()
    root!.querySelector<HTMLButtonElement>('[data-open-panel=batch]')!.click(); await settle()
    expect(root!.querySelector('[data-batch-panel] [data-batch-file-input]')).toBeNull()
    root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click(); await settle()
    expect(root!.textContent).toContain('录音.wav')
    const leave = vi.fn()
    expect(app!._instance!.exposed!.requestClose(leave)).toBe('pending'); await settle()
    expect(root!.querySelectorAll('[role=dialog]')).toHaveLength(1)
    expect(root!.textContent).toContain(t('batch.leaveFiles'))
  })

  it('restores saved selection order and keeps missing IDs visible instead of silently deleting them', async () => {
    setupCollection({ audio_ids: ['a2', 'missing', 'a1'] }); await settle()
    const store = useWorkflowStore()
    const rows = root!.querySelectorAll('[data-batch-item]')
    expect(rows[0]!.textContent).toContain('a2.wav')
    expect(rows[1]!.textContent).toContain('missing')
    expect(rows[1]!.textContent).toContain(t('workflow.resourceMissing'))
    expect(store.boundAudioIds).toEqual(['a2', 'missing', 'a1'])
    expect(store.dirty).toBe(false)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
  })

  it('synchronizes exclusion and undo without deleting the uploaded resource', async () => {
    const { audios } = setupCollection({ audio_ids: ['a1', 'a2'] }); await settle()
    root!.querySelector<HTMLInputElement>('[data-manifest-selected]')!.click(); await settle()
    const store = useWorkflowStore()
    expect(store.boundAudioIds).toEqual(['a2'])
    store.undo(); await settle()
    expect(store.boundAudioIds).toEqual(['a1', 'a2'])
    expect(audios).toHaveLength(2)
  })

  it('blocks the successful subset while a selected file is pending, then allows explicit exclusion', async () => {
    setupCollection({ audio_ids: ['a1'] }); await settle(); await chooseFiles()
    const store = useWorkflowStore()
    await store.save(); await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.title).toBe(t('batch.unready'))
    const checkboxes = root!.querySelectorAll<HTMLInputElement>('[data-manifest-selected]')
    checkboxes[1]!.click(); await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(false)
  })

  it('routes one saved audio through the single-run API from the canvas', async () => {
    setupCollection({ audio_ids: ['a2'] }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.click(); await settle()
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).endsWith('/audios/a2/workflow-runs') && init?.method === 'POST')).toBe(true)
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).endsWith('/batches') && init?.method === 'POST')).toBe(false)
    expect(useWorkflowStore().activeSingleRun).toBe(true)
  })

  it.each(['canvas', 'panel'])('routes the full saved list through one batch API from the %s', async (entry) => {
    setupCollection({ audio_ids: ['a2', 'a1'] }); await settle()
    if (entry === 'panel') await openPanel('run')
    root!.querySelector<HTMLButtonElement>(entry === 'canvas' ? '[data-run-workflow]' : '[data-panel-run]')!.click(); await settle()
    const creates = fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/batches') && init?.method === 'POST')
    expect(creates).toHaveLength(1)
    expect(JSON.parse(creates[0]![1]!.body as string)).toEqual({ workflow_revision: 1, audio_ids: ['a2', 'a1'], request_id: expect.any(String) })
    expect(root!.querySelectorAll('[role=dialog]')).toHaveLength(1)
    expect(root!.querySelector('[data-batch-run-item] td')!.textContent).toBe('4')
    expect(root!.querySelector('[data-batch-panel] [data-batch-start]')).toBeNull()
  })

  it('keeps old fixed single bindings until explicitly converting them to a collection', async () => {
    setupCollection({ audio_id: 'a2' }); await settle()
    const store = useWorkflowStore()
    expect(store.boundAudioId).toBe('a2')
    expect(store.boundAudioIds).toBeNull()
    expect(store.dirty).toBe(false)
    await enableCollection()
    expect(store.draft.nodes[0]!.config).toEqual({ audio_ids: ['a2'] })
  })

  it('uses resource metadata for an extension collection key and strips both bindings on export', async () => {
    setupCollection({ custom_list: ['a2'] }, { collectionKey: 'custom_list' }); await settle()
    const store = useWorkflowStore()
    expect(store.boundAudioIds).toEqual(['a2'])
    expect(root!.querySelector('[data-field=custom_list]')).toBeNull()
    const transfer = parseWorkflowFile(serializeWorkflow(store.draft, store.registry!), store.registry!)
    expect(transfer.nodes[0]!.config).toEqual({})
    expect(store.draft.nodes[0]!.config).toEqual({ custom_list: ['a2'] })
  })

  it('preserves exact naming for old graphs and applies creation_default only to new nodes', async () => {
    setupCollection(); await settle()
    const store = useWorkflowStore()
    expect(node('audio_output').querySelector<HTMLSelectElement>('[data-field=naming_mode]')!.value).toBe('exact')
    expect(store.draft.nodes[1]!.config).not.toHaveProperty('naming_mode')
    store.addNode('audio_output')
    const created = store.draft.nodes[store.draft.nodes.length - 1]!
    expect(created.config.naming_mode).toBe('prefix_sequence')
    expect(store.registry!.createDefaultConfig('audio_output').naming_mode).toBe('exact')
    expect(store.registry!.kind('audio_output')!.fields.find((field) => field.key === 'naming_mode')!.inputPort).toBeNull()
    expect(executionSignature(documentBody(), store.registry!)).toBe(executionSignature({ ...documentBody(), nodes: documentBody().nodes.map((node) => node.type === 'audio_output' ? { ...node, config: { ...node.config, naming_mode: 'exact' } } : node) }, store.registry!))
  })

  it('keeps unconfirmed multi-run submissions guarded and restores their original request ID', async () => {
    setupCollection({ audio_ids: ['a1', 'a2'] }, { failCreate: true }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.click(); await settle()
    expect(root!.querySelector('[data-batch-retry-request]')).not.toBeNull()
    const original = JSON.parse(fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/batches') && init?.method === 'POST')![1]!.body as string)
    root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click(); await settle()
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(app!._instance!.exposed!.requestClose(vi.fn())).toBe('pending'); await settle()
    expect(root!.textContent).toContain(t('batch.leavePending'))
    app!.unmount(); root!.remove()
    setupCollection({ audio_ids: ['a1', 'a2'] }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-open-panel=batch]')!.click(); await settle()
    root!.querySelector<HTMLButtonElement>('[data-batch-retry-request]')!.click(); await settle()
    const repeated = JSON.parse(fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/batches') && init?.method === 'POST')![1]!.body as string)
    expect(repeated).toEqual(original)
  })

  it('keeps batch children editable and navigable, while genuine single runs still lock edits', async () => {
    setupCollection({ audio_ids: ['a1', 'a2'] }); await settle()
    const store = useWorkflowStore()
    store.run = runBody({ status: 'running', batch_id: 'bat_12345678901234567890123456789012', batch_item_sequence: 2 })
    store.updateNode('output', { config: { filename_stem: 'updated' } })
    expect(store.draft.nodes[1]!.config.filename_stem).toBe('updated')
    expect(store.activeSingleRun).toBe(false)
    store.discardDraft()
    expect(app!._instance!.exposed!.requestClose(vi.fn())).toBe('completed')
    store.run = runBody({ status: 'running' })
    store.updateNode('output', { config: { filename_stem: 'blocked' } })
    expect(store.draft.nodes[1]!.config.filename_stem).toBe('audio-copy')
    expect(app!._instance!.exposed!.requestClose(vi.fn())).toBe('blocked')
  })

  it('paginates local files without expanding the node or losing off-page selection', async () => {
    setupCollection({ audio_ids: [] }); await settle()
    await chooseFiles(Array.from({ length: 25 }, (_, index) => new File(['RIFF'], `${index}.wav`)))
    expect(root!.querySelectorAll('[data-batch-item]')).toHaveLength(20)
    root!.querySelector<HTMLInputElement>('[data-manifest-selected]')!.click(); await settle()
    root!.querySelector<HTMLButtonElement>('[data-manifest-next]')!.click(); await settle()
    expect(root!.querySelectorAll('[data-batch-item]')).toHaveLength(5)
    root!.querySelector<HTMLButtonElement>('[data-manifest-previous]')!.click(); await settle()
    expect(root!.querySelector<HTMLInputElement>('[data-manifest-selected]')!.checked).toBe(false)
  })

  it('restores recursive inclusion when undo restores a selected subfolder resource', async () => {
    const { audios } = setupCollection({ audio_ids: [] }); await settle()
    ;(audios[1]!.source as any).origin = { root_id: 'root', root_name: 'Album', relative_path: 'nested/a2.wav' }
    const store = useWorkflowStore()
    store.updateNode('input', { config: { audio_ids: ['a1', 'a2'] } }); await settle()
    const recursive = root!.querySelector<HTMLInputElement>('[data-batch-recursive]')!
    recursive.click(); await settle()
    expect(store.boundAudioIds).toEqual(['a1'])
    expect(recursive.checked).toBe(false)
    store.undo(); await settle()
    expect(store.boundAudioIds).toEqual(['a1', 'a2'])
    expect(recursive.checked).toBe(true)
    expect([...root!.querySelectorAll<HTMLInputElement>('[data-manifest-selected]')].every((input) => input.checked)).toBe(true)
    store.redo(); await settle()
    expect(store.boundAudioIds).toEqual(['a1'])
    expect(root!.querySelectorAll('[data-batch-item]:not(.batch-excluded)')).toHaveLength(1)
    store.undo(); await settle()
    expect(root!.querySelectorAll('[data-batch-item]:not(.batch-excluded)')).toHaveLength(2)
    await store.save(); await settle()
    root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.click(); await settle()
    const request = fetchMock.mock.calls.find(([path, init]) => String(path).endsWith('/batches') && init?.method === 'POST')!
    expect(JSON.parse(request[1]!.body as string).audio_ids).toEqual(['a1', 'a2'])
  })

  it.each([{ audio_ids: ['a2'] }, { audio_id: 'a2' }])('allows an idle fixed target while another audio has a batch child: %j', async (config) => {
    setupCollection(config); await settle()
    const store = useWorkflowStore()
    store.run = runBody({ audio_id: 'a1', status: 'running', batch_id: 'bat_12345678901234567890123456789012' })
    await settle()
    const run = root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!
    expect(run.disabled).toBe(false)
    run.click(); await settle()
    expect(fetchMock.mock.calls.some(([path, init]) => String(path).endsWith('/audios/a2/workflow-runs') && init?.method === 'POST')).toBe(true)
  })
})
describe('node rerun planning UI', () => {
  const validPlan = { valid: true, execute_node_ids: ['output'], reuse_node_ids: ['input'], skipped_node_ids: [], issues: [] }
  async function ready(override?: FetchOverride) {
    setup(override, createPinia(), { audios: ['a1', 'a2'].map(id => ({ id, source: { original_name: id === 'a1' ? 'chosen-audio.wav' : 'second.wav', language: 'ja', size_bytes: 5 } })) }); await settle()
    const store = useWorkflowStore(); store.capabilities!.schemaVersion = '1.14'; store.run = runBody({ status: 'completed' }); await settle(); return store
  }
  async function openRerun() {
    node('audio_output').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 400, clientY: 220 })); await settle()
    root!.querySelector<HTMLButtonElement>('[data-rerun-node]')!.click(); await settle()
  }
  it('plans the chosen historical source and retries submission with the exact request ID after a lost response', async () => {
    const submissions: any[] = []; let lose = true
    const store = await ready((path, init) => {
      if (path.endsWith('/workflow-runs/plan')) return Promise.resolve(response(validPlan))
      if (path.endsWith('/workflow-runs') && init?.method === 'POST') {
        submissions.push(JSON.parse(String(init.body)))
        if (lose) { lose = false; return Promise.reject(new TypeError('lost response')) }
        return Promise.resolve(response(runBody({ id: 'rerun-2', status: 'pending', rerun: { source_run_id: 'run-1', from_node_id: 'output', ...validPlan } }), 202))
      }
    })
    await openRerun()
    expect(root!.querySelector<HTMLButtonElement>('[data-editor-menu]')!.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('[data-open-panel=run]')!.disabled).toBe(true)
    expect(root!.querySelector('[data-rerun-audio]')!.textContent).toBe('chosen-audio.wav')
    const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith('/workflow-runs/plan'))!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ workflow_revision: 1, rerun: { source_run_id: 'run-1', from_node_id: 'output' } })
    root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.click(); await settle()
    expect(root!.querySelector('.node-rerun-dialog')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('.node-rerun-dialog .canvas-dialog-close')!.click(); await settle()
    await openRerun()
    root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.click(); await settle()
    expect(submissions).toHaveLength(2); expect(submissions[1]).toEqual(submissions[0]); expect(submissions[0].request_id).toBeTruthy()
    expect(store.run!.id).toBe('rerun-2'); expect(root!.querySelector('.node-rerun-dialog [role=dialog]')).toBeNull()
  })
  it('reruns one selected historical audio from a multi-audio input and renders reused/skipped records', async () => {
    const store = await ready(path => path.endsWith('/workflow-runs/plan') ? Promise.resolve(response(validPlan)) : undefined)
    const source = store.capabilities!.nodes.find(node => node.type === 'audio_input')! as any
    source.fields = [{ key: 'many', type: 'json', default: null, inputPort: null, required: false, minimum: null, maximum: null, minLength: null, maxLength: null, pattern: null, validator: null, options: [], label: 'Audio list', description: '' }]
    source.resourceInput = { resourceType: 'audio', configKey: 'single', collectionKey: 'many' }
    const graph = cloneDocument(store.draft); graph.nodes[0]!.config = { many: ['a1', 'a2'] }
    store.document = { ...store.document!, ...graph }; store.setCleanBaseline(graph)
    await openRerun(); expect(root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.disabled).toBe(false)
    root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.click(); await settle()
    const submits = fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/workflow-runs') && init?.method === 'POST')
    expect(submits).toHaveLength(1); expect(String(submits[0]![0])).toContain('/audios/a1/')
    store.run = runBody({ status: 'completed', nodes: runBody().nodes.map((node, index) => ({ ...node, status: index ? 'skipped' : 'completed', execution_kind: index ? null : 'reused', reused_from: index ? null : { run_id: 'old-run', node_id: node.node_id } })) })
    await openPanel('run'); expect(root!.textContent).toContain(t('workflow.rerunReused')); expect(root!.textContent).toContain(t('workflow.nodeStatus_skipped'))
  })

  it('reserves a separate header region for reused status without covering node titles', async () => {
    const store = await ready()
    store.run!.nodes[1]!.execution_kind = 'reused'; await settle()
    const group = node('audio_output')
    const title = group.querySelector('clipPath rect')!
    const badge = group.querySelector('[data-reuse-badge]')!
    const titleEnd = Number(title.getAttribute('x')) + Number(title.getAttribute('width'))
    expect(titleEnd).toBeLessThan(Number(badge.getAttribute('x')) - 56)
    expect(badge.getAttribute('text-anchor')).toBe('end')
  })

  it('blocks close, duplicate submission and workspace leave while a rerun is being submitted', async () => {
    const pending = deferred<Response>()
    await ready((path, init) => path.endsWith('/workflow-runs/plan') ? Promise.resolve(response(validPlan)) : path.endsWith('/workflow-runs') && init?.method === 'POST' ? pending.promise : undefined)
    await openRerun()
    const submit = root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!
    submit.click(); await settle(); submit.click()
    expect(submit.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('.node-rerun-dialog .canvas-dialog-close')!.disabled).toBe(true)
    const leave = vi.fn(); expect(app!._instance!.exposed!.requestClose(leave)).toBe('blocked'); expect(leave).not.toHaveBeenCalled()
    expect(fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/workflow-runs') && init?.method === 'POST')).toHaveLength(1)
    pending.resolve(response(runBody({ id: 'created-rerun' }), 202)); await settle()
  })

  it('shows unavailable checkpoints, allows locating an earlier node and never silently runs it', async () => {
    await ready((path) => path.endsWith('/workflow-runs/plan') ? Promise.resolve(response({ ...validPlan, valid: false, issues: [{ code: 'RERUN_CHECKPOINT_UNAVAILABLE', message: 'missing', node_id: 'input', restart_node_ids: ['input'] }] })) : undefined)
    await openRerun(); expect(root!.textContent).toContain(t('workflow.error_RERUN_CHECKPOINT_UNAVAILABLE'))
    expect(root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.disabled).toBe(true)
    const locate = [...root!.querySelectorAll<HTMLButtonElement>('.node-rerun-dialog button')].find(button => button.textContent === t('workflow.rerunLocate', { node: 'input' }))!
    locate.click(); await settle(); expect(useWorkflowStore().selectedNodeId).toBe('input')
    expect(fetchMock.mock.calls.filter(([path, init]) => String(path).endsWith('/workflow-runs') && init?.method === 'POST')).toHaveLength(0)
  })
  it('allows closing a pending plan and ignores its late response, and rejects dirty graphs visibly', async () => {
    const pending = deferred<Response>()
    const store = await ready(path => path.endsWith('/workflow-runs/plan') ? pending.promise : undefined)
    await openRerun(); root!.querySelector<HTMLButtonElement>('.node-rerun-dialog .canvas-dialog-close')!.click(); await settle()
    pending.resolve(response(validPlan)); await settle(); expect(root!.querySelector('.node-rerun-dialog [role=dialog]')).toBeNull()
    store.updateNodeConfig('output', { filename_stem: 'changed' }); await openRerun()
    expect(root!.querySelector('.node-rerun-dialog')!.textContent).toContain(t('workflow.saveBeforeRun'))
    expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/workflow-runs/plan'))).toHaveLength(1)
  })
})


describe('declarative debug node visibility', () => {
  afterEach(() => { setDebugMode(false); window.localStorage.removeItem(DEBUG_MODE_STORAGE_KEY) })
  it('hides debug declarations in both add menus while preserving and duplicating existing debug nodes', async () => {
    setup(); await settle(); const store = useWorkflowStore()
    store.addNode('asr'); const existing = store.selectedNodeId!
    store.capabilities!.schemaVersion = '1.15'
    store.capabilities!.nodes.forEach(kind => { kind.debugOnly = kind.type === 'asr' || kind.type === 'preprocess' })
    const extension = JSON.parse(JSON.stringify(store.capabilities!.nodes.find(kind => kind.type === 'asr')!))
    extension.type = 'unusual_extension'; extension.debugOnly = true
    store.capabilities!.nodes = [...store.capabilities!.nodes, extension]
    await settle(); expect(debugMode.value).toBe(false)
    root!.querySelector<HTMLButtonElement>('[data-add-node]')!.click(); await settle()
    expect(root!.querySelector('[data-add-kind=asr]')).toBeNull(); expect(root!.querySelector('[data-add-kind=unusual_extension]')).toBeNull()
    expect(root!.querySelector('[data-add-kind=audio_output]')).not.toBeNull()
    setDebugMode(true); await settle()
    expect(root!.querySelector('[data-add-kind=asr]')).not.toBeNull(); expect(root!.querySelector('[data-add-kind=unusual_extension]')).not.toBeNull()
    expect(window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY)).toBe('true')
    root!.querySelector('.canvas-context-menu')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    setDebugMode(false); await settle()
    expect(store.draft.nodes.some(node => node.id === existing)).toBe(true)
    expect(root!.querySelector(`[data-node-id="${existing}"]`)).not.toBeNull()
    store.selectNode(existing)
    root!.querySelector('.workflow-svg')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'd', ctrlKey: true, bubbles: true, cancelable: true })); await settle()
    expect(store.draft.nodes.filter(node => node.type === 'asr')).toHaveLength(2)
    root!.querySelector('.workflow-svg')!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 300, clientY: 250 })); await settle()
    const search = root!.querySelector<HTMLInputElement>('.canvas-node-search input')!; search.value = 'asr'; search.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(root!.querySelector('[data-add-kind=asr]')).toBeNull()
    root!.querySelector('.canvas-context-menu')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    node('asr').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })); await settle()
    expect(root!.querySelector('[data-rerun-node]')).not.toBeNull()
  })
})
