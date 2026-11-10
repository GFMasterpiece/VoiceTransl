import { runFixture } from './workflow/fixtures'
import modularCatalog from './workflow/modularCapabilities.fixture.json'
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
  const capabilities = structuredClone(modularCatalog)
  const node_defaults = Object.fromEntries(capabilities.workflow.nodes.filter(node => node.evaluation_kind === 'configuration' && node.config.fields.length).map(kind => {
    const fields = kind.config.fields.filter(field => field.key !== kind.presentation?.inline_connection_input?.credential_ref_key)
    const config: Record<string, unknown> = Object.fromEntries(fields.map(field => [field.key, field.type === 'json' && field.default === null ? {} : field.default]))
    if (kind.type === 'service_connection') config.endpoint = 'https://llm.test/v1'
    if (kind.type === 'model_config') config.model_name = 'future-deployment/model-2028'
    return [kind.type, { label: kind.label, fields, config, credential_configured: kind.type === 'service_connection', credential_version: kind.type === 'service_connection' ? 'frozen-global-key' : null }]
  }))
  const settings = { schema_version: '3.0', revision: 1, global_fields: [], global: {}, node_defaults }
  return { capabilities, settings, graph: capabilities.workflow.default_graph }
}
const response = (value: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(value) }) as Response
let app: App | undefined; let root: HTMLDivElement | undefined
async function settle() { for (let count = 0; count < 45; count++) await Promise.resolve(); await nextTick() }
function mount(component: any, props: Record<string, unknown>, pinia = createPinia()) { setActivePinia(pinia); root = document.createElement('div'); document.body.append(root); app = createApp(component, props).use(pinia); app.mount(root) }
function edit(input: HTMLInputElement, value: string) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })) }
function setup() {
  const fixture = catalog(); let document = { ...fixture.graph, schema_version: "3.0", project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '' }; let runCount = 0
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
      const run: WorkflowRun = runFixture({ id: `result-${++runCount}`, project_id: 'p1', audio_id: 'a1', workflow_revision: document.workflow_revision, status: state.failure ? 'failed' : 'completed', created_at: '', started_at: null, finished_at: null, cancel_requested: false, error: state.failure ? { code: 'LLM_TRANSLATION_OUTPUT_INVALID', message: 'safe fallback' } : null, workflow: structuredClone(document), nodes: document.nodes.map(node => ({ node_id: node.id, node_type: node.type, config: node.config, status: state.failure && node.type === 'llm_translate' ? 'failed' : 'completed', started_at: null, finished_at: null, error: null, artifact: node.type === 'text_output' ? artifact(node.id) : null, ...(node.type === 'text_preview' ? { preview: { renderer: 'text', content_url: '/api/v1/translation-current/preview/content', media_type: 'text/plain' } } : {}) })) })
      return response(run)
    }
    if (path.endsWith('/preview/content')) return new Response('Bonjour après la deuxième traduction.\n<script>translated safely</script>')
    if (path.includes('/outputs/')) return response(artifact(path.split('/').pop()!))
    return response({ items: [], total: 0, limit: 30, offset: 0 })
  })
  vi.stubGlobal('fetch', fetcher); vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1)); vi.stubGlobal('cancelAnimationFrame', vi.fn())
  mount(WorkflowEditor, { projectId: 'p1', audioId: 'a1', audioName: 'voice.wav', audios: [{ id: 'a1', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 12 } }] })
  return { fixture, fetcher, saves, store: useWorkflowStore(), state }
}
afterEach(() => { app?.unmount(); root?.remove(); app = undefined; root = undefined; settingsCache.value = null; vi.restoreAllMocks(); vi.unstubAllGlobals(); setLocale('zh-CN') })

