import { emptySettings } from './workflow/fixtures'
import { catalogFixture, fieldFixture, nodeFixture, typeFixture, runFixture } from './workflow/fixtures'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import { useWorkflowStore, workflowErrorLabel, type WorkflowRun } from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry } from './workflow/nodeRegistry'
import { setLocale, t } from './i18n'
import { batchError } from './composables/useBatches'
import CloudWorkflowNotice from './components/CloudWorkflowNotice.vue'
import { workflowCredentials } from './composables/workflowCredentials'
import { copySelection, parseWorkflowFile, preparePaste, serializeWorkflow } from './workflow/editorTransfer'
import { connectionTarget } from './workflow/providerConnections'

function catalog(streaming = false): any {
  const field = (key: string, type = 'string', value: unknown = '', extra: object = {}) => (fieldFixture({ key, label: key, type, default: value, required: false, input_port: null, ...extra }))
  const node = (type: string, fields: any[] = []) => (nodeFixture({ type, label: type, category: null, input_ports: [], output_ports: [], required_input_ports: [], required_inbound: 0, is_source: false, is_parameter_source: false, terminal: false, config: { kind: type, fields } }))
  const source = { ...node('source_extension', [field('single','string',null),field('many','json',null)]), output_ports:['audio'], is_source:true, max_instances:1, removable:false, presentation:{resource_input:{resource_type:'audio', config_key:'single',collection_key:'many'}} }
  const cloud = { ...node('remote_extension', [field('endpoint','string','https://inline.service.test/api'),field('timeout','integer',37,{minimum:1,maximum:3600}),field('region','string',null),field('credential_handle','string',null),field('model','string','model-not-in-a-list'),field('language','string','',{input_port:'language'}),field('additional_parameters','json',{})]), input_ports:['audio','language'],required_input_ports:['audio'],required_inbound:1,output_ports:['transcript'],category:{id:'speech_recognition',label:'语音识别'}, presentation:{inline_connection_input:{provider_type:'extension_service',label:'File service',base_url_key:'endpoint',timeout_key:'timeout',region_key:'region',credential_ref_key:'credential_handle',data_handling:{temporary_upload_retention_hours:17,remote_cancel_pending_only:true}}} }
  const stream = structuredClone(cloud)
  stream.type='qwen_asr_streaming';stream.label='Qwen streaming';stream.config.kind=stream.type
  stream.config.fields=stream.config.fields.filter((field:any)=>field.key!=='region')
  stream.config.fields.find((field:any)=>field.key==='endpoint').default='wss://stream.service.test/api-ws/v1/inference'
  stream.config.fields.find((field:any)=>field.key==='model').default='qwen-audio-3.0-asr-flash-streaming'
  stream.presentation.inline_connection_input={provider_type:'qwen_asr_streaming',label:'Qwen Streaming',base_url_key:'endpoint',timeout_key:'timeout',region_key:null,credential_ref_key:'credential_handle',data_handling:{temporary_upload_retention_hours:null,remote_cancel_pending_only:false,cancel_behavior:'close_stream'}} as any
  const output={...node('text_extension'),input_ports:['transcript'],required_input_ports:['transcript'],required_inbound:1,terminal:true}
  const value={...node('language_value',[field('value','string','custom-lang')]),output_ports:['language'],is_parameter_source:true}
  const config=streaming?{endpoint:'wss://stream.service.test/api-ws/v1/inference',timeout:37,credential_handle:null,model:'qwen-audio-3.0-asr-flash-streaming',language:'',additional_parameters:{}}:{endpoint:'https://inline.service.test/api',timeout:37,region:null,credential_handle:'saved-ref',model:'model-not-in-a-list',language:'',additional_parameters:{}}
  return catalogFixture({schema_version:'1.0',workflow:{schema_version:"1.18",nodes:[source,cloud,output,value,stream],ports:['audio','transcript','language'].map(id=>({id,type_id:id==='language'?'string':id,label:id})),data_types:['audio','transcript','string'].map(id=>(typeFixture({id,label:id,parent_type_id:null,capabilities:[]}))),constraints:{max_nodes:64,max_edges:64,min_sources:1,max_sources:1,min_terminals:1},default_graph:{nodes:[{id:'input',type:'source_extension',config:{},position:{x:0,y:0}},{id:'cloud',type:streaming?'qwen_asr_streaming':'remote_extension',config,position:{x:450,y:0}},{id:'output',type:'text_extension',config:{},position:{x:900,y:0}}],edges:[{id:'first',source:'input',source_port:'audio',target:'cloud',target_port:'audio'},{id:'second',source:'cloud',source_port:'transcript',target:'output',target_port:'transcript'}]}},asr:{providers:[]}})
}
const inlineCatalog = () => catalog()
const streamingCatalog = () => catalog(true)
const response=(value:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>structuredClone(value)}) as Response
async function settle(){for(let index=0;index<40;index++)await Promise.resolve();await nextTick()}
let app:VueApp|undefined
let root:HTMLDivElement|undefined
function setup(options:{inline?:boolean;streaming?:boolean;legacy?:boolean}={}) {
  const state={rejectWorkflow:false,loseSaveResponse:false}
  const initial=catalog(options.streaming)
  let document={...initial.workflow.default_graph,schema_version:"4.0",project_id:'p1',revision:1,workflow_revision:1,updated_at:'2026-11-08T00:00:00Z'}
  if(options.legacy) document.nodes[1].config={provider_profile_id:'old-profile',connection_mode:'profile',api_key_env:'OLD_ENV',model:'old-model',language:'',additional_parameters:{}}
  const savedRequests=new Map<string,any>()
  const audio={id:'a1',source:{original_name:'local.wav',language:'ja',size_bytes:4}}
  const project={id:'p1',name:'Connection test',source:audio.source,audios:[audio],created_at:'2026-11-08',updated_at:'2026-11-08'}
  const fetcher=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(input)
    if (path.endsWith('/settings')) return Promise.resolve(response(emptySettings()))
    if(path.endsWith('/capabilities'))return response(catalog(options.streaming))
    if(path.includes('/projects?'))return response({items:[project],total:1,limit:20,offset:0})
    if(path.endsWith('/projects/p1'))return response(project)
    if(path.endsWith('/projects/p1/audios'))return response({items:[audio]})
    if(path.endsWith('/workflow')){
      if(init?.method==='PUT'){
        const body=JSON.parse(String(init.body))
        if(state.rejectWorkflow)return response({error:{code:'CREDENTIAL_STORAGE_ERROR',message:'Safe storage error'}},503)
        if(body.request_id&&savedRequests.has(body.request_id))return response(savedRequests.get(body.request_id))
        const nodes=body.nodes
        for(const update of body.credential_updates||[])nodes.find((node:any)=>node.id===update.node_id).config.credential_handle=update.operation==='clear'?null:`new-ref-${document.revision+1}`
        document={...document,nodes,edges:body.edges,revision:document.revision+1,workflow_revision:document.workflow_revision+1}
        if(body.request_id)savedRequests.set(body.request_id,structuredClone(document))
        if(state.loseSaveResponse){state.loseSaveResponse=false;throw new TypeError('Failed to fetch')}
      }
      return response(document)
    }
    if(path.endsWith('/upload-policy'))return response({allowed_extensions:['wav'],max_upload_bytes:100})
    if(path.endsWith('/workflow/validate'))return response({valid:true,errors:[]})
    if(path.includes('/workflow-runs?')||path.includes('/batches?'))return response({items:[],total:0,limit:20,offset:0})
    return response({items:[]})
  })
  vi.stubGlobal('fetch',fetcher)
  const pinia=createPinia();setActivePinia(pinia)
  root=window.document.createElement('div');window.document.body.append(root)
  app=createApp(App).use(pinia);app.mount(root)
  return {state,fetcher,workflow:useWorkflowStore(pinia),document,pinia}
}
async function openProject(){await app!._instance!.exposed!.openProject('p1');await settle()}
afterEach(()=>{app?.unmount();root?.remove();app=undefined;root=undefined;vi.restoreAllMocks();vi.unstubAllGlobals()})

