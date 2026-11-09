import { locale, setLocale, LOCALE_STORAGE_KEY } from './i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import GlobalSettingsDialog from './components/GlobalSettingsDialog.vue'
import { settingsCache } from './composables/globalSettings'
const field=(key:string,value:unknown)=>({key,type:'string',label:key,default:value,input_port:null,suggestion_source:null,required:false})
const document=()=>({schema_version:'1.0',revision:1,global_fields:[],global:{},node_defaults:{extension:{label:'Extension',fields:[field('model','arbitrary-model')],config:{model:'arbitrary-model'},credential_configured:false,credential_version:null}}})
const response=(value:unknown,status=200)=>({ok:status<400,status,json:async()=>structuredClone(value)}) as Response
let app:App|undefined;let root:HTMLDivElement|undefined
async function settle(){for(let index=0;index<35;index++)await Promise.resolve();await nextTick()}
function mount(close=vi.fn()) {root=window.document.createElement('div');window.document.body.append(root);app=createApp(GlobalSettingsDialog,{open:true,credentialTypes:['extension'],onClose:close});app.mount(root);return close}
async function open(){await settle();root!.querySelectorAll<HTMLButtonElement>('.settings-tabs button')[1]!.click();await nextTick()}
afterEach(()=>{app?.unmount();root?.remove();settingsCache.value=null;vi.unstubAllGlobals();vi.restoreAllMocks();setLocale('zh-CN');window.localStorage.removeItem(LOCALE_STORAGE_KEY)})
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