describe('catalog-driven API translation', () => {
  it('uses ordinary string context and keeps processors free of builder fields in both locales', async () => {
    const { store, fetcher } = setup(); await settle(); store.addNode('llm_translate'); await settle()
    const processor = root!.querySelector(`[data-node-id="${store.selectedNodeId}"]`)!
    expect(store.selectedNode!.config).toEqual({})
    expect(processor.querySelector('foreignObject, .node-meta, [data-credential-input]')).toBeNull()
    expect(processor.querySelectorAll('.node-port-in')).toHaveLength(4)
    store.addNode('model_config'); expect(store.selectedNode!.config.model_name).toBe('future-deployment/model-2028')
    store.addNode('string_value'); await settle()
    const node = root!.querySelector(`[data-node-id="${store.selectedNodeId}"]`)!
    const value = node.querySelector<HTMLTextAreaElement>('[data-field=value]')!
    edit(value as unknown as HTMLInputElement, 'Translate into English. Keep names unchanged.'); await settle()
    setLocale('en'); await settle(); expect(value.value).toBe('Translate into English. Keep names unchanged.')
    expect(store.registry!.has('translation_context')).toBe(false)
    expect(store.registry!.has('asr_context')).toBe(false)
    expect(fetcher.mock.calls.every(([url]) => !String(url).startsWith('https://llm.test'))).toBe(true)
  })
  it('copies global key versions into independent connection nodes and strips credentials from copied connections', async () => {
    const { store, saves } = setup(); await settle(); store.addNode('service_connection'); const first = store.selectedNodeId!; store.addNode('service_connection'); const second = store.selectedNodeId!; await settle()
    const credentials = workflowCredentials(store.credentialContext)
    expect(credentials.updates()).toEqual([first, second].map(node_id => ({ node_id, operation: 'use_default', default_credential_version: 'frozen-global-key' })))
    const firstView = root!.querySelector(`[data-node-id="${first}"]`)!
    expect([...firstView.querySelectorAll('[data-field], [data-connection-field], [data-credential-input]')].map(input => input.getAttribute('data-field') || input.getAttribute('data-connection-field') || 'KEY')).toEqual(['protocol', 'endpoint', 'region', 'KEY'])
    expect(firstView.querySelector('[data-node-section=parameters]')).toBeNull()
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
    const connection = add('service_connection'), model = add('model_config'), context = add('string_value'), options = add('translation_options')
    expect(store.connect(connection, model, 'connection', 'connection')).toBe(true)
    for (const target of [first, second]) {
      expect(store.connect(model, target, 'model', 'model')).toBe(true)
      expect(store.connect(context, target, 'string', 'context')).toBe(true)
      expect(store.connect(options, target, 'translation_options', 'translation_options')).toBe(true)
    }
    expect(store.connect('input', asr, 'audio', 'audio')).toBe(true)
    for (const [from, to] of [[asr, first], [first, second], [second, preview], [second, output]]) expect(store.connect(from!, to!, 'transcript', 'transcript')).toBe(true)
    await store.save(); await store.startRun(); await settle()
    expect(store.run, store.error + JSON.stringify(store.validationIssues)).not.toBeNull()
    const previewView = root!.querySelector(`[data-node-id="${preview}"] .node-preview`)!
    expect(previewView.querySelector('pre')!.textContent).toBe('Bonjour après la deuxième traduction.\n<script>translated safely</script>')
    expect(previewView.querySelector('script, select, button')).toBeNull()
    const clicked: string[] = []; vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.href) })
    store.downloadArtifact(output); expect(clicked[0]).toContain(`/outputs/${output}/download`)
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/workflow-runs'))[0]![1]!.body).toBe(JSON.stringify({ workflow_revision: store.document!.workflow_revision }))
    const sourceRunId = store.run!.id
    const implementation = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (input, init) => String(input).endsWith('/plan') ? response({ valid: true, execute_node_ids: [second, preview, output], reuse_node_ids: [asr, first], skipped_node_ids: ['input'], issues: [] }) : implementation(input, init))
    root!.querySelector(`[data-node-id="${second}"]`)!.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 150, clientY: 150 })); await settle()
    root!.querySelector<HTMLButtonElement>('[data-rerun-node]')!.click(); await settle()
    expect(root!.querySelector('[data-rerun-audio]')!.textContent).toBe('voice.wav')
    expect(root!.querySelector('[data-rerun-submit]')).not.toBeNull()
    const planRequest = fetcher.mock.calls.find(([url]) => String(url).endsWith('/plan'))!
    expect(JSON.parse(String(planRequest[1]!.body))).toEqual({ workflow_revision: store.document!.workflow_revision, rerun: { source_run_id: sourceRunId, from_node_id: second } })
    root!.querySelector<HTMLButtonElement>('[data-rerun-submit]')!.click(); await settle()
    const rerunRequests = fetcher.mock.calls.filter(([url, init]) => String(url).endsWith('/workflow-runs') && init?.method === 'POST')
    const rerunRequest = JSON.parse(String(rerunRequests[rerunRequests.length - 1]![1]!.body))
    expect(rerunRequest).toEqual({ workflow_revision: store.document!.workflow_revision, request_id: expect.any(String), rerun: { source_run_id: sourceRunId, from_node_id: second } })
    expect(workflowCredentials(store.credentialContext).updates()).toEqual([])
    state.failure = true; await store.startRun(); await settle(); root!.querySelector<HTMLButtonElement>('[data-open-panel=run]')!.click(); await settle()
    expect(root!.textContent).toContain(t('workflow.error_LLM_TRANSLATION_OUTPUT_INVALID'))
    setLocale('en'); await settle(); expect(root!.textContent).toContain('The translated output does not match the input segments, or its output language is invalid.')
  })
  it('edits ordered dynamic inputs, preserves fallbacks, and restores deleted connections with undo', async () => {
    const { store } = setup(); await settle()
    const add = (type: string) => { store.addNode(type); return store.selectedNodeId! }
    const first = add('string_value'), join = add('text_join'), object = add('json_value'), merge = add('json_merge'), model = add('model_config')
    store.updateNodeConfig(join, { items: [{ id: 'a', value: 'first' }, { id: 'b', value: 'fallback' }, { id: 'c', value: 'last' }], separator: '\n' })
    expect(store.connect(first, join, 'string', 'text_b')).toBe(true)
    expect(store.connect(object, merge, 'json', 'json_left')).toBe(true)
    expect(store.connect(object, merge, 'json', 'json_right')).toBe(true)
    expect(store.connect(merge, model, 'json', 'additional_parameters')).toBe(true)
    await settle()
    const node = root!.querySelector(`[data-node-id="${join}"]`)!
    const bound = node.querySelector<HTMLTextAreaElement>('[data-field="items.b.value"]')!
    expect(bound.disabled).toBe(true); expect(bound.closest('label')!.style.display).toBe('none')
    const buttons = node.querySelectorAll<HTMLButtonElement>('[data-slot=b] button')
    expect([...buttons].every(button => button.classList.contains('text-button'))).toBe(true)
    expect(node.querySelector('[data-add-input]')!.classList.contains('secondary-button')).toBe(true)
    const manual = node.querySelector<HTMLTextAreaElement>('[data-field="items.a.value"]')!
    edit(manual as unknown as HTMLInputElement, 'edited first'); await settle()
    buttons[0]!.click(); await settle()
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[]).map(slot => slot.id)).toEqual(['b', 'a', 'c'])
    expect(store.draft.edges.some(edge => edge.target === join && edge.target_port === 'text_b')).toBe(true)
    store.undo(); await settle()
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[]).map(slot => slot.id)).toEqual(['a', 'b', 'c'])
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[])[0].value).toBe('edited first')
    store.undo(); await settle()
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[])[0].value).toBe('first')
    store.redo(); store.redo(); await settle()
    buttons[2]!.click(); await settle()
    expect(store.draft.edges.some(edge => edge.target === join)).toBe(false)
    store.undo(); await settle()
    expect(store.draft.edges.some(edge => edge.target === join && edge.target_port === 'text_b')).toBe(true)
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[])[0]).toEqual({ id: 'b', value: 'fallback' })
    const edge = store.draft.edges.find(edge => edge.target === join)!
    store.disconnect(edge.id); await settle()
    const restored = node.querySelector<HTMLTextAreaElement>('[data-field="items.b.value"]')!
    expect(restored.disabled).toBe(false); expect(restored.value).toBe('fallback')
    node.querySelector<HTMLButtonElement>('[data-add-input]')!.click(); await settle()
    expect((store.draft.nodes.find(node => node.id === join)!.config.items as any[])).toHaveLength(4)
    const copied = preparePaste(copySelection(store.draft, [join], store.registry!), store.draft, store.registry!, { x: 100, y: 100 })
    expect(copied.nodes[0]!.config.items).toEqual(store.draft.nodes.find(node => node.id === join)!.config.items)
    store.connect(first, join, 'string', 'text_b')
    const fragment = preparePaste(copySelection(store.draft, [first, join], store.registry!), store.draft, store.registry!, { x: 200, y: 200 })
    expect(fragment.edges).toHaveLength(1); expect(fragment.edges[0]!.target_port).toBe('text_b')
    expect(workflowCredentials(store.credentialContext).updates()).toEqual([])
  })
  it('shows translation defaults from the same directory and preserves the private default key while changing interface language', async () => {
    const fixture = catalog(); const bodies: any[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => { if (init?.method === 'PUT') bodies.push(JSON.parse(String(init.body))); return response(fixture.settings) }))
    mount(GlobalSettingsDialog, { open: true, capabilities: readCapabilities(fixture.capabilities), languages: readCapabilities(fixture.capabilities).languages, credentialTypes: ['service_connection'] }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=parameters]')!.click(); await settle()
    const type = root!.querySelector<HTMLSelectElement>('[data-settings-node-type]')!; type.value = 'service_connection'; type.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect([...root!.querySelectorAll('[data-settings-section=connection] [data-field], [data-settings-key]')].map(input => input.getAttribute('data-field') || 'KEY')).toEqual(['protocol', 'endpoint', 'region', 'KEY'])
    expect(root!.querySelector('[data-settings-section=parameters]')).toBeNull()
    edit(root!.querySelector<HTMLInputElement>('[data-settings-key]')!, 'private-template-key'); type.value = 'string_value'; type.dispatchEvent(new Event('change', { bubbles: true })); await settle(); edit(root!.querySelector<HTMLInputElement>('[data-field=value]')!, 'Keep names unchanged.'); type.value = 'service_connection'; type.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    setLocale('en'); await settle(); expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('private-template-key')
    root!.querySelector<HTMLButtonElement>('[data-settings-actions] .primary-button')!.click(); await settle()
    expect(bodies[0].node_defaults.string_value.value).toBe('Keep names unchanged.')
    expect(bodies[0].credential_updates).toEqual([{ node_type: 'service_connection', operation: 'set', api_key: 'private-template-key' }])
    expect(bodies[0].node_defaults.service_connection).not.toHaveProperty('credential_ref')
  })
  it.each(['CONNECTION_ENDPOINT_REQUIRED', 'MODEL_NAME_REQUIRED', 'MODEL_PROTOCOL_INCOMPATIBLE', 'CONNECTION_BINDING_INVALID', 'CONNECTION_BINDING_CHANGED', 'MODEL_BINDING_INVALID', 'CONFIGURATION_INVALID', 'CONFIGURATION_VALUE_INVALID', 'JSON_MERGE_CONFLICT', 'ASR_LANGUAGE_REQUIRED', 'LLM_CONTEXT_INVALID', 'QWEN_CONTEXT_INVALID', 'QWEN_CONTEXT_TOO_LONG', 'CURRENT_TEXT_INVALID', 'CURRENT_TEXT_MISSING', 'LLM_TRANSLATION_CREDENTIALS_MISSING', 'LLM_TRANSLATION_HTTP_ERROR', 'LLM_TRANSLATION_OUTPUT_INVALID', 'LLM_TRANSLATION_REFUSED', 'LLM_TRANSLATION_TIMEOUT'])('localizes safe translation error %s in both interfaces', code => {
    for (const locale of ['zh-CN', 'en']) { setLocale(locale); expect(workflowErrorLabel({ code, message: 'server fallback' })).toBe(t(`workflow.error_${code}`)); expect(workflowErrorLabel({ code, message: 'server fallback' })).not.toBe('server fallback') }
  })
})


