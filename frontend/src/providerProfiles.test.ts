import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import { useWorkflowStore, workflowErrorLabel, type WorkflowRun } from './stores/workflow'
import { useProviderProfilesStore, type ProviderProfile } from './stores/providerProfiles'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry } from './workflow/nodeRegistry'
import { setLocale, t } from './i18n'
import { batchError } from './composables/useBatches'
import CloudWorkflowNotice from './components/CloudWorkflowNotice.vue'
import { workflowCredentials } from './composables/workflowCredentials'
import { copySelection, parseWorkflowFile, preparePaste, serializeWorkflow } from './workflow/editorTransfer'
import { connectionTarget } from './workflow/providerConnections'

const profile = (id = 'connection-1', extra: Partial<ProviderProfile> = {}): ProviderProfile => ({ schema_version: '1.0', id, display_name: id, provider_type: 'extension_service',
  base_url: 'https://old.service.test/api', api_key_env: 'EXAMPLE_KEY', region: 'region-test', default_timeout: 37, enabled: true, ...extra })
function catalog(profiles: ProviderProfile[] = [profile()]): any {
  const plain = (type: string, fields: any[] = []) => ({ type, label: type, input_ports: [], output_ports: [], required_input_ports: [], required_inbound: 0, is_source: false, is_parameter_source: false, terminal: false, config: { kind: type, fields } })
  const field = (key: string, type = 'string', value: unknown = '') => ({ key, label: key, type, default: value, required: false, input_port: null })
  const source = { ...plain('source_extension', [field('single', 'string', null), field('many', 'json', null)]), output_ports: ['audio'], is_source: true, max_instances: 1, removable: false,
    presentation: { resource_input: { resource_type: 'audio', config_key: 'single', collection_key: 'many' } } }
  const remote = { ...plain('remote_extension', [{ ...field('connection_ref', 'select'), options: profiles.filter((item) => item.enabled && item.provider_type === 'extension_service').map((item) => ({ value: item.id, label: item.display_name })) },
    field('model', 'string', 'model-not-in-a-list'), { ...field('language'), input_port: 'language' }, field('additional_parameters', 'json', {})]), input_ports: ['audio', 'language'], required_input_ports: ['audio'], required_inbound: 1, output_ports: ['transcript'],
    presentation: { provider_profile_input: { config_key: 'connection_ref', provider_type: 'extension_service', label: 'Extension cloud', defaults: { base_url: 'https://new.service.test/api', api_key_env: 'NEW_KEY', default_timeout: 37 },
      data_handling: { temporary_upload_retention_hours: 17, remote_cancel_pending_only: true } } } }
  const output = { ...plain('text_extension'), input_ports: ['transcript'], required_input_ports: ['transcript'], required_inbound: 1, terminal: true }
  const value = { ...plain('language_value', [field('value', 'string', 'custom-lang')]), output_ports: ['language'], is_parameter_source: true }
  return { schema_version: '1.0', workflow: { schema_version: '1.7', nodes: [source, remote, output, value],
    ports: [{ id: 'audio', type_id: 'audio', label: 'Audio' }, { id: 'transcript', type_id: 'transcript', label: 'Transcript' }, { id: 'language', type_id: 'string', label: 'Language' }],
    data_types: ['audio', 'transcript', 'string'].map((id) => ({ id, label: id, parent_type_id: null, capabilities: [] })), constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: { nodes: [{ id: 'input', type: 'source_extension', config: {}, position: { x: 0, y: 0 } }, { id: 'cloud', type: 'remote_extension', config: { connection_ref: 'connection-1', model: 'model-not-in-a-list', language: '', additional_parameters: {} }, position: { x: 450, y: 0 } },
      { id: 'output', type: 'text_extension', config: {}, position: { x: 900, y: 0 } }], edges: [{ id: 'first', source: 'input', source_port: 'audio', target: 'cloud', target_port: 'audio' }, { id: 'second', source: 'cloud', source_port: 'transcript', target: 'output', target_port: 'transcript' }] } }, asr: { providers: [] } }
}
function inlineCatalog(profiles: ProviderProfile[] = [profile()], legacy = false): any {
  const value = catalog(profiles); value.workflow.schema_version = '1.9'
  value.workflow.nodes.forEach((node: any) => { node.category = null })
  const node = value.workflow.nodes[1]
  const field = (key: string, type: string, value: unknown, extra: object = {}) => ({ key, label: key, type, default: value, required: false, input_port: null, ...extra })
  node.config.fields.push(field('mode', 'select', 'profile', { creation_default: 'inline', options: [{ value: 'profile', label: 'Legacy' }, { value: 'inline', label: 'Inline' }] }),
    field('endpoint', 'string', 'https://new.service.test/api'), field('timeout', 'integer', 37, { minimum: 1, maximum: 3600 }), field('region', 'string', null), field('env', 'string', 'DEFAULT_ENV'), field('credential_handle', 'string', null))
  node.presentation.inline_connection_input = { mode_key: 'mode', mode_value: 'inline', base_url_key: 'endpoint', timeout_key: 'timeout', region_key: 'region', api_key_env_key: 'env', credential_ref_key: 'credential_handle' }
  if (!legacy) Object.assign(value.workflow.default_graph.nodes[1].config, { mode: 'inline', endpoint: 'https://inline.service.test/api', timeout: 37, region: null, env: null, credential_handle: 'saved-ref' })
  return value
}
function streamingCatalog(profiles: ProviderProfile[] = [profile()]): any {
  const value = inlineCatalog(profiles); value.workflow.schema_version = '1.10'
  const old = value.workflow.nodes[1]
  old.category = { id: 'speech_recognition', label: '语音识别' }
  Object.assign(old.presentation.inline_connection_input, { provider_type: 'extension_service', label: 'File service', data_handling: old.presentation.provider_profile_input.data_handling })
  const stream = structuredClone(old)
  stream.type = 'qwen_asr_streaming'; stream.config.kind = 'qwen_asr_streaming'; stream.label = 'Qwen streaming'; stream.add_label = stream.label
  delete stream.presentation.provider_profile_input
  stream.presentation.inline_connection_input = { mode_key: null, mode_value: null, base_url_key: 'endpoint', timeout_key: 'timeout', region_key: null, api_key_env_key: null, credential_ref_key: 'credential_handle', provider_type: 'qwen_asr_streaming', label: 'Qwen Streaming ASR', data_handling: { temporary_upload_retention_hours: null, remote_cancel_pending_only: false, cancel_behavior: 'close_stream' } }
  stream.config.fields = stream.config.fields.filter((field: any) => !['connection_ref', 'mode', 'region', 'env'].includes(field.key))
  stream.config.fields.find((field: any) => field.key === 'model').default = 'qwen-audio-3.0-asr-flash-streaming'
  stream.config.fields.find((field: any) => field.key === 'endpoint').default = 'wss://stream.service.test/api-ws/v1/inference'
  value.workflow.nodes.push(stream)
  const cloud = value.workflow.default_graph.nodes[1]
  cloud.type = stream.type
  cloud.config = { endpoint: 'wss://stream.service.test/api-ws/v1/inference', timeout: 37, credential_handle: null, model: 'qwen-audio-3.0-asr-flash-streaming', language: '', additional_parameters: {} }
  return value
}
const response = (value: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(value) }) as Response
async function settle() { for (let index = 0; index < 40; index++) await Promise.resolve(); await nextTick() }
let app: VueApp | undefined
let root: HTMLDivElement | undefined
function setup(options: { profiles?: ProviderProfile[]; rejectSave?: boolean; failCatalog?: boolean; inline?: boolean; legacy?: boolean; streaming?: boolean } = {}) {
  const state = { profiles: options.profiles || [profile()], rejectSave: !!options.rejectSave, failCatalog: !!options.failCatalog, rejectWorkflow: false, loseSaveResponse: false }
  const readCatalog = () => options.streaming ? streamingCatalog(state.profiles) : options.inline ? inlineCatalog(state.profiles, options.legacy) : catalog(state.profiles)
  const initial = readCatalog()
  let document = { ...initial.workflow.default_graph, schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '2026-11-07T00:00:00Z' }
  const savedRequests = new Map<string, any>()
  const audio = { id: 'a1', source: { original_name: 'local.wav', language: 'ja', size_bytes: 4 } }
  const project = { id: 'p1', name: 'Profile test', source: audio.source, audios: [audio], created_at: '2026-11-07', updated_at: '2026-11-07' }
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/capabilities')) return state.failCatalog ? response({ detail: 'Catalog temporarily unavailable' }, 503) : response(readCatalog())
    if (path.endsWith('/provider-profiles') && !init?.method) return response({ items: state.profiles })
    if (path.includes('/provider-profiles') && init?.method) {
      if (state.rejectSave) return response({ error: { code: 'EXTENSION_PROFILE_ERROR', message: 'Original safe diagnostic' } }, 422)
      if (init.method === 'DELETE') { state.profiles = state.profiles.filter((item) => !path.endsWith(item.id)); return response(null, 204) }
      const saved = JSON.parse(init.body as string) as ProviderProfile
      state.profiles = [...state.profiles.filter((item) => item.id !== saved.id), saved]
      return response(saved, init.method === 'POST' ? 201 : 200)
    }
    if (path.includes('/projects?')) return response({ items: [project], total: 1, limit: 20, offset: 0 })
    if (path.endsWith('/projects/p1')) return response(project)
    if (path.endsWith('/projects/p1/audios')) return response({ items: [audio] })
    if (path.endsWith('/workflow')) {
      if (init?.method === 'PUT' && (options.inline || options.streaming)) {
        const body = JSON.parse(String(init.body))
        if (state.rejectWorkflow) return response({ error: { code: 'CREDENTIAL_STORAGE_ERROR', message: 'Safe storage error' } }, 503)
        if (body.request_id && savedRequests.has(body.request_id)) return response(savedRequests.get(body.request_id))
        const nodes = body.nodes
        for (const update of body.credential_updates || []) nodes.find((node: any) => node.id === update.node_id).config.credential_handle = update.operation === 'clear' ? null : `new-ref-${document.revision + 1}`
        document = { ...document, nodes, edges: body.edges, revision: document.revision + 1, workflow_revision: document.workflow_revision + 1 }
        if (body.request_id) savedRequests.set(body.request_id, structuredClone(document))
        if (state.loseSaveResponse) { state.loseSaveResponse = false; throw new TypeError('Failed to fetch') }
      }
      return response(document)
    }
    if (path.endsWith('/upload-policy')) return response({ allowed_extensions: ['wav'], max_upload_bytes: 100 })
    if (path.endsWith('/workflow/validate')) return response({ valid: true, errors: [] })
    if (path.includes('/workflow-runs?') || path.includes('/batches?')) return response({ items: [], total: 0, limit: 20, offset: 0 })
    return response({ items: [] })
  })
  vi.stubGlobal('fetch', fetcher)
  const pinia = createPinia(); setActivePinia(pinia)
  root = window.document.createElement('div'); window.document.body.append(root)
  app = createApp(App).use(pinia); app.mount(root)
  return { state, fetcher, workflow: useWorkflowStore(pinia), profiles: useProviderProfilesStore(pinia), document, pinia }
}
async function openProject() { await app!._instance!.exposed!.openProject('p1'); await settle() }
async function openManager() { root!.querySelector<HTMLButtonElement>('[data-open-profiles]')!.click(); await settle() }
async function fill(key: string, value: string) {
  const input = root!.querySelector<HTMLInputElement>(`[data-profile-field=${key}]`)!
  input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
}
afterEach(() => { app?.unmount(); root?.remove(); app = undefined; root = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('provider profile management', () => {
  it('waits for the authoritative initial list before writing and preserves old connections after create', async () => {
    const { fetcher, profiles } = setup(); await settle()
    const originalFetch = fetcher.getMockImplementation()!
    let release!: () => void
    const initialRead = new Promise<void>((resolve) => { release = resolve })
    fetcher.mockImplementation(async (input, init) => {
      if (String(input).endsWith('/provider-profiles') && !init?.method) await initialRead
      return originalFetch(input, init)
    })
    await openProject(); await openManager()
    expect(root!.querySelector('[data-provider-target="connection-1"]')!.textContent).toContain(t('profiles.loading'))
    expect(root!.querySelector('[data-provider-target="connection-1"]')!.textContent).not.toContain(t('profiles.missing'))
    expect(root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.disabled).toBe(true)
    expect(await profiles.save(profile('blocked'), true)).toBeNull()
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    release(); await settle()
    root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.click(); await settle(); await fill('display_name', 'New connection')
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(profiles.loaded).toBe(true)
    expect(profiles.items).toHaveLength(2)
    expect(profiles.items.some((item) => item.id === 'connection-1')).toBe(true)
    expect(root!.querySelectorAll('[data-profile-record]')).toHaveLength(2)
    expect(fetcher.mock.calls.filter(([path, init]) => String(path).endsWith('/provider-profiles') && !init?.method).length).toBeGreaterThanOrEqual(3)
  })

  it('allows Escape to close while read requests never complete without sending writes', async () => {
    const { fetcher } = setup(); await settle()
    const originalFetch = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (input, init) => {
      if (String(input).endsWith('/provider-profiles') || String(input).endsWith('/capabilities')) return new Promise<Response>(() => {})
      return originalFetch(input, init)
    })
    await openManager()
    expect(root!.querySelector('[data-profile-manager]')).not.toBeNull()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    expect(root!.querySelector('[role=dialog]')).toBeNull()
    expect(fetcher.mock.calls.some(([, init]) => ['POST', 'PUT', 'DELETE'].includes(init?.method || ''))).toBe(false)
  })

  it('allows closing during the read refresh after a completed write', async () => {
    const { fetcher, profiles } = setup(); await settle(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.click(); await settle(); await fill('display_name', 'Saved before refresh')
    const originalFetch = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (input, init) => {
      if (!init?.method && (String(input).endsWith('/provider-profiles') || String(input).endsWith('/capabilities'))) return new Promise<Response>(() => {})
      return originalFetch(input, init)
    })
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(profiles.busy).toBe(false)
    expect(profiles.loading).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await settle()
    expect(root!.querySelector('[role=dialog]')).toBeNull()
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('derives creation defaults from an extension descriptor and keeps failed form input across languages', async () => {
    const { state, fetcher } = setup({ rejectSave: true }); await settle(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.click(); await settle()
    expect(root!.querySelector<HTMLInputElement>('[data-profile-field=base_url]')!.value).toBe('https://new.service.test/api')
    expect(root!.querySelector<HTMLInputElement>('[data-profile-field=api_key_env]')!.value).toBe('NEW_KEY')
    expect(root!.querySelector<HTMLInputElement>('[data-profile-field=default_timeout]')!.value).toBe('37')
    expect(root!.querySelector('[type=password]')).toBeNull()
    await fill('display_name', 'My service')
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(root!.textContent).toContain('Original safe diagnostic')
    setLocale('en'); await settle()
    expect(root!.querySelector<HTMLInputElement>('[data-profile-field=display_name]')!.value).toBe('My service')
    state.rejectSave = false
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    const posts = fetcher.mock.calls.filter(([path, init]) => String(path).endsWith('/provider-profiles') && init?.method === 'POST')
    expect(posts).toHaveLength(2)
    const body = JSON.parse(posts[1]![1]!.body as string)
    expect(body.provider_type).toBe('extension_service')
    expect(body.api_key_env).toBe('NEW_KEY')
    expect(body.id).toBe(JSON.parse(posts[0]![1]!.body as string).id)
  })

  it('preserves unknown fields and unsupported legacy profile types when editing', async () => {
    const { fetcher } = setup({ profiles: [profile('legacy', { provider_type: 'legacy_type', future_metadata: { version: 3 } })] }); await settle(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-edit=legacy]')!.click(); await settle()
    expect(root!.querySelector<HTMLSelectElement>('[data-profile-field=provider_type]')!.disabled).toBe(true)
    await fill('display_name', 'Renamed legacy')
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    const body = JSON.parse(fetcher.mock.calls.find(([, init]) => init?.method === 'PUT')![1]!.body as string)
    expect(body.provider_type).toBe('legacy_type')
    expect(body.future_metadata).toEqual({ version: 3 })
  })

  it('keeps draft, undo, local files, viewport and history when CRUD refreshes only caches', async () => {
    const { workflow, fetcher } = setup(); await settle(); await openProject()
    workflow.updateNode('cloud', { config: { ...workflow.draft.nodes[1]!.config, model: 'edited-model' } })
    root!.querySelector<HTMLButtonElement>('[data-collection-enable]')!.click(); await settle()
    const picker = root!.querySelector<HTMLInputElement>('[data-batch-file-input]')!
    Object.defineProperty(picker, 'files', { value: [new File(['data'], 'pending.wav')], configurable: true })
    picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    workflow.pan = { x: 101, y: 102 }; workflow.zoom = .8
    workflow.history = [{ id: 'old-run', workflow_revision: 1, status: 'completed', created_at: '', finished_at: '', error: null }]
    const draft = JSON.stringify(workflow.draft)
    const load = vi.spyOn(workflow, 'load')
    await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.click(); await settle(); await fill('display_name', 'Another service')
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(load).not.toHaveBeenCalled()
    expect(JSON.stringify(workflow.draft)).toBe(draft)
    expect(workflow.canUndo).toBe(true)
    expect(workflow.pan).toEqual({ x: 101, y: 102 })
    expect(workflow.zoom).toBe(.8)
    expect(workflow.history[0]!.id).toBe('old-run')
    root!.querySelector<HTMLButtonElement>('.profiles-dialog .canvas-dialog-close')!.click(); await settle()
    expect(root!.textContent).toContain('pending.wav')
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith('/workflow'))).toHaveLength(1)
  })

  it('preserves the original selected ID when its connection is deleted', async () => {
    const { workflow } = setup(); await settle(); await openProject(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-delete="connection-1"]')!.click(); await settle()
    root!.querySelector<HTMLButtonElement>('.profiles-dialog .canvas-dialog-close')!.click(); await settle()
    const select = root!.querySelector<HTMLSelectElement>('[data-field=connection_ref]')!
    expect(select.value).toBe('connection-1')
    expect(select.textContent).toContain(t('profiles.unknownOption', { value: 'connection-1' }))
    expect(workflow.draft.nodes[1]!.config.connection_ref).toBe('connection-1')
    expect(workflow.dirty).toBe(false)
    expect(root!.querySelector('[data-provider-target="connection-1"]')!.textContent).toContain(t('profiles.missing'))
  })

  it('keeps language parameter wiring generic and authentication off parameter ports', async () => {
    const { workflow } = setup(); await settle(); await openProject()
    workflow.addNode('language_value', { x: 0, y: 600 }); await settle()
    const value = workflow.draft.nodes.find((node) => node.type === 'language_value')!
    expect(workflow.connect(value.id, 'cloud', 'language', 'language')).toBe(true); await settle()
    expect((root!.querySelector('[data-field=language]')!.closest('label') as HTMLElement).style.display).toBe('none')
    expect(workflow.registry!.kind('remote_extension')!.fields.find((field) => field.key === 'connection_ref')!.inputPort).toBeNull()
    expect(root!.querySelector('[data-provider-target="connection-1"]')!.textContent).toContain('old.service.test')
    expect(root!.textContent).toContain(t('profiles.temporaryRetention', { hours: 17 }))
    expect(root!.textContent).not.toContain('48')
  })

  it('keeps one modal and restores focus, and blocks running behind the connection dialog', async () => {
    const { fetcher, workflow } = setup(); await settle(); await openProject()
    const trigger = root!.querySelector<HTMLButtonElement>('[data-open-profiles]')!
    trigger.focus(); trigger.click(); await settle()
    app!._instance!.exposed!.showCreate(); await settle()
    expect(root!.querySelectorAll('[role=dialog]')).toHaveLength(1)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
    root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.click(); await settle()
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    root!.querySelector<HTMLButtonElement>('.profiles-dialog .canvas-dialog-close')!.click(); await settle()
    expect(document.activeElement).toBe(trigger)
    workflow.run = { id: 'active', project_id: 'p1', audio_id: 'a1', status: 'running', workflow_revision: 1, created_at: '', started_at: '', finished_at: null, cancel_requested: false, error: null, workflow: structuredClone(catalog().workflow.default_graph), nodes: [] } satisfies WorkflowRun
    await settle()
    expect(root!.querySelector<HTMLButtonElement>('[data-open-profiles]')!.disabled).toBe(true)
  })

  it('reports a successful save separately from failed catalog refresh and does not create twice', async () => {
    const { state, fetcher } = setup(); await settle(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-new]')!.click(); await settle(); await fill('display_name', 'Saved connection')
    state.failCatalog = true
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(root!.textContent).toContain(t('profiles.reloadFailed'))
    state.failCatalog = false
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1)
  })

  it('preserves records and edited inputs after rejected update and delete operations', async () => {
    const { state, profiles } = setup(); await settle(); await openManager()
    root!.querySelector<HTMLButtonElement>('[data-profile-edit="connection-1"]')!.click(); await settle(); await fill('display_name', 'Unsaved edit')
    state.rejectSave = true
    root!.querySelector('form.profile-form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await settle()
    expect(profiles.items[0]!.display_name).toBe('connection-1')
    expect(root!.querySelector<HTMLInputElement>('[data-profile-field=display_name]')!.value).toBe('Unsaved edit')
    root!.querySelector<HTMLButtonElement>('[data-profile-delete="connection-1"]')!.click(); await settle()
    expect(profiles.items).toHaveLength(1)
    expect(root!.querySelector('[data-profile-record="connection-1"]')).not.toBeNull()
  })
})

describe('inline node credentials', () => {
  async function typeKey(value = 'private-test-key') {
    const input = root!.querySelector<HTMLInputElement>('[data-credential-input]')!
    input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    return input
  }
  it('remeasures expanded help and late fonts without dragging nodes, changing legacy sizes or losing private input', async () => {
    const fontDescriptor = Object.getOwnPropertyDescriptor(document, 'fonts')
    const fonts = Object.assign(new EventTarget(), { ready: Promise.resolve() })
    Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
    let extraFontHeight = 0
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      if (!this.classList.contains('node-config-fields') || !this.closest('.node-measure-host')) return originalRect.call(this)
      const height = (this.querySelector<HTMLDetailsElement>('[data-node-data-handling]')?.open ? 1000 : 700) + extraFontHeight
      return { width: 320, height, x: 0, y: 0, top: 0, left: 0, right: 320, bottom: height, toJSON: () => ({}) }
    })
    const frame = async () => { await new Promise<void>((resolve) => requestAnimationFrame(() => resolve())); await settle() }
    try {
      const { workflow } = setup({ inline: true }); await settle(); await openProject()
      workflow.updateNode('cloud', { size: { width: 316, height: 300 } })
      workflow.document = { ...workflow.document!, ...JSON.parse(JSON.stringify(workflow.draft)) }
      workflow.setCleanBaseline(workflow.draft)
      const input = await typeKey(); await frame()
      const cloud = () => workflow.draft.nodes.find((node) => node.id === 'cloud')!
      const graph = JSON.stringify(workflow.draft)
      const closedHeight = workflow.getNodeSize(cloud()).height
      expect(workflow.getNodeSize(cloud()).width).toBeGreaterThan(316)
      expect(closedHeight).toBeGreaterThan(300)
      const help = root!.querySelector<HTMLDetailsElement>('[data-node-data-handling]')!
      expect(help.open).toBe(false)
      const summary = help.querySelector('summary')!
      summary.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 20, clientY: 20 }))
      help.open = true; await settle(); await frame()
      window.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 200, clientY: 200 }))
      const expandedHeight = workflow.getNodeSize(cloud()).height
      expect(expandedHeight).toBeGreaterThan(closedHeight)
      extraFontHeight = 50; fonts.dispatchEvent(new Event('loadingdone')); await frame()
      expect(workflow.getNodeSize(cloud()).height).toBeGreaterThan(expandedHeight)
      help.open = false; await settle(); await frame()
      expect(workflow.getNodeSize(cloud()).height).toBeLessThan(expandedHeight)
      setLocale('en'); await settle(); await frame()
      expect(input.value).toBe('private-test-key')
      expect(cloud().size).toEqual({ width: 316, height: 300 })
      expect(JSON.stringify(workflow.draft)).toBe(graph)
      expect(root!.querySelector('[data-inline-provider-target=cloud]')).not.toBeNull()
    } finally {
      app?.unmount(); app = undefined
      if (fontDescriptor) Object.defineProperty(document, 'fonts', fontDescriptor)
      else Reflect.deleteProperty(document, 'fonts')
    }
  })
  it('keeps new connections inline, old references unchanged, and nullable fields visibly empty', async () => {
    const { workflow } = setup({ inline: true, legacy: true }); await settle(); await openProject()
    expect(workflow.draft.nodes[1]!.config.mode).toBeUndefined()
    expect(root!.querySelector('[data-credential-input]')).toBeNull()
    const mode = root!.querySelector<HTMLSelectElement>('[data-connection-mode]')!
    mode.value = 'inline'; mode.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    expect(workflow.draft.nodes[1]!.config.endpoint).toBe('https://old.service.test/api')
    expect(workflow.draft.nodes[1]!.config.connection_ref).toBe('connection-1')
    workflow.undo(); await settle()
    expect(root!.querySelector('[data-credential-input]')).toBeNull()
    expect(workflow.dirty).toBe(false)
    workflow.addNode('remote_extension'); await settle()
    expect(workflow.draft.nodes[workflow.draft.nodes.length - 1]!.config.mode).toBe('inline')
  })
  it('marks key-only edits dirty, guards save/run/leave, and never places keys in Pinia, graph, validation or transfer data', async () => {
    const { workflow, pinia, fetcher } = setup({ inline: true }); await settle(); await openProject()
    expect(root!.querySelector<HTMLInputElement>('[data-connection-field=env]')!.value).toBe('')
    expect(workflow.dirty).toBe(false)
    const input = await typeKey()
    expect(workflow.dirty).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
    const save = [...root!.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === t('workflow.validateAndSave'))!
    expect(save.disabled).toBe(false)
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(JSON.stringify(pinia.state.value)).not.toContain('private-test-key')
    expect(serializeWorkflow(workflow.draft, workflow.registry!)).not.toContain('private-test-key')
    expect(copySelection(workflow.draft, ['cloud'], workflow.registry!)).not.toContain('saved-ref')
    await workflow.validate()
    expect(fetcher.mock.calls.filter(([path]) => String(path).endsWith('/workflow/validate')).every(([, init]) => !String(init?.body).includes('private-test-key'))).toBe(true)
    setLocale('en'); await settle()
    expect(input.value).toBe('private-test-key')
    await workflow.save(); await settle()
    expect(input.value).toBe('')
    expect(workflow.dirty).toBe(false)
    expect(workflow.draft.nodes[1]!.config.credential_handle).toBe('new-ref-2')
    const body = JSON.parse(fetcher.mock.calls.find(([, init]) => init?.method === 'PUT')![1]!.body as string)
    expect(body.credential_updates).toEqual([{ node_id: 'cloud', operation: 'set', api_key: 'private-test-key' }])
    expect(JSON.stringify(body.nodes)).not.toContain('private-test-key')
  })
  it('retries a lost Save response with the same request ID and clears only after confirmed success', async () => {
    const { state, workflow, fetcher } = setup({ inline: true }); await settle(); await openProject(); const input = await typeKey()
    state.loseSaveResponse = true
    await expect(workflow.save()).rejects.toThrow(); await settle()
    expect(input.value).toBe('private-test-key')
    expect(workflow.dirty).toBe(true)
    await workflow.save(); await settle()
    const bodies = fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT').map(([, init]) => JSON.parse(String(init!.body)))
    expect(bodies[0]).toEqual(bodies[1])
    expect(input.value).toBe('')
    expect(workflow.document!.revision).toBe(2)
  })
  it('retains failed inputs, changes the request ID only when content changes, and allows cancelling a pending clear', async () => {
    const { state, workflow, fetcher } = setup({ inline: true }); await settle(); await openProject(); const input = await typeKey()
    state.rejectWorkflow = true
    await expect(workflow.save()).rejects.toThrow(); await settle()
    expect(input.value).toBe('private-test-key')
    await typeKey('replacement-test-key')
    await expect(workflow.save()).rejects.toThrow(); await settle()
    const bodies = fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT').map(([, init]) => JSON.parse(String(init!.body)))
    expect(bodies[0].request_id).not.toBe(bodies[1].request_id)
    root!.querySelector<HTMLButtonElement>('[data-credential-discard]')!.click(); await settle()
    expect(workflow.dirty).toBe(false)
    root!.querySelector<HTMLButtonElement>('[data-credential-clear]')!.click(); await settle()
    expect(workflow.credentialPending).toBe(true)
    root!.querySelector<HTMLButtonElement>('[data-credential-discard]')!.click(); await settle()
    expect(workflow.dirty).toBe(false)
    root!.querySelector<HTMLButtonElement>('[data-credential-clear]')!.click(); await settle(); state.rejectWorkflow = false
    await workflow.save(); await settle()
    expect(workflow.draft.nodes[1]!.config.credential_handle).toBeNull()
    expect(workflow.credentialPending).toBe(false)
  })
  it('never restores private keys through undo, deletion, discard or portable paste/import', async () => {
    const { workflow } = setup({ inline: true }); await settle(); await openProject(); await typeKey()
    const copied = copySelection(workflow.draft, ['cloud'], workflow.registry!)
    const pasted = preparePaste(copied, workflow.draft, workflow.registry!)
    expect(JSON.stringify(pasted)).not.toContain('credential_handle')
    const imported = parseWorkflowFile(serializeWorkflow(workflow.draft, workflow.registry!), workflow.registry!)
    expect(JSON.stringify(imported)).not.toContain('credential_handle')
    workflow.removeNode('cloud'); await settle(); expect(workflow.credentialPending).toBe(false)
    workflow.undo(); await settle()
    expect(root!.querySelector<HTMLInputElement>('[data-credential-input]')!.value).toBe('')
    await typeKey(); workflow.discardDraft(); await settle()
    expect(root!.querySelector<HTMLInputElement>('[data-credential-input]')!.value).toBe('')
    expect(workflow.dirty).toBe(false)
    await typeKey(); workflow.stop(); expect(workflowCredentials(workflow.credentialContext).pending.value).toBe(false)
  })
  it('keeps env-derived frozen refs current only for the same saved revision and marks key edits or rotations historical', async () => {
    const { workflow } = setup({ inline: true }); await settle(); await openProject()
    workflow.updateNode('cloud', { config: { ...workflow.draft.nodes[1]!.config, env: 'ENV_FALLBACK', credential_handle: null } })
    await workflow.save(); await settle()
    const frozen = JSON.parse(JSON.stringify(workflow.draft))
    frozen.nodes[1].config.credential_handle = 'env-frozen-ref'
    workflow.run = { id: 'completed', project_id: 'p1', audio_id: 'a1', workflow_revision: workflow.document!.workflow_revision, status: 'completed', created_at: '', started_at: '', finished_at: '', cancel_requested: false, error: null, workflow: frozen, nodes: [] }
    expect(workflow.runMatchesDraft).toBe(true)
    workflow.run.workflow_revision -= 1
    expect(workflow.runMatchesDraft).toBe(false)
    workflow.run.workflow_revision += 1
    await typeKey()
    expect(workflow.runMatchesDraft).toBe(false)
    root!.querySelector<HTMLButtonElement>('[data-credential-discard]')!.click(); await settle()
    expect(workflow.runMatchesDraft).toBe(true)
    await typeKey(); await workflow.save(); await settle()
    expect(workflow.runMatchesDraft).toBe(false)
  })
  it('compares explicit saved credential versions and displays historical inline destinations from the frozen graph', async () => {
    const { workflow } = setup({ inline: true }); await settle(); await openProject()
    const frozen = JSON.parse(JSON.stringify(workflow.draft))
    workflow.run = { id: 'completed', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed', created_at: '', started_at: '', finished_at: '', cancel_requested: false, error: null, workflow: frozen, nodes: [] }
    expect(workflow.runMatchesDraft).toBe(true)
    await typeKey(); await workflow.save(); await settle()
    expect(workflow.runMatchesDraft).toBe(false)
    workflow.updateNode('cloud', { config: { ...workflow.draft.nodes[1]!.config, endpoint: 'https://changed.service.test/api' } })
    const runButton = [...root!.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes(t('workflow.panelRun')))!
    runButton.click(); await settle()
    expect(root!.querySelector('[data-inline-provider-snapshot-target=cloud]')!.textContent).toContain('inline.service.test')
    expect(root!.querySelector('[data-inline-provider-snapshot-target=cloud]')!.textContent).not.toContain('changed.service.test')
  })
  it('locks connection edits during Save and keeps forced input events from changing the submitted key', async () => {
    const { workflow, fetcher } = setup({ inline: true }); await settle(); await openProject(); const input = await typeKey()
    const originalFetch = fetcher.getMockImplementation()!
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    fetcher.mockImplementation(async (path, init) => { if (init?.method === 'PUT') await gate; return originalFetch(path, init) })
    const save = workflow.save(); await settle()
    expect(input.disabled).toBe(true)
    expect(root!.querySelector<HTMLInputElement>('[data-connection-field=endpoint]')!.disabled).toBe(true)
    input.value = 'forced-change'; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    expect(workflowCredentials(workflow.credentialContext).value('cloud')).toBe('private-test-key')
    release(); await save; await settle()
    expect(input.value).toBe('')
  })
  it('redacts failed Save diagnostics and protects private input through the leave confirmation', async () => {
    const { workflow, fetcher } = setup({ inline: true }); await settle(); await openProject(); const input = await typeKey()
    const originalFetch = fetcher.getMockImplementation()!
    fetcher.mockImplementation(async (path, init) => init?.method === 'PUT' ? response({ error: { code: 'UNKNOWN', message: 'Rejected private-test-key' } }, 422) : originalFetch(path, init))
    await expect(workflow.save()).rejects.toThrow('Rejected [redacted]'); await settle()
    expect(workflow.error).not.toContain('private-test-key')
    expect(input.value).toBe('private-test-key')
    app!._instance!.exposed!.showList(); await settle()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    expect(root!.querySelectorAll('[role=dialog]')).toHaveLength(1)
    const stay = [...root!.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === t('workflow.continueEditing'))!
    stay.click(); await settle()
    expect(input.value).toBe('private-test-key')
  })
  it.each(['missing', 'duplicate', 'parameter', 'type'])('rejects malformed 1.9 inline metadata: %s', (mutation) => {
    const value = inlineCatalog(); const node = value.workflow.nodes[1]
    if (mutation === 'missing') delete node.presentation.inline_connection_input.timeout_key
    if (mutation === 'duplicate') node.presentation.inline_connection_input.credential_ref_key = 'env'
    if (mutation === 'parameter') node.config.fields.find((field: any) => field.key === 'credential_handle').input_port = 'language'
    if (mutation === 'type') node.config.fields.find((field: any) => field.key === 'credential_handle').type = 'json'
    expect(() => readCapabilities(value)).toThrow()
  })
})

