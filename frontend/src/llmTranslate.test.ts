import { parseApiError, type ApiError } from './api'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import WorkflowEditor from './components/WorkflowEditor.vue'
import GlobalSettingsDialog from './components/GlobalSettingsDialog.vue'
import { useWorkflowStore, workflowErrorLabel, type WorkflowRun } from './stores/workflow'
import { workflowCredentials } from './composables/workflowCredentials'
import { settingsCache } from './composables/globalSettings'
import { copySelection, preparePaste } from './workflow/editorTransfer'
import { readCapabilities } from './workflow/capabilities'
import { setLocale, t } from './i18n'

function catalog() {
  const field = (key: string, value: unknown, type = 'string', extra: object = {}) => ({ key, label: key, type, default: value, required: false, input_port: null, suggestion_source: null, ...extra })
  const language = (key: string) => field(key, '', 'string', { max_length: 32, input_port: key, suggestion_source: 'languages' })
  const fields = [field('base_url', '', 'string', { max_length: 2048 }), field('timeout_seconds', 120, 'integer', { minimum: 1, maximum: 3600 }), field('model', '', 'string', { input_port: 'model', max_length: 128 }), language('source_language'), language('target_language'), field('instructions', '', 'string', { max_length: 8192 }), field('glossary', {}, 'json'), field('batch_size', 20, 'integer', { minimum: 1, maximum: 100 }), field('max_batch_characters', 6000, 'integer', { minimum: 256, maximum: 30000 }), field('context_segments', 2, 'integer', { minimum: 0, maximum: 10 }), field('max_attempts', 2, 'integer', { minimum: 1, maximum: 5 }), field('additional_parameters', {}, 'json')]
  const node = (type: string, fields: any[] = [], input_ports: string[] = [], output_ports: string[] = [], extra: object = {}) => ({ type, label: type, debug_only: false, category: null, input_ports, output_ports, required_input_ports: input_ports, required_inbound: input_ports.length, is_parameter_source: false, terminal: false, config: { kind: type, fields }, ...extra })
  const input = node('audio_input', [], [], ['audio'], { is_source: true, removable: false, max_instances: 1 })
  const output = node('audio_output', [], ['audio'], [], { terminal: true })
  const asr = node('asr', [field('language', 'ja')], ['audio'], ['transcript'], { debug_only: true })
  const llm = node('llm_translate', [...fields, field('credential_ref', null)], ['transcript', 'model', 'source_language', 'target_language'], ['transcript'], { required_input_ports: ['transcript'], required_inbound: 1, category: { id: 'translation', label: '翻译' }, presentation: { inline_connection_input: { provider_type: 'llm_translate', label: 'LLM翻译', base_url_key: 'base_url', timeout_key: 'timeout_seconds', region_key: null, credential_ref_key: 'credential_ref' } } })
  const preview = node('text_preview', [], ['transcript'], [], { terminal: true, presentation: { preview: { renderer: 'text' } } })
  const text = node('text_output', [field('filename_stem', 'translation'), field('format', 'txt', 'select', { options: [{ value: 'txt', label: 'TXT' }] })], ['transcript'], [], { terminal: true })
  const graph = { nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 0, y: 0 } }, { id: 'output', type: 'audio_output', config: {}, position: { x: 400, y: 0 } }], edges: [{ id: 'direct', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }] }
  const capabilities = { schema_version: '1.0', languages: [{ code: 'ja', names: { 'zh-CN': '日语', en: 'Japanese' }, native_name: '日本語', aliases: ['Nihongo'] }, { code: 'en', names: { 'zh-CN': '英语', en: 'English' }, native_name: 'English', aliases: [] }], workflow: { schema_version: '1.15', nodes: [input, output, asr, llm, preview, text], ports: ['audio', 'transcript', 'model', 'source_language', 'target_language'].map(id => ({ id, label: id, type_id: ['audio', 'transcript'].includes(id) ? id : 'string' })), data_types: ['audio', 'transcript', 'string'].map(id => ({ id, label: id, parent_type_id: null, capabilities: [] })), constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 }, default_graph: graph }, asr: { providers: [] } }
  const defaults = Object.fromEntries(fields.map(field => [field.key, field.default]))
  const settings = { schema_version: '1.0', revision: 1, global_fields: [], global: {}, node_defaults: { llm_translate: { label: 'LLM翻译', fields, config: { ...defaults, base_url: 'https://llm.test/v1', model: 'future-deployment/model-2028', target_language: 'en' }, credential_configured: true, credential_version: 'frozen-global-key' } } }
  return { capabilities, settings, graph }
}
const response = (value: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(value) }) as Response
let app: App | undefined; let root: HTMLDivElement | undefined
async function settle() { for (let count = 0; count < 45; count++) await Promise.resolve(); await nextTick() }
function mount(component: any, props: Record<string, unknown>, pinia = createPinia()) { setActivePinia(pinia); root = document.createElement('div'); document.body.append(root); app = createApp(component, props).use(pinia); app.mount(root) }
function edit(input: HTMLInputElement, value: string) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })) }
function setup() {
  const fixture = catalog(); let document = { ...fixture.graph, schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '' }; let runCount = 0
  const state = { failure: false }; const saves: any[] = []
  const artifact = (id: string) => ({ artifact_id: 'a', port: 'text', filename: 'translation.txt', media_type: 'text/plain', size_bytes: 12, sha256: 'abc', download_url: `/api/v1/projects/p1/audios/a1/workflow-runs/result/outputs/${id}/download` })
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/capabilities')) return response(fixture.capabilities)
    if (path.endsWith('/settings')) return response(fixture.settings)
    if (path.endsWith('/workflow')) {
      if (init?.method === 'PUT') { const body = JSON.parse(String(init.body)); saves.push(body); for (const update of body.credential_updates || []) body.nodes.find((node: any) => node.id === update.node_id).config.credential_ref = `independent-${update.node_id}`; document = { ...document, nodes: body.nodes, edges: body.edges, revision: document.revision + 1, workflow_revision: document.workflow_revision + 1 } }
      return response(document)
    }
    if (path.endsWith('/workflow-runs') && init?.method === 'POST') {
      const run: WorkflowRun = { id: `result-${++runCount}`, project_id: 'p1', audio_id: 'a1', workflow_revision: document.workflow_revision, status: state.failure ? 'failed' : 'completed', created_at: '', started_at: null, finished_at: null, cancel_requested: false, error: state.failure ? { code: 'LLM_TRANSLATION_OUTPUT_INVALID', message: 'safe fallback' } : null, workflow: structuredClone(document), nodes: document.nodes.map(node => ({ node_id: node.id, node_type: node.type, config: node.config, status: state.failure && node.type === 'llm_translate' ? 'failed' : 'completed', started_at: null, finished_at: null, error: null, artifact: node.type === 'text_output' ? artifact(node.id) : null, ...(node.type === 'text_preview' ? { preview: { renderer: 'text', content_url: '/api/v1/translation-current/preview/content', media_type: 'text/plain' } } : {}) })) }
      return response(run)
    }
    if (path.endsWith('/preview/content')) return new Response('Bonjour après la deuxième traduction.\n<script>translated safely</script>')
    if (path.includes('/outputs/')) return response(artifact(path.split('/').pop()!))
    return response({ items: [], total: 0, limit: 30, offset: 0 })
  })
  vi.stubGlobal('fetch', fetcher); vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn())
  mount(WorkflowEditor, { projectId: 'p1', audioId: 'a1', audioName: 'voice.wav' })
  return { fixture, fetcher, saves, store: useWorkflowStore(), state }
}
afterEach(() => { app?.unmount(); root?.remove(); app = undefined; root = undefined; settingsCache.value = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale('zh-CN') })