it('localizes a translation submission rejection without losing the stable API error code', async () => {
  setLocale('en')
  const error = await parseApiError({ status: 422, json: async () => ({ error: { code: 'MODEL_NAME_REQUIRED', message: '请填写模型名称' } }) } as Response) as ApiError
  expect(error.message).toBe(t('workflow.error_MODEL_NAME_REQUIRED'))
  expect(error.code).toBe('MODEL_NAME_REQUIRED'); expect(error.status).toBe(422)
})

it('explains current context and output-language errors in both interface languages', () => {
  setLocale('zh-CN')
  expect(workflowErrorLabel({ code: 'QWEN_CONTEXT_TOO_LONG', message: 'fallback' })).toContain('400 个字符')
  expect(workflowErrorLabel({ code: 'LLM_CONTEXT_INVALID', message: 'fallback' })).toContain('非空')
  expect(workflowErrorLabel({ code: 'LLM_TRANSLATION_OUTPUT_INVALID', message: 'fallback' })).toContain('输出语言无效')
  setLocale('en')
  expect(workflowErrorLabel({ code: 'QWEN_CONTEXT_TOO_LONG', message: 'fallback' })).toContain('400 characters')
  expect(workflowErrorLabel({ code: 'LLM_CONTEXT_INVALID', message: 'fallback' })).toContain('nonempty')
  expect(workflowErrorLabel({ code: 'LLM_TRANSLATION_OUTPUT_INVALID', message: 'fallback' })).toContain('output language is invalid')
})
