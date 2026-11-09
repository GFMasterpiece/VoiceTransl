import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkflowStore } from './stores/workflow'
import { workflowCredentials } from './composables/workflowCredentials'
import { settingsCache, readSettings, loadSettings, saveSettings } from './composables/globalSettings'
import { readCapabilities } from './workflow/capabilities'
const response = (value: unknown) => ({ ok: true, status: 200, json: async () => structuredClone(value) }) as Response
function setup() {
  const caps = readCapabilities({ schema_version:'1.0', workflow:{ schema_version:'1.2', nodes:[{type:'extension',label:'Extension',input_ports:[],output_ports:[],config:{kind:'extension',fields:[{key:'model',type:'string',default:'original'},{key:'credential_ref',type:'string',default:null}]}}],ports:[],constraints:{max_nodes:64,max_edges:64,min_sources:0,max_sources:1,min_terminals:0},default_graph:{nodes:[],edges:[]}}, asr:{providers:[]},export:{formats:[],naming_modes:[]} })
  caps.schemaVersion='1.13'
  caps.nodes[0]!.inlineConnectionInput={baseUrlKey:'endpoint',timeoutKey:'timeout',regionKey:null,credentialRefKey:'credential_ref',providerType:'extension',label:'Extension'}
  const store=useWorkflowStore();store.capabilities=caps;store.beginDraftEditing();store.projectId='p1'
  store.document={schema_version:'1.0',project_id:'p1',revision:1,workflow_revision:1,updated_at:'',nodes:[],edges:[]};store.setCleanBaseline(store.document)
  settingsCache.value={schema_version:'1.0',revision:1,global_fields:[],global:{upload_concurrency:3,max_concurrent:1},node_defaults:{extension:{label:'Extension',fields:[],config:{model:'future-model'},credential_configured:true,credential_version:'frozen-token'}}}
  return {store,credentials:workflowCredentials(store.credentialContext)}
}
beforeEach(()=>setActivePinia(createPinia()))
afterEach(()=>{useWorkflowStore().stop();settingsCache.value=null;vi.unstubAllGlobals()})
describe('global node creation defaults',()=>{
  it('freezes defaults privately through undo/redo and excludes imports and ID reuse',()=>{
    const {store,credentials}=setup();store.addNode('extension');const id=store.selectedNodeId!
    expect(store.selectedNode!.config.model).toBe('future-model');expect(credentials.updates()).toEqual([{node_id:id,operation:'use_default',default_credential_version:'frozen-token'}])
    settingsCache.value!.node_defaults.extension!.credential_version='new-token'
    settingsCache.value!.node_defaults.extension!.config.model='new-model'
    expect(store.selectedNode!.config.model).toBe('future-model')
    expect(JSON.stringify(store.$state)).not.toContain('frozen-token')
    store.undo();expect(credentials.updates()).toEqual([]);store.redo();expect(credentials.updates()[0]).toMatchObject({default_credential_version:'frozen-token'})
    store.removeNode(id);store.addNode('extension');expect(credentials.updates()[0]).toMatchObject({default_credential_version:'new-token'})
    store.replaceGraph({nodes:[{id,type:'extension',config:{model:'imported'},position:{x:0,y:0}}],edges:[]})
    expect(credentials.updates()).toEqual([])
  })
  it('keeps materialized node credentials across save then undo/redo and another save',async()=>{
    const {store,credentials}=setup();store.addNode('extension');const id=store.selectedNodeId!
    store.updateNodeConfig(id,{...store.selectedNode!.config,model:'edited'})
    const bodies:any[]=[]
    vi.stubGlobal('fetch',vi.fn(async (_url:unknown,init:RequestInit)=>{
      const body=JSON.parse(String(init.body));bodies.push(body)
      return response({...store.document,revision:bodies.length+1,workflow_revision:bodies.length+1,nodes:body.nodes.map((node:any)=>({...node,config:{...node.config,credential_ref:'independent-node-ref'}})),edges:body.edges})
    }))
    await store.save();expect(credentials.updates()).toEqual([])
    store.undo();expect(store.selectedNode!.config.credential_ref).toBe('independent-node-ref');expect(credentials.updates()).toEqual([])
    store.redo();expect(store.selectedNode!.config.credential_ref).toBe('independent-node-ref')
    await store.save();expect(bodies[1].credential_updates).toBeUndefined();expect(bodies[1].nodes[0].config.credential_ref).toBe('independent-node-ref')
  })
  it('manual key edits override default intents even across graph history',()=>{
    const {store,credentials}=setup();store.addNode('extension');const id=store.selectedNodeId!
    credentials.set(id,'private-key');store.updateNodeConfig(id,{...store.selectedNode!.config,model:'edited'});store.undo()
    expect(credentials.updates()).toEqual([{node_id:id,operation:'set',api_key:'private-key'}]);expect(JSON.stringify(store.$state)).not.toContain('private-key')
    credentials.clear(id);store.redo();expect(credentials.updates()).toEqual([{node_id:id,operation:'clear'}]);store.undo();store.undo();store.redo();expect(credentials.updates()).toEqual([])
  })
  it('rejects malformed settings metadata without replacing the cache',async()=>{
    setup();vi.stubGlobal('fetch',vi.fn(async()=>response({schema_version:'1.0',revision:2,global_fields:[{key:'upload_concurrency',type:'integer',input_port:null}],global:{},node_defaults:{}})))
    await expect(loadSettings()).rejects.toThrow();expect(settingsCache.value).toBeNull()
    expect(()=>readSettings({schema_version:'2.0'})).toThrow()
  })
})


it('ignores an old GET after a newer GET and settings save when creating nodes', async () => {
  const { store, credentials } = setup()
  const raw = (model: string, token: string, revision: number) => ({
    schema_version: '1.0', revision, global_fields: [], global: {},
    node_defaults: { extension: { label: 'Extension', fields: [{ key: 'model', type: 'string', default: '', input_port: null, suggestion_source: null }], config: { model }, credential_configured: true, credential_version: token } },
  })
  let resolveOld!: (response: Response) => void
  let reads = 0
  vi.stubGlobal('fetch', vi.fn(async (_url: unknown, init?: RequestInit) => {
    if (init?.method === 'PUT') return response(raw('saved-model', 'saved-token', 2))
    if (++reads === 1) return new Promise<Response>(resolve => { resolveOld = resolve })
    return response(raw('recent-model', 'recent-token', 1))
  }))
  const oldRead = loadSettings()
  await loadSettings()
  await saveSettings({})
  resolveOld(response(raw('obsolete-model', 'obsolete-token', 0)))
  await oldRead
  expect(settingsCache.value!.revision).toBe(2)
  store.addNode('extension')
  expect(store.selectedNode!.config.model).toBe('saved-model')
  expect(credentials.updates()[0]).toMatchObject({ default_credential_version: 'saved-token' })
})
