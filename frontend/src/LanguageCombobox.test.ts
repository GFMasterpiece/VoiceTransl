import { emptySettings } from './workflow/fixtures'
import { catalogFixture, fieldFixture, nodeFixture, typeFixture } from './workflow/fixtures'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, createApp, h, nextTick, provide, reactive, ref, type App } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import LanguageCombobox from './components/LanguageCombobox.vue'
import CanvasDialog from './components/CanvasDialog.vue'
import WorkflowEditor from './components/WorkflowEditor.vue'
import { languageSuggestionsKey, matchesLanguage } from './composables/languageSuggestions'
import { readCapabilities, type CapabilityLanguage } from './workflow/capabilities'
import { useWorkflowStore } from './stores/workflow'
import type { Audio } from './stores/projects'
import { setLocale, t } from './i18n'

const languages: CapabilityLanguage[] = [
  { code: 'zh', names: { 'zh-CN': '中文', en: 'Chinese' }, nativeName: '中文', aliases: ['Mandarin', '普通话'] },
  { code: 'en', names: { 'zh-CN': '英语', en: 'English' }, nativeName: 'English', aliases: ['英语别名'] },
  { code: 'ja', names: { 'zh-CN': '日语', en: 'Japanese' }, nativeName: '日本語', aliases: ['Nihongo'] },
]
function catalog(): any {
  const field = (key: string, type = 'string', value: unknown = '', extra = {}) => (fieldFixture({ key, label: key, type, default: value, required: false, input_port: null, suggestion_source: null, ...extra }))
  const node = (type: string, fields: any[] = []) => (nodeFixture({ type, label: type, category: null, input_ports: [], output_ports: [], required_input_ports: [], required_inbound: 0, is_source: false, is_parameter_source: false, terminal: false, config: { kind: type, fields } }))
  const source = { ...node('input_extension', [field('chosen','string',null), field('many','json',null)]), output_ports: ['audio'], is_source: true, max_instances: 1, removable: false, presentation: { resource_input: { resource_type: 'audio', config_key: 'chosen', collection_key: 'many' } } }
  const processor = { ...node('processor_extension', [field('spoken_code','string','',{ suggestion_source: 'languages', input_port: 'language_signal' }), field('manual_language','string','en')]), input_ports: ['audio','language_signal'], required_input_ports: ['audio'], required_inbound: 1, output_ports: ['audio'] }
  const output = { ...node('output_extension'), input_ports: ['audio'], required_input_ports: ['audio'], required_inbound: 1, terminal: true }
  const parameter = { ...node('parameter_extension', [field('value','string','ja')]), output_ports: ['language_signal'], is_parameter_source: true }
  return catalogFixture({ schema_version: '1.0', languages: languages.map(({nativeName,...entry}) => ({ ...entry, names: {...entry.names}, aliases: [...entry.aliases], native_name: nativeName })), asr: { providers: [] }, workflow: {
    schema_version: "1.16", nodes: [source,processor,output,parameter], ports: [{ id:'audio',type_id:'audio',label:'Audio' },{id:'language_signal',type_id:'string',label:'Language'}],
    data_types: ['audio','string'].map(id => (typeFixture({ id,label:id,parent_type_id:null,capabilities:[] }))), constraints:{max_nodes:64,max_edges:64,min_sources:1,max_sources:1,min_terminals:1},
    default_graph: { nodes:[{id:'input',type:source.type,config:{},position:{x:0,y:0}},{id:'processor',type:processor.type,config:{spoken_code:'custom-old',manual_language:'en'},position:{x:450,y:0}},{id:'output',type:output.type,config:{},position:{x:900,y:0}}],
      edges:[{id:'first',source:'input',source_port:'audio',target:'processor',target_port:'audio'},{id:'second',source:'processor',source_port:'audio',target:'output',target_port:'audio'}] }
  } })
}
let app: App | undefined
let root: HTMLDivElement
let rect: DOMRect
let frames: Map<number, FrameRequestCallback>
let frameId: number
async function settle() { for (let i=0;i<35;i++) await Promise.resolve(); await nextTick() }
function draw() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(0)) }
function input(selector = '[data-language-input]') { return root.querySelector<HTMLInputElement>(selector)! }
function key(value: string, element = input(), options: KeyboardEventInit = {}) { const event = new KeyboardEvent('keydown',{ key:value,bubbles:true,cancelable:true,...options }); element.dispatchEvent(event); return event }
async function type(value: string, element = input()) { element.focus(); element.value=value; element.dispatchEvent(new Event('input',{bubbles:true})); await settle() }
function option(code: string) { return document.querySelector<HTMLElement>(`[data-language-code="${code}"]`)! }
function response(value: unknown, status=200) { return { ok: status<400,status,json:async()=>structuredClone(value) } as Response }
function mountCombo(initial='custom-old', extra: object = {}, dialog=false) {
  const props = reactive({ modelValue:initial,languages,allowEmpty:true,disabled:false,hidden:false,...extra })
  const suspended=ref(false), context=ref('p1'), show=ref(true)
  const commit=vi.fn((value:string)=>{props.modelValue=value}), searching=vi.fn()
  app=createApp({setup(){provide(languageSuggestionsKey,{languages:computed(()=>languages),suspended,context});return()=>{
    const combo=h(LanguageCombobox,{...props,'aria-label':'Language','onUpdate:modelValue':commit,onSearching:searching})
    return dialog ? h(CanvasDialog,{open:show.value,title:'Settings',onClose:()=>{show.value=false}},{default:()=>combo}) : combo
  }}})
  app.mount(root)
  return {props,suspended,context,commit,searching,show}
}
function mountEditor() {
  const pinia=createPinia(); setActivePinia(pinia)
  const value=catalog()
  const audios=reactive<Audio[]>([{id:'a1',source:{original_name:'old.wav',language:'en',size_bytes:2}} as Audio])
  const fetcher=vi.fn(async(request:RequestInfo|URL, init?:RequestInit)=>{
    const path=String(request)
    if (path.endsWith('/settings')) return Promise.resolve(response(emptySettings()))
    if(path.endsWith('/capabilities')) return response(value)
    if(path.endsWith('/upload-policy')) return response({allowed_extensions:['wav'],max_upload_bytes:100000})
    if(path.endsWith('/audios') && init?.method==='POST') {
      const body=init.body as FormData
      return response({audio:{id:'a2',source:{original_name:(body.get('file') as File).name,language:body.get('source_language'),size_bytes:2}}})
    }
    if(path.endsWith('/workflow/validate')) return response({valid:true,issues:[]})
    if(path.endsWith('/workflow')) return response({schema_version:"2.0",project_id:'p1',revision:1,workflow_revision:1,created_at:'2026-11-09T00:00:00Z',updated_at:'2026-11-09T00:00:00Z',...value.workflow.default_graph,...(init?.method==='PUT'?JSON.parse(String(init.body)):{})})
    const url=new URL(path,'http://localhost')
    if(url.searchParams.has('limit')) return response({items:[],total:0,limit:Number(url.searchParams.get('limit')),offset:Number(url.searchParams.get('offset'))||0})
    return response({items:[]})
  })
  vi.stubGlobal('fetch',fetcher)
  app=createApp(WorkflowEditor,{projectId:'p1',audioId:'a1',audioName:'old.wav',audios,onBatchUploaded:(audio:Audio)=>audios.push(audio)}).use(pinia)
  app.mount(root)
  return {store:useWorkflowStore(),fetcher}
}
beforeEach(()=>{
  root=document.createElement('div'); document.body.append(root)
  rect={x:100,y:100,left:100,top:100,right:300,bottom:136,width:200,height:36,toJSON:()=>({})}
  vi.spyOn(HTMLInputElement.prototype,'getBoundingClientRect').mockImplementation(()=>rect)
  frames=new Map(); frameId=0
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{frames.set(++frameId,callback);return frameId})
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>frames.delete(id))
})
afterEach(()=>{ app?.unmount(); app=undefined; root.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('language suggestion catalog',()=>{
  it('parses extension metadata without treating suggestions as a value whitelist',()=>{
    const value=readCapabilities(catalog())
    expect(value.languages).toEqual(languages)
    expect(value.nodes[1]!.fields[0]!.suggestionSource).toBe('languages')
    expect(value.nodes[1]!.fields[1]!.suggestionSource).toBeNull()
    expect(value.defaultGraph.nodes[1]!.config.spoken_code).toBe('custom-old')
  })
  it.each(['missing','duplicate','name','alias','source','nonstring','fieldmissing'])('rejects malformed 1.12 metadata: %s',(mutation)=>{
    const value=catalog()
    if(mutation==='missing') delete value.languages
    if(mutation==='duplicate') value.languages.push({...value.languages[0],code:'ZH'})
    if(mutation==='name') value.languages[0].names.en=' Chinese'
    if(mutation==='alias') value.languages[0].aliases=['bad\nname']
    if(mutation==='source') value.workflow.nodes[1].config.fields[0].suggestion_source='guessed'
    if(mutation==='nonstring') value.workflow.nodes[1].config.fields[0].type='integer'
    if(mutation==='fieldmissing') delete value.workflow.nodes[1].config.fields[1].suggestion_source
    expect(()=>readCapabilities(value)).toThrow()
  })
  it('does not infer a suggestion source from old language field names',()=>{
    const value=catalog();value.workflow.schema_version='1.11'; delete value.languages
    expect(()=>readCapabilities(value)).toThrow('workflow.schema_version')
  })
})

describe('LanguageCombobox interaction',()=>{
  it.each(['中文','Chinese','zh','普通话','Mandarin'])('searches backend names, code and aliases: %s',async(query)=>{
    const {commit}=mountCombo();await type(query)
    expect(option('zh')).not.toBeNull();expect(commit).not.toHaveBeenCalled()
    expect(matchesLanguage(languages[2]!,'日本語')).toBe(true)
    expect(root.contains(document.querySelector('[data-language-options]'))).toBe(false)
    option('zh').click();await settle();expect(commit).toHaveBeenLastCalledWith('zh')
    expect(input().value).toBe('zh');expect(document.querySelector('[data-language-options]')).toBeNull()
  })
  it('cancels search on Escape or blur and commits custom codes and empty defaults only explicitly',async()=>{
    const {commit,searching}=mountCombo();await type('中文');key('Escape');await settle()
    expect(input().value).toBe('custom-old');expect(commit).not.toHaveBeenCalled();expect(searching).toHaveBeenLastCalledWith(false)
    await type('English');input().blur();await settle();expect(commit).not.toHaveBeenCalled()
    await type('xx-My_Custom');key('Enter');await settle();expect(commit).toHaveBeenLastCalledWith('xx-My_Custom')
    await type('');key('Enter');await settle();expect(commit).toHaveBeenLastCalledWith('')
  })
  it('navigates candidates from the input and does not submit during IME composition',async()=>{
    const {commit}=mountCombo('');input().focus();key('ArrowUp');key('Enter');await settle();expect(commit).toHaveBeenLastCalledWith('ja')
    input().dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));await type('英语')
    key('Enter',input(),{isComposing:true});expect(commit).toHaveBeenCalledTimes(1)
    input().dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));key('Enter');await settle()
    expect(commit).toHaveBeenLastCalledWith('en');expect(document.activeElement).toBe(input())
  })
  it('does not select on touch pointerdown or scrolling; completed clicks select without taking focus',async()=>{
    const {commit}=mountCombo('');input().focus();await settle()
    const candidate=option('ja')
    candidate.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true,cancelable:true}));await settle()
    const list=document.querySelector<HTMLElement>('[data-language-options]')!;list.scrollTop=60;list.dispatchEvent(new Event('scroll'))
    expect(commit).not.toHaveBeenCalled();expect(document.querySelector('[data-language-options]')).not.toBeNull()
    const mouse=new MouseEvent('mousedown',{bubbles:true,cancelable:true});candidate.dispatchEvent(mouse)
    expect(mouse.defaultPrevented).toBe(true);candidate.click();await settle()
    expect(commit).toHaveBeenLastCalledWith('ja');expect(document.activeElement).toBe(input())
  })
  it('updates translated labels without rewriting codes, search or custom values',async()=>{
    const {props,commit}=mountCombo('custom-old');await type('Nihongo');setLocale('en');await settle()
    expect(input().value).toBe('Nihongo');expect(option('ja').textContent).toContain('Japanese');expect(commit).not.toHaveBeenCalled()
    props.modelValue='server-custom';await settle();expect(input().value).toBe('server-custom');expect(document.querySelector('[data-language-options]')).toBeNull()
  })
  it('follows transformed anchor rectangles, clamps narrow viewports and flips upward without changing its parent',async()=>{
    mountCombo();input().focus();await settle();draw();await settle()
    const list=()=>document.querySelector<HTMLElement>('[data-language-options]')!
    expect(list().style.left).toBe('100px');expect(list().style.top).toBe('140px')
    vi.stubGlobal('innerWidth',390);vi.stubGlobal('innerHeight',400)
    rect={...rect,x:300,y:350,left:300,top:350,right:370,bottom:386,width:70};draw();await settle()
    expect(Number.parseFloat(list().style.left)+Number.parseFloat(list().style.width)).toBeLessThanOrEqual(382)
    expect(Number.parseFloat(list().style.top)).toBeLessThan(350)
    expect(root.querySelector('[data-language-options]')).toBeNull()
  })
  it('closes when a scrolling overflow ancestor completely clips the focused input',async()=>{
    mountCombo();input().focus();await settle();draw();await settle()
    expect(document.querySelector('[data-language-options]')).not.toBeNull()
    const anchor=input(), clip=document.createElement('div');clip.style.overflow='auto'
    anchor.parentElement!.append(clip);clip.append(anchor)
    clip.getBoundingClientRect=()=>({...rect,top:0,bottom:80,height:80})
    clip.dispatchEvent(new Event('scroll'));draw();await settle()
    expect(document.querySelector('[data-language-options]')).toBeNull();expect(frames.size).toBe(0)
  })
  it('closes on disabled, hidden, modal suspension, project changes and unmount, and cancels frame listeners',async()=>{
    const {props,suspended,context}=mountCombo();await type('中文');props.disabled=true;await settle()
    expect(input().disabled).toBe(true);expect(input().value).toBe('custom-old');expect(frames.size).toBe(0)
    props.disabled=false;await settle();input().focus();await settle();props.hidden=true;await settle();expect(document.querySelector('[data-language-options]')).toBeNull()
    props.hidden=false;await settle();input().focus();await settle();suspended.value=true;await settle();expect(input().disabled).toBe(true)
    suspended.value=false;await settle();input().focus();await settle();context.value='p2';await settle();expect(document.querySelector('[data-language-options]')).toBeNull()
    input().blur();input().focus();await settle();app!.unmount();app=undefined
    expect(document.querySelector('[data-language-options]')).toBeNull();expect(frames.size).toBe(0)
  })
  it('closes the candidate layer before a containing modal on Escape',async()=>{
    const {show}=mountCombo('',{},true);await settle();input().focus();await settle()
    key('Escape');await settle();expect(show.value).toBe(true);expect(document.querySelector('[data-language-options]')).toBeNull()
    key('Escape');await settle();expect(show.value).toBe(false)
  })
})

