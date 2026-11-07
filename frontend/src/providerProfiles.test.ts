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
const response = (value: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(value) }) as Response
async function settle() { for (let index = 0; index < 40; index++) await Promise.resolve(); await nextTick() }
let app: VueApp | undefined
let root: HTMLDivElement | undefined
function setup(options: { profiles?: ProviderProfile[]; rejectSave?: boolean; failCatalog?: boolean } = {}) {
  const state = { profiles: options.profiles || [profile()], rejectSave: !!options.rejectSave, failCatalog: !!options.failCatalog }
  const initial = catalog(state.profiles)
  const document = { ...initial.workflow.default_graph, schema_version: '1.0', project_id: 'p1', revision: 1, workflow_revision: 1, updated_at: '2026-11-07T00:00:00Z' }
  const audio = { id: 'a1', source: { original_name: 'local.wav', language: 'ja', size_bytes: 4 } }
  const project = { id: 'p1', name: 'Profile test', source: audio.source, audios: [audio], created_at: '2026-11-07', updated_at: '2026-11-07' }
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/capabilities')) return state.failCatalog ? response({ detail: 'Catalog temporarily unavailable' }, 503) : response(catalog(state.profiles))
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
    if (path.endsWith('/workflow')) return response(document)
    if (path.endsWith('/upload-policy')) return response({ allowed_extensions: ['wav'], max_upload_bytes: 100 })
    if (path.endsWith('/workflow/validate')) return response({ valid: true, errors: [] })
    if (path.includes('/workflow-runs?') || path.includes('/batches?')) return response({ items: [], total: 0, limit: 20, offset: 0 })
    return response({ items: [] })
  })
  vi.stubGlobal('fetch', fetcher)
  const pinia = createPinia(); setActivePinia(pinia)
  root = window.document.createElement('div'); window.document.body.append(root)
  app = createApp(App).use(pinia); app.mount(root)
  return { state, fetcher, workflow: useWorkflowStore(pinia), profiles: useProviderProfilesStore(pinia), document }
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