describe('inline node credentials', () => {
  it('rejects legacy connection fields visibly without changing the graph and allows deletion and rebuilding', async () => {
    const { workflow, fetcher } = setup({ legacy: true }); await settle(); await openProject()
    expect(workflow.registry!.validateConfig('remote_extension', workflow.draft.nodes[1]!.config)[0]!.code).toBe('INVALID_NODE_CONFIG')
    expect(root!.querySelector('[data-open-profiles], [data-connection-mode], [data-cloud-rebuild]')).toBeNull()
    expect(workflow.draft.nodes[1]!.config.provider_profile_id).toBe('old-profile')
    expect(workflow.dirty).toBe(false)
    expect(root!.querySelector<HTMLButtonElement>('[data-run-workflow]')!.disabled).toBe(true)
    expect(await workflow.startRun()).toBeNull()
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    workflow.removeNode('cloud'); workflow.addNode('remote_extension'); await settle()
    const fresh = workflow.draft.nodes.find((node) => node.type === 'remote_extension')!
    expect(Object.keys(fresh.config)).not.toContain('connection_mode')
    expect(Object.keys(fresh.config)).not.toContain('api_key_env')
    expect(Object.keys(fresh.config)).not.toContain('provider_profile_id')
    expect(workflow.invalidNodeConfig).toBe(false)
    expect(root!.querySelector('[data-credential-input]')).not.toBeNull()
  })
  async function typeKey(value = 'private-test-key') {
    const input = root!.querySelector<HTMLInputElement>('[data-credential-input]')!
    input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await settle()
    return input
  }
  it('remeasures validation errors and late fonts without dragging nodes, changing legacy sizes or losing private input', async () => {
    const fontDescriptor = Object.getOwnPropertyDescriptor(document, 'fonts')
    const fonts = Object.assign(new EventTarget(), { ready: Promise.resolve() })
    Object.defineProperty(document, 'fonts', { value: fonts, configurable: true })
    let extraFontHeight = 0
    const originalRect = HTMLElement.prototype.getBoundingClientRect
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      if (!this.classList.contains('node-config-fields') || !this.closest('.node-measure-host')) return originalRect.call(this)
      const height = (this.querySelector('.node-field-error') ? 1000 : 700) + extraFontHeight
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
      const timeout = root!.querySelector<HTMLInputElement>('[data-connection-field=timeout]')!
      timeout.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, clientX: 20, clientY: 20 }))
      timeout.value = 'invalid'; timeout.dispatchEvent(new Event('input', { bubbles: true })); await settle(); await frame()
      window.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 200, clientY: 200 }))
      const expandedHeight = workflow.getNodeSize(cloud()).height
      expect(expandedHeight).toBeGreaterThan(closedHeight)
      extraFontHeight = 50; fonts.dispatchEvent(new Event('loadingdone')); await frame()
      expect(workflow.getNodeSize(cloud()).height).toBeGreaterThan(expandedHeight)
      timeout.value = '37'; timeout.dispatchEvent(new Event('input', { bubbles: true })); await settle(); await frame()
      expect(workflow.getNodeSize(cloud()).height).toBeLessThan(expandedHeight)
      setLocale('en'); await settle(); await frame()
      expect(input.value).toBe('private-test-key')
      expect(cloud().size).toEqual({ width: 316, height: 300 })
      expect(JSON.stringify(workflow.draft)).toBe(graph)
      expect(root!.querySelector('[data-node-credential-help], [data-node-data-handling], .node-field-description')).toBeNull()
    } finally {
      app?.unmount(); app = undefined
      if (fontDescriptor) Object.defineProperty(document, 'fonts', fontDescriptor)
      else Reflect.deleteProperty(document, 'fonts')
    }
  })
  it('marks key-only edits dirty, guards save/run/leave, and never places keys in Pinia, graph, validation or transfer data', async () => {
    const { workflow, pinia, fetcher } = setup({ inline: true }); await settle(); await openProject()
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
  it('compares explicit saved credential versions and displays historical inline destinations from the frozen graph', async () => {
    const { workflow } = setup({ inline: true }); await settle(); await openProject()
    const frozen = JSON.parse(JSON.stringify(workflow.draft))
    workflow.run = runFixture({ id: 'completed', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed', created_at: '', started_at: '', finished_at: '', cancel_requested: false, error: null, workflow: frozen, nodes: [] })
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
})

describe('independent streaming node connections', () => {
  it('does not infer an old historical target from Profile metadata or current node defaults', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const workflow = useWorkflowStore(pinia); workflow.capabilities = readCapabilities(catalog())
    const frozen = catalog().workflow.default_graph
    frozen.nodes[1].config = { provider_profile_id: 'old-profile', connection_mode: 'profile', endpoint: 'https://unused-inline.test/api', model: 'old-model' }
    const historical = { ...frozen, provider_profiles_snapshot: { 'old-profile': { base_url: 'https://do-not-use.test', display_name: 'Old profile' } } }
    root = document.createElement('div'); document.body.append(root)
    app = createApp(CloudWorkflowNotice, { workflow: historical, audioCount: 1, historical: true }).use(pinia); app.mount(root); await settle()
    expect(root.querySelector('[data-historical-target-missing]')).not.toBeNull()
    expect(root.textContent).not.toContain('do-not-use.test')
    expect(root.textContent).not.toContain('inline.service.test')
    expect(root.textContent).not.toContain('unused-inline.test')
  })
  it.each(['missing', 'legacy-role', 'profile', 'parameter', 'type', 'handling'])('rejects malformed or removed 1.11 connection metadata: %s', (mutation) => {
    const value = catalog(); const node = value.workflow.nodes[1]; const descriptor = node.presentation.inline_connection_input
    if (mutation === 'missing') delete descriptor.provider_type
    if (mutation === 'legacy-role') descriptor.mode_key = 'mode'
    if (mutation === 'profile') node.presentation.provider_profile_input = { config_key: 'provider_profile_id' }
    if (mutation === 'parameter') node.config.fields.find((field: any) => field.key === 'credential_handle').input_port = 'language'
    if (mutation === 'type') node.config.fields.find((field: any) => field.key === 'timeout').type = 'string'
    if (mutation === 'handling') descriptor.data_handling.cancel_behavior = 'pretend_remote_stops'
    expect(() => readCapabilities(value)).toThrow()
  })
  it('renders an inline-only WSS connection without loading profiles, mode, region or env controls', async () => {
    const { workflow, fetcher } = setup({ streaming: true }); await settle(); await openProject()
    const node = root!.querySelector('[data-node-id=cloud]')!
    expect(node.querySelector('[data-connection-mode], [data-open-profiles], [data-connection-field=env], [data-connection-field=region]')).toBeNull()
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/provider-profiles'))).toBe(false)
    expect(node.querySelector<HTMLInputElement>('[data-connection-field=endpoint]')!.value).toBe('wss://stream.service.test/api-ws/v1/inference')
    expect(node.querySelector<HTMLInputElement>('[data-field=model]')!.value).toBe('qwen-audio-3.0-asr-flash-streaming')
    expect(workflow.registry!.validateConfig('qwen_asr_streaming', workflow.draft.nodes[1]!.config)).toEqual([])
    setLocale('en'); await settle()
    expect(node.querySelector('[data-credential-status]')!.textContent).toBe(t('connections.keyNotStoredDirect'))
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
    workflow.run = runFixture({ id: 'done', project_id: 'p1', audio_id: 'a1', workflow_revision: 1, status: 'completed', created_at: '', started_at: '', finished_at: '', cancel_requested: false, error: null, workflow: frozen, nodes: [] })
    expect(workflow.runMatchesDraft).toBe(true)
    workflow.updateNode('cloud', { config: { ...workflow.draft.nodes[1]!.config, endpoint: 'wss://changed.service.test/another-path' } })
    expect(workflow.runMatchesDraft).toBe(false)
    const runButton = [...root!.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes(t('workflow.panelRun')))!
    runButton.click(); await settle()
    const historical = root!.querySelector('[data-inline-provider-snapshot-target=cloud]')!
    expect(historical.textContent).toContain('wss://stream.service.test/api-ws/v1/inference')
    expect(historical.textContent).not.toContain('changed.service.test')
  })
  it('keeps safe Streaming HTTP status and accurate cancellation diagnostics in both locales', () => {
    for (const locale of ['zh-CN', 'en']) {
      setLocale(locale)
      expect(workflowErrorLabel({ type: 'QWEN_STREAM_AUTH_FAILED', message: 'HTTP 401' })).toContain('401')
      expect(batchError({ type: 'QWEN_STREAM_CANCELLED', message: 'safe fallback' })).toBe(t('connections.error_QWEN_STREAM_CANCELLED'))
    }
  })
})