describe('shared language controls in the editor',()=>{
  it('uses metadata for extensions, keeps searches out of history, and preserves code selections through undo, wiring and modal locks',async()=>{
    const {store,fetcher}=mountEditor();await settle();draw();await settle()
    expect(store.capabilitiesError || store.error).toBe('')
    const code=input('[data-field=spoken_code]');expect(code.value).toBe('custom-old')
    expect(input('[data-field=manual_language]').getAttribute('role')).toBeNull()
    const before=JSON.stringify(store.draft), size={...store.getNodeSize(store.draft.nodes[1]!)}
    await type('中文',code);draw();await settle();expect(store.dirty).toBe(false);expect(JSON.stringify(store.draft)).toBe(before)
    expect(store.getNodeSize(store.draft.nodes[1]!)).toEqual(size)
    key('Enter',code);await settle();expect(store.draft.nodes[1]!.config.spoken_code).toBe('zh')
    await type('Nihongo',code);key('Enter',code);await settle();expect(code.value).toBe('ja')
    store.undo();await settle();expect(code.value).toBe('zh')
    store.undo();await settle();expect(code.value).toBe('custom-old');store.redo();await settle();expect(code.value).toBe('zh')
    await store.save();await settle()
    const save=JSON.parse(String(fetcher.mock.calls.find(([,init])=>init?.method==='PUT')![1]!.body))
    expect(save.nodes[1].config.spoken_code).toBe('zh');expect(JSON.stringify(save)).not.toContain('Mandarin')
    store.addNode('parameter_extension');await settle();await type('English',code)
    const parameter=store.draft.nodes.find(node=>node.type==='parameter_extension')!
    store.connect(parameter.id,'processor','language_signal','language_signal');await settle()
    expect(code.disabled).toBe(true);expect(document.querySelector('[data-language-options]')).toBeNull();expect(store.draft.nodes[1]!.config.spoken_code).toBe('zh')
    store.disconnect(store.draft.edges.find(edge=>edge.target_port==='language_signal')!.id);await settle();code.focus();await settle()
    store.saving=true;await settle();expect(code.disabled).toBe(true);expect(document.querySelector('[data-language-options]')).toBeNull()
    store.saving=false;await settle();code.focus();await settle();root.querySelector<HTMLButtonElement>('[data-open-panel=run]')!.click();await settle()
    expect(document.querySelector('[data-language-options]')).toBeNull();expect(root.querySelectorAll('[role=dialog]')).toHaveLength(1)
  })
  it('uses one catalog for default and row languages and protects apply/upload from unconfirmed searches',async()=>{
    const {fetcher}=mountEditor();await settle()
    const mode=root.querySelector<HTMLSelectElement>('[data-collection-mode]')!;mode.value='collection';mode.dispatchEvent(new Event('change',{bubbles:true}));await settle()
    const files=root.querySelector<HTMLInputElement>('[data-batch-file-input]')!
    Object.defineProperty(files,'files',{configurable:true,value:[new File(['ab'],'voice.wav')]});files.dispatchEvent(new Event('change',{bubbles:true}));await settle()
    const defaultCode=input('[data-batch-language]');await type('中文',defaultCode)
    const apply=root.querySelector<HTMLButtonElement>('[data-manifest-apply-language]')!
    expect(apply.disabled).toBe(true);apply.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));apply.click();await settle()
    expect(defaultCode.value).toBe('中文');expect(input('[data-manifest-language]').value).toBe('')
    key('Enter',defaultCode);await settle();apply.click();await settle();expect(input('[data-manifest-language]').value).toBe('zh')
    const row=input('[data-manifest-language]'),upload=root.querySelector<HTMLButtonElement>('[data-batch-upload]')!
    await type('English',row);expect(upload.disabled).toBe(true);key('Escape',row);await settle();expect(row.value).toBe('zh')
    await type('x-Custom',row);key('Enter',row);await settle()
    fetcher.mockImplementationOnce(async()=>response({error:{type:'TEMPORARY_FAILURE',message:'Retry upload'}},500))
    upload.click();await settle()
    const retry=root.querySelector<HTMLButtonElement>('[data-manifest-retry-upload]')!;expect(retry).not.toBeNull()
    await type('English',row);expect(retry.disabled).toBe(true)
    retry.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));retry.click();await settle()
    const uploads=()=>fetcher.mock.calls.filter(([path,init])=>String(path).endsWith('/audios')&&init?.method==='POST')
    expect(uploads()).toHaveLength(1);key('Escape',row);await settle();retry.click();await settle();expect(uploads()).toHaveLength(2)
    const request=fetcher.mock.calls.find(([path,init])=>String(path).endsWith('/audios')&&init?.method==='POST')!
    expect((request[1]!.body as FormData).get('source_language')).toBe('x-Custom');expect(row.disabled).toBe(true)
  })
  it('keeps a dropped single file pending while its source language is only a search',async()=>{
    const {fetcher}=mountEditor();await settle()
    const source=input('[data-resource-language]');await type('中文',source)
    const drop=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(drop,'dataTransfer',{value:{files:[new File(['ab'],'voice.wav')]}})
    root.querySelector('.node-resource-input')!.dispatchEvent(drop);await settle()
    expect(fetcher.mock.calls.some(([path,init])=>String(path).endsWith('/audios')&&init?.method==='POST')).toBe(false)
    key('Enter',source);await settle()
    const retry=[...root.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent===t('app.upload.retry'))!;retry.click();await settle()
    const request=fetcher.mock.calls.find(([path,init])=>String(path).endsWith('/audios')&&init?.method==='POST')!
    expect((request[1]!.body as FormData).get('source_language')).toBe('zh')
  })
})


it.each(['1.16'])('preserves language metadata and strict shape in catalog %s', version => {
  const value = catalog(); value.workflow.schema_version = version
  expect(readCapabilities(value).languages).toEqual(languages)
  value.languages[0].names.extra = 'invalid'
  expect(() => readCapabilities(value)).toThrow()
})


it('parses required debug flags only in catalog 1.15 and preserves old catalog behavior', () => {
  const value = catalog(); value.workflow.schema_version = '1.16'
  value.workflow.nodes.forEach((node: any) => { node.debug_only = false })
  value.workflow.nodes[1].debug_only = true
  const parsed = readCapabilities(value)
  expect(parsed.nodes[1]!.debugOnly).toBe(true)
  expect(parsed.languages).toEqual(languages)
  value.workflow.schema_version = '1.14'
  expect(() => readCapabilities(value)).toThrow('workflow.schema_version')
})
it.each([undefined, null, 'true', 1, {}, []])('rejects an invalid required debug flag in catalog 1.15: %s', flag => {
  const value = catalog(); value.workflow.schema_version = '1.16'
  value.workflow.nodes.forEach((node: any) => { node.debug_only = false })
  value.workflow.nodes[0].debug_only = flag
  expect(() => readCapabilities(value)).toThrow()
})
