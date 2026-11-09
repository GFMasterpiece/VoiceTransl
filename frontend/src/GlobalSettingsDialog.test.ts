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
afterEach(()=>{app?.unmount();root?.remove();settingsCache.value=null;vi.unstubAllGlobals();vi.restoreAllMocks()})
describe('settings dialog write-only keys',()=>{
 it('keeps keys on failure, retries the same payload/request ID and clears keys on success',async()=>{
   let fail=true;const bodies:any[]=[]
   vi.stubGlobal('fetch',vi.fn(async(_url:unknown,init?:RequestInit)=>{if(init?.method==='PUT'){const body=JSON.parse(String(init.body));bodies.push(body);if(fail)throw new TypeError('network');return response({...document(),revision:2,node_defaults:{extension:{...document().node_defaults.extension,credential_configured:true,credential_version:'saved-version'}}})}return response(document())}))
   mount();await open();const input=root!.querySelector<HTMLInputElement>('[data-settings-key]')!;input.value='secret-value';input.dispatchEvent(new Event('input',{bubbles:true}));await nextTick()
   root!.querySelector<HTMLButtonElement>('.settings-actions .primary-button')!.click();await settle();expect(input.value).toBe('secret-value');expect(root!.textContent).not.toContain('secret-value')
   fail=false;root!.querySelector<HTMLButtonElement>('.settings-actions .primary-button')!.click();await settle();expect(bodies[1]).toEqual(bodies[0]);expect(input.value).toBe('');expect(JSON.stringify(settingsCache.value)).not.toContain('secret-value')
 })
 it('can close during a pending read and ignores a late response',async()=>{
   let resolve!:(value:Response)=>void;vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(done=>{resolve=done})));const close=mount();await nextTick();root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();expect(close).toHaveBeenCalledOnce();resolve(response(document()));await settle();expect(root!.querySelector('.settings-tabs')).toBeNull()
 })
})