describe('independent streaming node connections', () => {
  it('renders an inline-only WSS connection without loading profiles, mode, region or env controls', async () => {
    const { workflow, fetcher } = setup({ streaming: true }); await settle(); await openProject()
    const node = root!.querySelector('[data-node-id=cloud]')!
    expect(node.querySelector('[data-connection-mode], [data-open-profiles], [data-connection-field=env], [data-connection-field=region]')).toBeNull()
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/provider-profiles'))).toBe(false)
    expect(node.querySelector('[data-inline-provider-target=cloud]')!.textContent).toContain('wss://stream.service.test/api-ws/v1/inference')
    expect(node.textContent).toContain(t('profiles.cancelClosesStream'))
    expect(node.textContent).not.toContain(t('profiles.cancelPendingOnly'))
    expect(node.textContent).not.toContain('48')
    expect(node.querySelector<HTMLInputElement>('[data-field=model]')!.value).toBe('qwen-audio-3.0-asr-flash-streaming')
    expect(node.textContent).toContain('PCM16')
    expect(workflow.registry!.validateConfig('qwen_asr_streaming', workflow.draft.nodes[1]!.config)).toEqual([])
    setLocale('en'); await settle()
    expect(node.textContent).toContain('Microphone input and live captions are not provided')
    expect(node.textContent).not.toContain(t('profiles.keyNotStored'))
  })
  it('isolates two streaming credentials and a file service credential through failed Save and idempotent retry', async () => {
    const { state, workflow, fetcher, pinia } = setup({ streaming: true }); await settle(); await openProject()
    workflow.addNode('qwen_asr_streaming'); workflow.addNode('remote_extension'); await settle()
    const nodes = workflow.draft.nodes.filter((node) => ['qwen_asr_streaming', 'remote_extension'].includes(node.type))
    for (const [index, node] of nodes.entries()) {
      const input = root!.querySelector<HTMLInputElement>(`[data-node-id="${node.id}"] [data-credential-input]`)!
      expect(input.value).toBe('')
      input.value = `private-instance-${index}`; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    }
    expect(JSON.stringify(pinia.state.value)).not.toContain('private-instance-')
    state.loseSaveResponse = true; await expect(workflow.save()).rejects.toThrow(); await settle()
    for (const [index, node] of nodes.entries()) expect(root!.querySelector<HTMLInputElement>(`[data-node-id="${node.id}"] [data-credential-input]`)!.value).toBe(`private-instance-${index}`)
    await workflow.save(); await settle()
    const bodies = fetcher.mock.calls.filter(([, init]) => init?.method === 'PUT').map(([, init]) => JSON.parse(String(init!.body)))
    expect(bodies[0]).toEqual(bodies[1])
    expect(bodies[1].credential_updates).toEqual(nodes.map((node, index) => ({ node_id: node.id, operation: 'set', api_key: `private-instance-${index}` })))
    for (const node of nodes) expect(root!.querySelector<HTMLInputElement>(`[data-node-id="${node.id}"] [data-credential-input]`)!.value).toBe('')
    const input = root!.querySelector<HTMLInputElement>('[data-node-id=cloud] [data-credential-input]')!
    input.value = 'pending-original'; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    const pasted = preparePaste(copySelection(workflow.draft, ['cloud'], workflow.registry!), workflow.draft, workflow.registry!)
    expect(JSON.stringify(pasted)).not.toContain('credential_handle')
    workflow.applyPastedGraph(pasted); await settle()
    const copied = pasted.nodes[0]!
    expect(root!.querySelector<HTMLInputElement>(`[data-node-id="${copied.id}"] [data-credential-input]`)!.value).toBe('')
    expect(workflowCredentials(workflow.credentialContext).updates()).toEqual([{ node_id: 'cloud', operation: 'set', api_key: 'pending-original' }])
    expect(serializeWorkflow(workflow.draft, workflow.registry!)).not.toContain('pending-original')
  })
  it('keeps safe complete frozen WSS endpoints and excludes URL credentials, query and fragment', async () => {
    expect(connectionTarget('wss://user:secret@stream.service.test/path?token=hidden#key')).toBe('wss://stream.service.test/path')
    const { workflow } = setup({ streaming: true }); await settle(); await openProject()
    const frozen = JSON.parse(JSON.stringify(workflow.draft))
    workflow.run = { id: 'done', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed', created_at: '', started_at: '', finished_at: '', cancel_requested: false, error: null, workflow: frozen, nodes: [] }
    expect(workflow.runMatchesDraft).toBe(true)
    workflow.updateNode('cloud', { config: { ...workflow.draft.nodes[1]!.config, endpoint: 'wss://changed.service.test/another-path' } })
    expect(workflow.runMatchesDraft).toBe(false)
    const runButton = [...root!.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes(t('workflow.panelRun')))!
    runButton.click(); await settle()
    const historical = root!.querySelector('[data-inline-provider-snapshot-target=cloud]')!
    expect(historical.textContent).toContain('wss://stream.service.test/api-ws/v1/inference')
    expect(historical.textContent).not.toContain('changed.service.test')
  })
  it.each(['half-mode', 'missing-provider', 'bad-handling', 'missing-env-role', 'bound-role'])('rejects malformed 1.10 inline metadata: %s', (mutation) => {
    const value = streamingCatalog(); const node = value.workflow.nodes[value.workflow.nodes.length - 1]
    const descriptor = node.presentation.inline_connection_input
    if (mutation === 'half-mode') descriptor.mode_value = 'inline'
    if (mutation === 'missing-provider') delete descriptor.provider_type
    if (mutation === 'bad-handling') descriptor.data_handling.cancel_behavior = 'pretend_remote_stops'
    if (mutation === 'missing-env-role') delete descriptor.api_key_env_key
    if (mutation === 'bound-role') node.config.fields.find((field: any) => field.key === 'credential_handle').input_port = 'language'
    expect(() => readCapabilities(value)).toThrow()
  })
  it('keeps safe Streaming HTTP status and accurate cancellation diagnostics in both locales', () => {
    for (const locale of ['zh-CN', 'en']) {
      setLocale(locale)
      expect(workflowErrorLabel({ type: 'QWEN_STREAM_AUTH_FAILED', message: 'HTTP 401' })).toContain('401')
      expect(batchError({ type: 'QWEN_STREAM_CANCELLED', message: 'safe fallback' })).toBe(t('profiles.error_QWEN_STREAM_CANCELLED'))
    }
  })
})

describe('provider capability and errors', () => {
  it('accepts empty unconfigured profile drafts and keeps models as free strings', () => {
    const registry = createNodeRegistry(readCapabilities(catalog()))
    expect(registry.validateConfig('remote_extension', { connection_ref: '', model: 'new-model', language: '', additional_parameters: {} })).toEqual([])
    expect(registry.kind('remote_extension')!.providerProfileInput!.providerType).toBe('extension_service')
  })
  it.each(['field', 'inputPort', 'defaults', 'handling'])('rejects malformed 1.7 profile metadata: %s', (mutation) => {
    const value = catalog(); const remote = value.workflow.nodes[1]
    if (mutation === 'field') remote.presentation.provider_profile_input.config_key = 'missing'
    if (mutation === 'inputPort') { remote.config.fields[0].input_port = 'language'; remote.config.fields[2].input_port = null }
    if (mutation === 'defaults') remote.presentation.provider_profile_input.defaults.api_key = 'must-not-be-accepted'
    if (mutation === 'handling') remote.presentation.provider_profile_input.data_handling.temporary_upload_retention_hours = -1
    expect(() => readCapabilities(value)).toThrow()
  })
  it('translates built-in errors without losing safe HTTP status or unknown extension diagnostics', () => {
    const error = { type: 'QWEN_HTTP_ERROR', message: 'Qwen请求失败（HTTP 429）' }
    expect(workflowErrorLabel(error)).toContain('429')
    setLocale('en')
    expect(workflowErrorLabel(error)).toContain('Qwen rejected')
    expect(batchError(error)).toContain('429')
    expect(workflowErrorLabel({ type: 'FUTURE_EXTENSION', message: 'Original diagnostic' })).toBe('Original diagnostic')
  })

  it('uses frozen historical targets without adopting current connections or current handling policy', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const workflow = useWorkflowStore(pinia)
    const profiles = useProviderProfilesStore(pinia)
    workflow.capabilities = readCapabilities(catalog())
    profiles.items = [profile('connection-1', { base_url: 'https://current.service.test/api' })]
    const graph = { ...catalog().workflow.default_graph, provider_profiles_snapshot: { 'connection-1': profile('connection-1', { base_url: 'https://frozen.service.test/api', region: 'frozen-region' }) } }
    root = document.createElement('div'); document.body.append(root)
    app = createApp(CloudWorkflowNotice, { workflow: graph, audioCount: 3, historical: true }).use(pinia); app.mount(root); await settle()
    expect(root.querySelector('[data-provider-snapshot-target="connection-1"]')!.textContent).toContain('frozen.service.test')
    expect(root.textContent).not.toContain('current.service.test')
    expect(root.textContent).not.toContain(t('profiles.temporaryRetention', { hours: 17 }))
    expect(root.textContent).not.toContain(t('profiles.cancelPendingOnly'))
  })

  it('leaves unspecified provider retention and cancellation policy undisclosed', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const value = catalog(); delete value.workflow.nodes[1].presentation.provider_profile_input.data_handling
    useWorkflowStore(pinia).capabilities = readCapabilities(value)
    useProviderProfilesStore(pinia).items = [profile()]
    root = document.createElement('div'); document.body.append(root)
    app = createApp(CloudWorkflowNotice, { workflow: value.workflow.default_graph, audioCount: 1 }).use(pinia); app.mount(root); await settle()
    expect(root.textContent).toContain(t('profiles.cloudNotice'))
    expect(root.textContent).not.toContain(t('profiles.temporaryRetention', { hours: 17 }))
    expect(root.textContent).not.toContain(t('profiles.cancelPendingOnly'))
  })
})
