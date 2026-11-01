import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, reactive, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowEditor from './components/WorkflowEditor.vue'
import {
  WORKFLOW_NODE_HEIGHT, WORKFLOW_NODE_WIDTH, WORKFLOW_NODE_PORT_Y, workflowNodeHeight,
  cloneDocument, createDefaultWorkflow, executionSignature, nextNodePosition,
  resolveArtifactUrl, useWorkflowStore, validateNodeConfig,
  type WorkflowDocument, type WorkflowDocumentInput, type WorkflowNode, type WorkflowRun, type WorkflowRunSummary,
} from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry, type NodeRegistry } from './workflow/nodeRegistry'
import { setLocale, message, t } from './i18n'
import { workflowErrorLabel, workflowIssueLabel } from './stores/workflow'

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
    expect(node('audio_output')?.textContent).toContain('Filename')
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
    expect(firstPre).toEqual({ x: 286, y: 264 })
    const secondPre = nextNodePosition('preprocess', [input, output, { ...preprocess, position: firstPre }], testRegistry)
    expect(secondPre).toEqual({ x: 286, y: 408 })
    expect(nextNodePosition('audio_output', [input, output, { ...preprocess, position: firstPre }], testRegistry)).toEqual({ x: 420, y: 408 })
    expect(nextNodePosition('audio_output', [{ ...input, position: { x: 80, y: 120 } }], testRegistry)).toEqual({ x: 500, y: 120 })
    const all = [input, output, { ...preprocess, position: firstPre }, { ...preprocess, id: 'preprocess-2', position: secondPre }]
    for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) {
      const overlapX = Math.min(all[i].position.x + WORKFLOW_NODE_WIDTH, all[j].position.x + WORKFLOW_NODE_WIDTH) - Math.max(all[i].position.x, all[j].position.x)
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
    expect(document.activeElement).toBe(root!.querySelector('.canvas-context-menu'))
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
    expect(connect).toHaveBeenLastCalledWith(first.id, second.id)
    pointer(socket('audio_input', 'output'), 'pointerdown')
    pointer(socket('preprocess', 'input', 0), 'pointerup')
    expect(connect).toHaveBeenLastCalledWith('input', first.id)
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
        expect(connect).toHaveBeenLastCalledWith(store.draft.nodes.find((entry) => entry.type === 'preprocess')!.id, 'output')
      }
      // Forward completion must also treat an input with the CSS class
      // node-port-out as an input; only its explicit direction is authoritative.
      const calls = connect.mock.calls.length
      hit.mockReturnValue(start)
      pointer(destination, 'pointerdown', 600, 200, 7, 'touch')
      pointer(destination, 'pointerup', 100, 100, 7, 'touch')
      expect(connect).toHaveBeenCalledTimes(calls + 1)
      expect(connect).toHaveBeenLastCalledWith(store.draft.nodes.find((entry) => entry.type === 'preprocess')!.id, 'output')
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
    expect(transforms).toEqual(['translate(420 120)', 'translate(420 264)', 'translate(420 408)'])
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
