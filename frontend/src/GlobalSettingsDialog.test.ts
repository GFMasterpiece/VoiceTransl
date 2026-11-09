import { nodeFixture, catalogFixture, fieldFixture } from './workflow/fixtures'
import { readCapabilities } from './workflow/capabilities'
import { debugMode, setDebugMode, DEBUG_MODE_STORAGE_KEY } from './composables/editorPreferences'
import { locale, setLocale, LOCALE_STORAGE_KEY } from './i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import GlobalSettingsDialog from './components/GlobalSettingsDialog.vue'
import { settingsCache } from './composables/globalSettings'
const field=(key:string,value:unknown)=>(fieldFixture({key,type:'string',label:key,default:value,input_port:null,suggestion_source:null,required:false}))
const document=()=>({schema_version:"2.0",revision:1,global_fields:[],global:{},node_defaults:{extension:{label:'Extension',fields:[field('model','arbitrary-model')],config:{model:'arbitrary-model'},credential_configured:false,credential_version:null}}})
const response=(value:unknown,status=200)=>({ok:status<400,status,json:async()=>structuredClone(value)}) as Response
let app:App|undefined;let root:HTMLDivElement|undefined
async function settle(){for(let index=0;index<35;index++)await Promise.resolve();await nextTick()}
function mount(close=vi.fn(), extra: Record<string, unknown> = {}) {root=window.document.createElement('div');window.document.body.append(root);app=createApp(GlobalSettingsDialog,{open:true,credentialTypes:['extension'],onClose:close,...extra});app.mount(root);return close}
async function open(){await settle();root!.querySelectorAll<HTMLButtonElement>('.settings-tabs button')[1]!.click();await nextTick()}
afterEach(()=>{app?.unmount();root?.remove();settingsCache.value=null;vi.unstubAllGlobals();vi.restoreAllMocks();setLocale('zh-CN');window.localStorage.removeItem(LOCALE_STORAGE_KEY);setDebugMode(false);window.localStorage.removeItem(DEBUG_MODE_STORAGE_KEY)})
describe('settings dialog write-only keys',()=>{
 it('keeps keys on failure, retries the same payload/request ID and clears keys on success',async()=>{
   let fail=true;const bodies:any[]=[]
   vi.stubGlobal('fetch',vi.fn(async(_url:unknown,init?:RequestInit)=>{if(init?.method==='PUT'){const body=JSON.parse(String(init.body));bodies.push(body);if(fail)throw new TypeError('network');return response({...document(),revision:2,node_defaults:{extension:{...document().node_defaults.extension,credential_configured:true,credential_version:'saved-version'}}})}return response(document())}))
   mount();await open();const input=root!.querySelector<HTMLInputElement>('[data-settings-key]')!;input.value='secret-value';input.dispatchEvent(new Event('input',{bubbles:true}));await nextTick()
   root!.querySelector<HTMLButtonElement>('.settings-actions .primary-button')!.click();await settle();expect(input.value).toBe('secret-value');expect(root!.textContent).not.toContain('secret-value')
   fail=false;root!.querySelector<HTMLButtonElement>('.settings-actions .primary-button')!.click();await settle();expect(bodies[1]).toEqual(bodies[0]);expect(input.value).toBe('');expect(JSON.stringify(settingsCache.value)).not.toContain('secret-value')
 })
 it('can close during a pending read and ignores a late response',async()=>{
   let resolve!:(value:Response)=>void;vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(done=>{resolve=done})));const close=mount();await nextTick();root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();expect(close).toHaveBeenCalledOnce();resolve(response(document()));await settle();expect(root!.querySelector('[data-field=model]')).toBeNull()
 })
})