describe('catalog-driven API translation', () => {
  it('adds translation in its declared category, searches both language fields and preserves free model names in both locales', async () => {
    const { store, fetcher } = setup(); await settle()
    root!.querySelector<HTMLButtonElement>('[data-add-node]')!.click(); await settle()
    expect(root!.querySelector('[data-node-category=translation] h3')!.textContent).toBe('翻译')
    root!.querySelector<HTMLButtonElement>('[data-add-kind=llm_translate]')!.click(); await settle()
    const node = root!.querySelector<SVGGElement>('.is-llm_translate')!
    expect(node.querySelectorAll('[data-node-connection] [data-connection-field]')).toHaveLength(2)
    expect(store.selectedNode!.config.model).toBe('future-deployment/model-2028')
    expect(node.querySelector('[data-field=instructions]')!.tagName).toBe('INPUT')
    const source = node.querySelector<HTMLInputElement>('[data-field=source_language]')!; source.focus(); edit(source, 'Nihongo'); await settle()
    document.querySelector<HTMLElement>('[data-language-code=ja]')!.click(); await settle(); expect(store.selectedNode!.config.source_language).toBe('ja')
    const target = node.querySelector<HTMLInputElement>('[data-field=target_language]')!; target.focus(); edit(target, 'xx-CUSTOM'); await settle(); target.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); await settle(); expect(store.selectedNode!.config.target_language).toBe('xx-CUSTOM')
    setLocale('en'); await settle(); expect(node.querySelector('.node-name')!.textContent).toContain('LLM translation')
    expect(node.querySelector<HTMLInputElement>('[data-field=target_language]')!.getAttribute('aria-label')).toBe('Target language')
    expect(fetcher.mock.calls.every(([url]) => !String(url).startsWith('https://llm.test'))).toBe(true)
  })
  it('copies global key versions into independent nodes and strips credentials from copied translation nodes', async () => {
    const { store, saves } = setup(); await settle(); store.addNode('llm_translate'); const first = store.selectedNodeId!; store.addNode('llm_translate'); const second = store.selectedNodeId!; await settle()
    const credentials = workflowCredentials(store.credentialContext)
    expect(credentials.updates()).toEqual([first, second].map(node_id => ({ node_id, operation: 'use_default', default_credential_version: 'frozen-global-key' })))
    const firstView = root!.querySelector(`[data-node-id="${first}"]`)!
    edit(firstView.querySelector<HTMLInputElement>('[data-credential-input]')!, 'first-node-key'); await store.save(); await settle()
    expect(saves[0].credential_updates).toEqual([{ node_id: first, operation: 'set', api_key: 'first-node-key' }, { node_id: second, operation: 'use_default', default_credential_version: 'frozen-global-key' }])
    expect(store.draft.nodes.find(node => node.id === first)!.config.credential_ref).not.toBe(store.draft.nodes.find(node => node.id === second)!.config.credential_ref)
    const copied = copySelection(store.draft, [first, second], store.registry!)
    expect(copied).not.toContain('credential_ref'); expect(copied).not.toContain('first-node-key'); expect(copied).not.toContain('frozen-global-key')
    store.applyPastedGraph(preparePaste(copied, store.draft, store.registry!))
    expect(credentials.updates()).toEqual([])
    expect(store.selectedNodeIds.every(id => !store.draft.nodes.find(node => node.id === id)!.config.credential_ref)).toBe(true)
  })
  it('chains two translators through transcript ports and displays/downloads the backend current text without original-text controls', async () => {
    const { store, fetcher, state } = setup(); await settle(); store.removeNode('output')
    const add = (type: string) => { store.addNode(type); return store.selectedNodeId! }
    const asr = add('asr'), first = add('llm_translate'), second = add('llm_translate'), preview = add('text_preview'), output = add('text_output')
    expect(store.connect('input', asr, 'audio', 'audio')).toBe(true)
    for (const [from, to] of [[asr, first], [first, second], [second, preview], [second, output]]) expect(store.connect(from!, to!, 'transcript', 'transcript')).toBe(true)
    await store.save(); await store.startRun(); await settle()
    const previewView = root!.querySelector(`[data-node-id="${preview}"] .node-preview`)!
    expect(previewView.querySelector('pre')!.textContent).toBe('Bonjour après la deuxième traduction.\n<script>translated safely</script>')
    expect(previewView.querySelector('script, select, button')).toBeNull()
    const clicked: string[] = []; vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.href) })
    store.downloadArtifact(output); expect(clicked[0]).toContain(`/outputs/${output}/download`)
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/workflow-runs'))[0]![1]!.body).toBe(JSON.stringify({ workflow_revision: store.document!.workflow_revision }))
    state.failure = true; await store.startRun(); await settle(); root!.querySelector<HTMLButtonElement>('[data-open-panel=run]')!.click(); await settle()
    expect(root!.textContent).toContain(t('workflow.error_LLM_TRANSLATION_OUTPUT_INVALID'))
    setLocale('en'); await settle(); expect(root!.textContent).toContain('The translated output does not match the input segments.')
  })
  it('shows translation defaults from the same directory and preserves the private default key while changing interface language', async () => {
    const fixture = catalog(); const bodies: any[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => { if (init?.method === 'PUT') bodies.push(JSON.parse(String(init.body))); return response(fixture.settings) }))
    mount(GlobalSettingsDialog, { open: true, capabilities: readCapabilities(fixture.capabilities), languages: readCapabilities(fixture.capabilities).languages, credentialTypes: ['llm_translate'] }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=translation]')!.click(); await settle()
    expect(root!.querySelector<HTMLSelectElement>('[data-settings-node-type]')!.value).toBe('llm_translate')
    edit(root!.querySelector<HTMLInputElement>('[data-field=instructions]')!, 'Keep names unchanged.'); edit(root!.querySelector<HTMLInputElement>('[data-settings-key]')!, 'private-template-key')
    setLocale('en'); await settle(); expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('private-template-key')
    root!.querySelector<HTMLButtonElement>('[data-settings-actions] .primary-button')!.click(); await settle()
    expect(bodies[0].node_defaults.llm_translate.instructions).toBe('Keep names unchanged.')
    expect(bodies[0].credential_updates).toEqual([{ node_type: 'llm_translate', operation: 'set', api_key: 'private-template-key' }])
    expect(bodies[0].node_defaults.llm_translate).not.toHaveProperty('credential_ref')
  })
  it.each(['CURRENT_TEXT_INVALID', 'CURRENT_TEXT_MISSING', 'LLM_BASE_URL_REQUIRED', 'LLM_MODEL_REQUIRED', 'LLM_TARGET_LANGUAGE_REQUIRED', 'LLM_SOURCE_LANGUAGE_REQUIRED', 'LLM_TRANSLATION_CREDENTIALS_MISSING', 'LLM_TRANSLATION_HTTP_ERROR', 'LLM_TRANSLATION_OUTPUT_INVALID', 'LLM_TRANSLATION_REFUSED', 'LLM_TRANSLATION_TIMEOUT'])('localizes safe translation error %s in both interfaces', code => {
    for (const locale of ['zh-CN', 'en']) { setLocale(locale); expect(workflowErrorLabel({ code, message: 'server fallback' })).toBe(t(`workflow.error_${code}`)); expect(workflowErrorLabel({ code, message: 'server fallback' })).not.toBe('server fallback') }
  })
})


it('localizes a translation submission rejection without losing the stable API error code', async () => {
  setLocale('en')
  const error = await parseApiError({ status: 422, json: async () => ({ error: { code: 'LLM_MODEL_REQUIRED', message: '请填写翻译模型' } }) } as Response) as ApiError
  expect(error.message).toBe(t('workflow.error_LLM_MODEL_REQUIRED'))
  expect(error.code).toBe('LLM_MODEL_REQUIRED'); expect(error.status).toBe(422)
})