describe('stable settings layout and local interface language', () => {
  function change(input: HTMLInputElement | HTMLSelectElement, value: string) {
    input.value = value
    input.dispatchEvent(new Event(input instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  }
  it('changes and persists interface language immediately without making server settings dirty', async () => {
    const fetcher = vi.fn(async () => response(document())); vi.stubGlobal('fetch', fetcher)
    const confirm = vi.spyOn(window, 'confirm'); const close = mount(); await settle()
    const save = root!.querySelector<HTMLButtonElement>('[data-settings-actions] .primary-button')!
    expect(save.disabled).toBe(true)
    change(root!.querySelector<HTMLSelectElement>('[data-settings-locale]')!, 'en'); await nextTick()
    expect(locale.value).toBe('en'); expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
    expect(root!.querySelector('.canvas-dialog-header h2')!.textContent).toBe('Global settings')
    const debug = root!.querySelector<HTMLInputElement>('[data-settings-debug]')!; debug.checked = true; debug.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    expect(debugMode.value).toBe(true); expect(window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY)).toBe('true')
    expect(save.disabled).toBe(true); expect(fetcher).toHaveBeenCalledTimes(1)
    root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click()
    expect(close).toHaveBeenCalledOnce(); expect(confirm).not.toHaveBeenCalled()
  })
  it('preserves unsaved global fields, node fields and private keys across tabs and language changes without sending locale', async () => {
    const settings = { ...document(), global_fields: [{ ...field('upload_concurrency', 3), type: 'integer' }], global: { upload_concurrency: 3 } }
    const bodies: any[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === 'PUT') bodies.push(JSON.parse(String(init.body)))
      return response(settings)
    }))
    mount(); await settle()
    change(root!.querySelector<HTMLInputElement>('[data-field=upload_concurrency]')!, '5')
    await open(); change(root!.querySelector<HTMLInputElement>('[data-field=model]')!, 'future-model')
    change(root!.querySelector<HTMLInputElement>('[data-settings-key]')!, 'private-secret')
    root!.querySelectorAll<HTMLButtonElement>('[data-settings-tabs] button')[0]!.click(); await nextTick()
    change(root!.querySelector<HTMLSelectElement>('[data-settings-locale]')!, 'en'); await nextTick()
    expect(root!.querySelector<HTMLInputElement>('[data-field=upload_concurrency]')!.value).toBe('5')
    await open()
    expect(root!.querySelector<HTMLInputElement>('[data-field=model]')!.value).toBe('future-model')
    expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('private-secret')
    expect(JSON.stringify(window.localStorage)).not.toContain('private-secret')
    root!.querySelector<HTMLButtonElement>('[data-settings-actions] .primary-button')!.click(); await settle()
    expect(bodies).toHaveLength(1)
    expect(bodies[0].global).toEqual({ upload_concurrency: 5 })
    expect(bodies[0].node_defaults).toEqual({ extension: { model: 'future-model' } })
    expect(bodies[0].credential_updates).toEqual([{ node_type: 'extension', operation: 'set', api_key: 'private-secret' }])
    expect(Object.keys(bodies[0]).sort()).toEqual(['schema_version', 'expected_revision', 'request_id', 'global', 'node_defaults', 'credential_updates'].sort())
    expect(JSON.stringify(bodies[0])).not.toContain('locale')
  })
  it('keeps tabs and actions outside the scrolling content through loading, errors and long forms', async () => {
    let resolve!: (value: Response) => void
    let failSave = false
    vi.stubGlobal('fetch', vi.fn((_url: unknown, init?: RequestInit) => init?.method === 'PUT' && failSave ? Promise.reject(new TypeError('offline')) : new Promise<Response>(done => { resolve = done })))
    mount(); await nextTick()
    const dialog = root!.querySelector('.canvas-dialog')!
    const tabs = root!.querySelector('[data-settings-tabs]')!
    const content = root!.querySelector('[data-settings-content]')!
    const actions = root!.querySelector('[data-settings-actions]')!
    expect(content.contains(tabs)).toBe(false); expect(content.contains(actions)).toBe(false)
    const fields = Array.from({ length: 20 }, (_, index) => field(`field_${index}`, `value-${index}`))
    const config = Object.fromEntries(fields.map(item => [item.key, item.default]))
    resolve(response({ ...document(), node_defaults: { extension: { ...document().node_defaults.extension, fields, config } } })); await open()
    expect(content.querySelector('[data-field=field_19]')).not.toBeNull()
    change(content.querySelector<HTMLInputElement>('[data-field=field_19]')!, 'edited'); failSave = true; await nextTick()
    actions.querySelector<HTMLButtonElement>('.primary-button')!.click(); await settle()
    expect(content.querySelector('[role=alert]')).not.toBeNull()
    root!.querySelectorAll<HTMLButtonElement>('[data-settings-tabs] button')[0]!.click(); await nextTick()
    change(root!.querySelector<HTMLSelectElement>('[data-settings-locale]')!, 'en'); await nextTick()
    expect(root!.querySelector('.canvas-dialog')).toBe(dialog)
    expect(root!.querySelector('[data-settings-tabs]')).toBe(tabs)
    expect(root!.querySelector('[data-settings-actions]')).toBe(actions)
    expect(root!.querySelector('[data-settings-content]')).toBe(content)
  })
})


describe('category navigation and independent templates', () => {
  function fixture() {
    const makeField = (key: string, value: unknown, type = 'string') => ({ ...field(key, value), type })
    const fileFields = [makeField('model', 'file-model'), makeField('timeout_seconds', 30, 'integer'), makeField('base_url', 'https://file.test'), makeField('region', null), makeField('language', ''), makeField('additional_parameters', {}, 'json')]
    const streamFields = [makeField('model', 'stream-model'), makeField('endpoint', 'wss://stream.test'), makeField('timeout_seconds', 40, 'integer'), makeField('language', ''), makeField('additional_parameters', {}, 'json')]
    const declarations = [
      { type: 'file_extension', fields: fileFields, url: 'base_url', region: 'region', debug: false, category: { id: 'speech_recognition', label: 'ASR' } },
      { type: 'stream_extension', fields: streamFields, url: 'endpoint', region: null, debug: false, category: { id: 'speech_recognition', label: 'ASR' } },
      { type: 'hidden_fixture', fields: [makeField('language', 'original-hidden')], url: null, region: null, debug: true, category: { id: 'speech_recognition', label: 'ASR' } },
      ...Array.from({ length: 12 }, (_, index) => ({ type: `future_${index}`, fields: [makeField('model', `future-${index}`)], url: null, region: null, debug: false, category: { id: 'speech_recognition', label: 'ASR' } })),
      { type: 'unknown_category', fields: [makeField('model', 'other')], url: null, region: null, debug: false, category: { id: 'plugin_category', label: 'Plugin category' } },
      { type: 'no_category', fields: [makeField('model', 'uncategorized')], url: null, region: null, debug: false, category: null },
    ]
    const nodes = declarations.map(item => (nodeFixture({ type: item.type, label: item.type, debug_only: item.debug, category: item.category, input_ports: [], output_ports: [], required_input_ports: [], required_inbound: 0, is_parameter_source: false, config: { kind: item.type, fields: [...item.fields, ...(item.url ? [makeField('credential_ref', null)] : [])] }, ...(item.url ? { presentation: { inline_connection_input: { provider_type: 'extension_service', label: item.type, base_url_key: item.url, timeout_key: 'timeout_seconds', region_key: item.region, credential_ref_key: 'credential_ref' } } } : {}) })))
    const capabilities = readCapabilities(catalogFixture({ schema_version: '1.0', languages: [], workflow: { schema_version: "1.16", nodes, ports: [], data_types: [], constraints: { max_nodes: 64, max_edges: 64, min_sources: 0, max_sources: 1, min_terminals: 0 }, default_graph: { nodes: [], edges: [] } }, asr: { providers: [] }, export: { formats: [], naming_modes: [] } }))
    const settings = { ...document(), node_defaults: Object.fromEntries(declarations.map(item => [item.type, { label: item.type, fields: item.fields, config: Object.fromEntries(item.fields.map(field => [field.key, field.default])), credential_configured: false, credential_version: null }])) }
    return { capabilities, settings }
  }
  async function select(type: string) { const control = root!.querySelector<HTMLSelectElement>('[data-settings-node-type]')!; control.value = type; control.dispatchEvent(new Event('change', { bubbles: true })); await nextTick() }
  function edit(selector: string, value: string) { const input = root!.querySelector<HTMLInputElement>(selector)!; input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })) }
  it('groups arbitrary declarations into finite category navigation and follows connection roles then backend parameter order', async () => {
    const { capabilities, settings } = fixture(); vi.stubGlobal('fetch', vi.fn(async () => response(settings)))
    mount(vi.fn(), { capabilities, credentialTypes: ['file_extension', 'stream_extension'] }); await settle()
    const categories = [...root!.querySelectorAll('[data-settings-category]')]
    expect(categories.map(button => button.getAttribute('data-settings-category'))).toEqual(['speech_recognition', 'plugin_category', ''])
    expect(categories[1]!.textContent).toBe('Plugin category')
    expect(root!.querySelectorAll('[data-settings-tabs] button')).toHaveLength(4)
    ;(categories[0] as HTMLButtonElement).click(); await nextTick()
    expect(root!.querySelectorAll('[data-settings-node-type] option')).toHaveLength(14)
    const fieldOrder = () => [...root!.querySelectorAll('[data-settings-content] [data-field], [data-settings-key]')].map(input => input.hasAttribute('data-settings-key') ? 'KEY' : input.getAttribute('data-field'))
    expect(fieldOrder()).toEqual(['model', 'language', 'additional_parameters', 'base_url', 'timeout_seconds', 'region', 'KEY'])
    await select('stream_extension')
    expect(fieldOrder()).toEqual(['model', 'language', 'additional_parameters', 'endpoint', 'timeout_seconds', 'KEY'])
    expect(root!.querySelector('[data-settings-section=connection] [data-settings-key]')).not.toBeNull()
    expect(root!.querySelector('[data-settings-section=connection] [data-field=model]')).not.toBeNull()
  })
  it('keeps per-type fields and keys independent through type/category/debug/locale changes and saves hidden defaults', async () => {
    const { capabilities, settings } = fixture(); const bodies: any[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => { if (init?.method === 'PUT') bodies.push(JSON.parse(String(init.body))); return response(settings) }))
    mount(vi.fn(), { capabilities, credentialTypes: ['file_extension', 'stream_extension'] }); await settle()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=speech_recognition]')!.click(); await nextTick()
    edit('[data-field=model]', 'new-file-model'); edit('[data-settings-key]', 'file-secret')
    await select('stream_extension'); expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('')
    edit('[data-field=model]', 'new-stream-model'); edit('[data-settings-key]', 'stream-secret')
    root!.querySelector<HTMLButtonElement>('[data-settings-general]')!.click(); await nextTick()
    const debug = root!.querySelector<HTMLInputElement>('[data-settings-debug]')!; debug.checked = true; debug.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=speech_recognition]')!.click(); await nextTick()
    expect(root!.querySelector<HTMLSelectElement>('[data-settings-node-type]')!.value).toBe('stream_extension')
    expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('stream-secret')
    await select('hidden_fixture'); edit('[data-field=language]', 'changed-hidden')
    setDebugMode(false); await nextTick()
    expect(root!.querySelector('[data-settings-node-type]')).toBeNull()
    const localeInput = root!.querySelector<HTMLSelectElement>('[data-settings-locale]')!; localeInput.value = 'en'; localeInput.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=speech_recognition]')!.click(); await nextTick(); await select('file_extension')
    expect(root!.querySelector<HTMLInputElement>('[data-field=model]')!.value).toBe('new-file-model')
    expect(root!.querySelector<HTMLInputElement>('[data-settings-key]')!.value).toBe('file-secret')
    root!.querySelector<HTMLButtonElement>('[data-settings-category=plugin_category]')!.click(); await nextTick()
    root!.querySelector<HTMLButtonElement>('[data-settings-category=speech_recognition]')!.click(); await nextTick()
    expect(root!.querySelector<HTMLSelectElement>('[data-settings-node-type]')!.value).toBe('file_extension')
    root!.querySelector<HTMLButtonElement>('[data-settings-actions] .primary-button')!.click(); await settle()
    expect(bodies[0].node_defaults.file_extension.model).toBe('new-file-model')
    expect(bodies[0].node_defaults.stream_extension.model).toBe('new-stream-model')
    expect(bodies[0].node_defaults.hidden_fixture.language).toBe('changed-hidden')
    expect(bodies[0].credential_updates).toEqual([{ node_type: 'file_extension', operation: 'set', api_key: 'file-secret' }, { node_type: 'stream_extension', operation: 'set', api_key: 'stream-secret' }])
    expect(Object.keys(bodies[0]).sort()).toEqual(['schema_version', 'expected_revision', 'request_id', 'global', 'node_defaults', 'credential_updates'].sort())
  })
})
