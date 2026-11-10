import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createApp, h, nextTick } from 'vue'
import { useProjectMedia, type AudioGroup } from './composables/useProjectMedia'
import { useBatchSession } from './composables/useBatchSession'
import { useBatches } from './composables/useBatches'
import { emptySettings } from './workflow/fixtures'
import WorkflowEditor from './components/WorkflowEditor.vue'
import WorkflowPanels from './components/WorkflowPanels.vue'
import { measureNodeContent } from './workflow/measureNode'
import { frozenSourceGroup, sourceScopeLabel } from './stores/workflow'
import { useBatchManifest } from './composables/useBatchManifest'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry } from './workflow/nodeRegistry'
import { serializeWorkflow, parseWorkflowFile } from './workflow/editorTransfer'
import { useWorkflowStore, workflowFieldsHeight, workflowNodeHeight } from './stores/workflow'
import ProjectMediaDialog from './components/ProjectMediaDialog.vue'
import NodeGroupInput from './components/NodeGroupInput.vue'
import capabilities from './workflow/groupCapabilities.fixture.json'
import { setLocale } from './i18n'

const g1 = 'grp_' + '1'.repeat(32), g2 = 'grp_' + '2'.repeat(32)
const group = (id = g1, revision = 1): AudioGroup => ({ schema_version:'1.0', id, revision, name:id === g1 ? 'A' : 'B', audio_ids:['a1','a2'], background:'背景', mode:'independent', created_at:'2026-11-10', updated_at:'2026-11-10' })
const page = <T>(items: T[], offset = 0, total = items.length) => ({ items, total, limit:20, offset })
const response = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status, headers:{ 'Content-Type':'application/json' } })
function deferred<T>() { let resolve!: (value:T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
const sessions: Array<{dispose: () => void}> = []
let unmount: (() => void) | undefined
beforeEach(() => { setActivePinia(createPinia()); setLocale('zh-CN') })
afterEach(() => { unmount?.(); unmount = undefined; for (const value of sessions.splice(0)) value.dispose(); vi.unstubAllGlobals(); document.body.innerHTML = '' })
function media() { const value = useProjectMedia('p1'); sessions.push(value); return value }
async function settle() { for (let index=0; index<8; index++) await new Promise(done => setTimeout(done, 0)); await nextTick() }
function fetchGroups() { return vi.fn(async (url: string, init?: RequestInit) => {
  if (url.includes('/audios?')) return response(page([]))
  if (url.includes('/audio-groups?')) return response(page([{ ...group(), audio_count:2 }]))
  if (init?.method === 'PUT') return response({ ...group(g1,2), ...JSON.parse(String(init.body)) })
  if (init?.method === 'DELETE') return response(null,204)
  return response(group(url.includes(g2) ? g2 : g1))
}) }

describe('project media revisions and request ordering', () => {
  it('keeps selection and groups independent of graph revisions, with paginated media requests', async () => {
    const fetcher = fetchGroups(); vi.stubGlobal('fetch', fetcher)
    const value = media(), store = useWorkflowStore()
    await value.refresh(); await value.loadAudios(20)
    value.selected.value = ['a2','a1']; value.createDraft(); value.draft.value.name = 'Ordered'
    expect(value.draft.value.audio_ids).toEqual(['a2','a1']); expect(value.dirty.value).toBe(true)
    value.move('a1',-1); expect(value.draft.value.audio_ids).toEqual(['a1','a2'])
    expect(store.document).toBe(null); expect(store.draft.nodes).toEqual([])
    expect(fetcher.mock.calls.some(([url]) => url.includes('/audios?limit=20&offset=20'))).toBe(true)
  })
  it('does not let slow A replace a selected and edited B', async () => {
    const slow = deferred<Response>()
    vi.stubGlobal('fetch', vi.fn((url:string) => url.endsWith(g1) ? slow.promise : Promise.resolve(response(group(g2)))))
    const value = media(), first = value.edit(g1)
    await value.edit(g2); value.draft.value.background = 'B editing'
    slow.resolve(response(group(g1))); await first
    expect(value.draft.value.id).toBe(g2); expect(value.draft.value.background).toBe('B editing')
  })
  it('keeps a newly created or changed draft while a detail load is pending', async () => {
    const slow = deferred<Response>(); vi.stubGlobal('fetch', () => slow.promise)
    const value = media(), pending = value.edit(g1)
    value.createDraft(); value.draft.value.name = 'New'
    slow.resolve(response(group(g1))); await pending
    expect(value.draft.value.id).toBe(''); expect(value.draft.value.name).toBe('New')
  })
  it('does not replace typing that began before the pending selection finished', async () => {
    const slow = deferred<Response>(); vi.stubGlobal('fetch', () => slow.promise)
    const value = media(); value.createDraft()
    const pending = value.edit(g1); value.draft.value.background = 'typed during GET'
    slow.resolve(response(group(g1))); await pending
    expect(value.draft.value.background).toBe('typed during GET'); expect(value.draft.value.id).toBe('')
  })
  it('retains revision 2 when an earlier detail GET returns revision 1 after save', async () => {
    const slow = deferred<Response>(); let reads = 0
    vi.stubGlobal('fetch', vi.fn((url:string, init?:RequestInit) => {
      if (init?.method === 'PUT') return Promise.resolve(response({ ...group(g1,2), name:'saved' }))
      if (url.includes('/audio-groups?')) return Promise.resolve(response(page([])))
      return ++reads === 1 ? Promise.resolve(response(group(g1))) : slow.promise
    }))
    const value = media(); await value.edit(g1)
    const old = value.loadGroup(g1); value.draft.value.name = 'saved'; await value.save()
    slow.resolve(response(group(g1))); await old
    expect(value.groups.value[g1]!.revision).toBe(2); expect(value.draft.value.revision).toBe(2)
  })
  it('never resurrects a deleted group from an older detail GET', async () => {
    const slow = deferred<Response>(); let reads = 0
    vi.stubGlobal('fetch', vi.fn((url:string, init?:RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(response(null,204))
      if (url.includes('/audio-groups?')) return Promise.resolve(response(page([])))
      return ++reads === 1 ? Promise.resolve(response(group())) : slow.promise
    }))
    const value = media(); await value.edit(g1); const old = value.loadGroup(g1)
    await value.remove(g1); slow.resolve(response(group())); await old
    expect(value.groups.value[g1]).toBeUndefined(); expect(value.summaries.value[g1]).toBeUndefined()
  })
  it('counts concurrent reads and ignores responses after project disposal', async () => {
    const first = deferred<Response>(), second = deferred<Response>()
    vi.stubGlobal('fetch', (url:string) => url.includes('/audios?') ? first.promise : second.promise)
    const value = media(), a = value.loadAudios(), b = value.loadGroups()
    first.resolve(response(page([]))); await a; expect(value.loading.value).toBe(true)
    value.dispose(); second.resolve(response(page([{ ...group(), audio_count:2 }]))); await b
    expect(value.loading.value).toBe(false); expect(value.groupPage.value.items).toEqual([])
  })
  it('retains POST uncertainty and never automatically resends create', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('connection lost')); vi.stubGlobal('fetch',fetcher)
    const value = media(); value.createDraft(); value.draft.value.name = 'Draft'
    await value.save(); expect(value.uncertain.value).toBe(true)
    await value.save(); expect(fetcher).toHaveBeenCalledTimes(1); expect(value.draft.value.name).toBe('Draft')
  })
  it('preserves local edits on revision conflict and exposes explicit reload', async () => {
    vi.stubGlobal('fetch', vi.fn((url:string, init?:RequestInit) => Promise.resolve(init?.method === 'PUT' ? response({ error:{ code:'GROUP_REVISION_CONFLICT', message:'conflict' } },409) : response(group()))))
    const value = media(); await value.edit(g1); value.draft.value.background = 'local'
    await value.save(); expect(value.dirty.value).toBe(true); expect(value.uncertain.value).toBe(false); expect(value.draft.value.background).toBe('local')
    await value.edit(g1); expect(value.draft.value.background).toBe('背景')
  })
})

describe('capability-driven group selection and source execution', () => {
  it('reads real 1.19 metadata and strips group references on portable copy', () => {
    const registry = createNodeRegistry(readCapabilities(capabilities))
    expect(registry.capabilities.execution?.scope).toBe('source_branch')
    const source = registry.kind('audio_input')!
    expect(source.resourceInput?.groupKey).toBe('group_id')
    expect(registry.createDefaultConfig('audio_input',true)).toEqual({group_id:''})
    const graph = parseWorkflowFile(serializeWorkflow({nodes:[{id:'input',type:'audio_input',config:{group_id:g1},position:{x:0,y:0}}],edges:[]}, registry), registry)
    expect(graph.nodes[0]!.config.group_id).toBe('')
  })
  it('keeps page-external group selections visible and translated in compact controls', async () => {
    const value = media(); value.groups.value[g2] = group(g2); value.summaries.value[g2] = { ...group(g2), audio_count:2 }
    const host = document.createElement('div'); document.body.append(host)
    const app = createApp({ render: () => h(NodeGroupInput,{ value:g2, groups:value.summaries.value,details:value.groups.value,source:true,locked:false,runReason:'' }) }); app.mount(host); unmount=()=>app.unmount()
    expect(host.querySelector('option:checked')?.textContent).toContain('B'); expect(host.textContent).toContain('运行此输入')
    setLocale('en'); await nextTick(); expect(host.textContent).toContain('Run this input')
  })
  it('uses one target request for the whole canvas and only selected-scope group revisions', async () => {
    const store=useWorkflowStore(); store.capabilities=readCapabilities(capabilities); store.projectId='p1'; store.audioId='a1'
    const nodes=[{id:'s1',type:'audio_input',config:{group_id:g1},position:{x:0,y:0}},{id:'o1',type:'audio_output',config:{},position:{x:300,y:0}},{id:'s2',type:'audio_input',config:{group_id:g2},position:{x:0,y:300}},{id:'o2',type:'audio_output',config:{},position:{x:300,y:300}}]
    const edges=[{id:'e1',source:'s1',source_port:'audio',target:'o1',target_port:'audio'},{id:'e2',source:'s2',source_port:'audio',target:'o2',target_port:'audio'}]
    store.document={ schema_version:'4.0',project_id:'p1',revision:1,workflow_revision:1,updated_at:'now',nodes,edges }; store.draft=JSON.parse(JSON.stringify({nodes,edges})); store.discardDraft()
    const library=media(); library.groups.value[g1]=group(); library.groups.value[g2]=group(g2,3)
    const batch={ id:'bat1',project_id:'p1',status:'completed',workflow_revision:1,created_at:'now',started_at:null,finished_at:null,cancel_requested:false,retry_of:null,total:1,counts:{queued:0,running:0,completed:1,partial:0,failed:0,cancelled:0,interrupted:0,blocked:0},error:null }
    const fetcher=vi.fn(async (url:string, init?:RequestInit) => init?.method==='POST' ? response(batch) : url.includes('/items?') ? response(page([])) : url.includes('/batches?') ? response(page([batch])) : response({...batch,workflow:{schema_version:'4.0',nodes,edges}}));vi.stubGlobal('fetch',fetcher)
    const session=useBatchSession('p1',()=>{},()=>[],()=>false,library); sessions.push(session)
    library.groupErrors.value[g2]='other group broken'
    expect(session.reason('s1')).toBe(''); expect(session.startReason.value).not.toBe('')
    await session.start('s1')
    const posts=fetcher.mock.calls.filter(([,init])=>init?.method==='POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(String(posts[0]![1]!.body))).toMatchObject({target:{kind:'source',node_id:'s1'},expected_group_revisions:{[g1]:1}})
    expect(JSON.parse(String(posts[0]![1]!.body))).not.toHaveProperty('audio_ids')
    delete library.groupErrors.value[g2]; await session.start()
    const canvas=JSON.parse(String(fetcher.mock.calls.filter(([,init])=>init?.method==='POST')[1]![1]!.body))
    expect(canvas.target).toEqual({kind:'canvas'});expect(canvas.expected_group_revisions).toEqual({[g1]:1,[g2]:3})
    library.createDraft();library.draft.value.name='unsaved';expect(session.reason('s1')).not.toBe('')
  })
})


describe('media dialog protects separate drafts and Files', () => {
  it('preserves group text and selected Files across locale changes and requires explicit discard to close', async () => {
    vi.stubGlobal('fetch', fetchGroups())
    const library=media(), manifest=useBatchManifest('p1',()=>{}); sessions.push(manifest)
    library.createDraft(); library.draft.value.name='new'; library.draft.value.background='custom background'
    manifest.language.value='ja'; manifest.add([new File(['RIFF'],'audio.wav',{type:'audio/wav'})],false)
    const close=vi.fn(), host=document.createElement('div'); document.body.append(host)
    const app=createApp({ render:()=>h(ProjectMediaDialog,{media:library,manifest,audios:[],locked:false,onClose:close}) }); app.mount(host);unmount=()=>app.unmount()
    setLocale('en');await nextTick()
    expect(library.draft.value.background).toBe('custom background');expect(manifest.pendingLocal.value).toBe(true)
    host.querySelector<HTMLElement>('.canvas-dialog-body')!.scrollTop=400
    host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();await nextTick()
    expect(close).not.toHaveBeenCalled();expect(host.querySelector('[data-media-confirm-discard]')).not.toBe(null)
    await nextTick()
    expect(document.activeElement).toBe(host.querySelector('[data-media-keep-editing]'))
    expect(host.querySelector<HTMLElement>('.canvas-dialog-body')!.scrollTop).toBe(0)
    const editor=host.querySelector<HTMLElement>('[data-media-editor]')!
    expect(editor.hasAttribute('inert')).toBe(true);expect(editor.style.display).toBe('none')
    host.querySelector<HTMLButtonElement>('[data-media-keep-editing]')!.click();await nextTick()
    expect(host.querySelector('[data-media-confirm-view]')).toBe(null)
    expect(editor.hasAttribute('inert')).toBe(false)
    expect(library.draft.value.background).toBe('custom background');expect(manifest.pendingLocal.value).toBe(true)
    host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();await nextTick();await nextTick()
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await nextTick();await nextTick()
    expect(host.querySelector('[data-media-confirm-view]')).toBe(null);expect(close).not.toHaveBeenCalled()
    expect(library.draft.value.background).toBe('custom background');expect(manifest.pendingLocal.value).toBe(true)
    host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();await nextTick()
    host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();await nextTick()
    expect(host.querySelector('[data-media-confirm-view]')).toBe(null);expect(close).not.toHaveBeenCalled()
    host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click();await nextTick()
    host.querySelector<HTMLButtonElement>('[data-media-confirm-discard]')!.click();await nextTick()
    expect(close).toHaveBeenCalledTimes(1);expect(manifest.pendingLocal.value).toBe(false)
  })
  it('shows group member order and never writes workflow when saving group background', async () => {
    const fetcher=fetchGroups();vi.stubGlobal('fetch',fetcher)
    const library=media(), manifest=useBatchManifest('p1',()=>{});sessions.push(manifest)
    await library.edit(g1)
    const host=document.createElement('div');document.body.append(host)
    const app=createApp({render:()=>h(ProjectMediaDialog,{media:library,manifest,audios:[],locked:false})});app.mount(host);unmount=()=>app.unmount()
    host.querySelector<HTMLButtonElement>('[data-media-groups]')!.click();await nextTick()
    const field=host.querySelector<HTMLTextAreaElement>('[data-group-background]')!;field.value='new background';field.dispatchEvent(new Event('input',{bubbles:true}));await nextTick()
    library.move('a2',-1);host.querySelector<HTMLButtonElement>('[data-group-save]')!.click();await settle()
    const mutations=fetcher.mock.calls.filter(([,init])=>init?.method==='PUT')
    expect(mutations).toHaveLength(1);expect(mutations[0]![0]).toContain('/audio-groups/')
    expect(JSON.parse(String(mutations[0]![1]!.body))).toMatchObject({expected_revision:1,background:'new background',audio_ids:['a2','a1']})
    expect(library.dirty.value).toBe(false)
  })
})


describe('real editor library upload and pending target requests', () => {
  it('opens the library with active language search, confirms default and row language codes, and uploads without rebinding any source', async () => {
    const documentValue={schema_version:'4.0',project_id:'p1',revision:0,workflow_revision:0,updated_at:'2026-11-10',...capabilities.workflow.default_graph}
    const uploaded=vi.fn()
    const fetcher=vi.fn(async (url:string, init?:RequestInit) => {
      if(url.endsWith('/capabilities'))return response(capabilities)
      if(url.endsWith('/settings'))return response(emptySettings())
      if(url.endsWith('/workflow'))return response(documentValue)
      if(url.endsWith('/upload-policy'))return response({allowed_extensions:['wav'],max_upload_bytes:1000000,upload_concurrency:3})
      if(url.endsWith('/audios') && init?.method==='POST')return response({audio:{id:'a3',source:{path:'source/a.wav',original_name:'audio.wav',language:'en',size_bytes:4,media_type:'audio/wav'}}},201)
      return response(page([]))
    });vi.stubGlobal('fetch',fetcher)
    const pinia=createPinia();setActivePinia(pinia)
    const host=document.createElement('div');document.body.append(host)
    const app=createApp(WorkflowEditor,{projectId:'p1',audioId:null,audioName:'',audios:[],onBatchUploaded:uploaded}).use(pinia);app.mount(host);unmount=()=>app.unmount();await settle()
    const store=useWorkflowStore(), original=JSON.stringify(store.draft)
    host.querySelector<HTMLButtonElement>('[data-project-media-open]')!.click();await settle()
    const language=host.querySelector<HTMLInputElement>('[data-batch-language]')!
    expect(language.disabled).toBe(false)
    language.focus();language.value='日本';language.dispatchEvent(new Event('input',{bubbles:true}));await nextTick()
    const option=document.querySelector<HTMLElement>('[data-language-code="ja"][data-language-kind="language"]')!
    expect(option).not.toBe(null);option.click();await nextTick();expect(language.value).toBe('ja')
    const picker=host.querySelector<HTMLInputElement>('[data-batch-file-input]')!
    Object.defineProperty(picker,'files',{configurable:true,value:[new File(['RIFF'],'audio.wav',{type:'audio/wav'})]});picker.dispatchEvent(new Event('change',{bubbles:true}));await settle()
    const row=host.querySelector<HTMLInputElement>('[data-manifest-language]')!
    expect(row.disabled).toBe(false);row.focus();row.value='English';row.dispatchEvent(new Event('input',{bubbles:true}));await nextTick()
    document.querySelector<HTMLElement>('[data-language-code="en"][data-language-kind="language"]')!.click();await nextTick()
    expect(row.value).toBe('en')
    const button=host.querySelector<HTMLButtonElement>('[data-batch-upload]')!;expect(button.disabled).toBe(false);button.click();await settle()
    expect(uploaded).toHaveBeenCalledTimes(1)
    const post=fetcher.mock.calls.find(([url,init])=>url.endsWith('/audios') && init?.method==='POST')!
    expect((post[1]!.body as FormData).get('source_language')).toBe('en')
    expect(JSON.stringify(store.draft)).toBe(original);expect(store.dirty).toBe(false)
    expect(host.querySelector<HTMLSelectElement>('[data-node-group] option')?.textContent).not.toContain('当前音频')
  })
  it('restores an unconfirmed canvas target with its exact group revisions and request id', async () => {
    const fetcher=vi.fn(async (_url:string,init?:RequestInit)=>{if(init?.method==='POST')throw new TypeError('lost');return response(page([]))});vi.stubGlobal('fetch',fetcher)
    const first=useBatches('p1');sessions.push(first)
    await first.createTarget(3,{kind:'canvas'},'a1',{[g1]:7,[g2]:9})
    const pending=JSON.parse(JSON.stringify(first.pending.value));expect(pending.body.target).toEqual({kind:'canvas'})
    const second=useBatches('p1');sessions.push(second);expect(second.pending.value).toEqual(pending)
    await second.retryPending()
    const bodies=fetcher.mock.calls.filter(([,init])=>init?.method==='POST').map(([,init])=>JSON.parse(String(init!.body)))
    expect(bodies[0]).toEqual(bodies[1]);expect(bodies[1].expected_group_revisions).toEqual({[g1]:7,[g2]:9})
  })
})


describe('frozen source provenance', () => {
  it('keeps same-audio history selections distinct and uses server frozen names rather than current graph or library names', async () => {
    const pinia=createPinia();setActivePinia(pinia);const store=useWorkflowStore()
    store.capabilities=readCapabilities(capabilities);store.projectId='p1';store.audioId='same-audio'
    store.document={schema_version:'4.0',project_id:'p1',revision:2,workflow_revision:2,updated_at:'now',...JSON.parse(JSON.stringify(capabilities.workflow.default_graph))};store.discardDraft()
    store.history=[{id:'run-A',source_node_id:'source_A',source_group_name:'Frozen A',workflow_revision:1,status:'completed',created_at:'2026-11-10',finished_at:null,error:null},{id:'run-B',source_node_id:'source_B',source_group_name:'Frozen B',workflow_revision:1,status:'completed',created_at:'2026-11-10',finished_at:null,error:null},{id:'old',workflow_revision:1,status:'completed',created_at:'2026-11-10',finished_at:null,error:null}]
    const library=media();library.groups.value[g1]={...group(),name:'Current renamed group'}
    const load=vi.spyOn(store,'loadRun').mockResolvedValue(undefined)
    const host=document.createElement('div');document.body.append(host)
    const app=createApp({render:()=>h(WorkflowPanels,{panel:'run'})}).use(pinia);app.mount(host);unmount=()=>app.unmount();await nextTick()
    const labels=[...host.querySelectorAll('[data-history-source]')].map(element=>element.textContent)
    expect(labels).toHaveLength(2);expect(labels[0]).toContain('Frozen A');expect(labels[0]).toContain('source_A');expect(labels[1]).toContain('Frozen B')
    expect(host.textContent).not.toContain('Current renamed group')
    const rows=host.querySelectorAll<HTMLButtonElement>('.workflow-history-row');rows[1]!.click();expect(load).toHaveBeenCalledWith('run-B')
  })
  it('derives run and rerun source labels exclusively from frozen workflow snapshots', () => {
    const registry=createNodeRegistry(readCapabilities(capabilities))
    const snapshot={schema_version:'4.0',nodes:[{id:'source_A',type:'audio_input',config:{group_id:g1},position:{x:0,y:0}}],edges:[],group_snapshots:{[g1]:group()}}
    const library=media();library.groups.value[g1]={...group(g1,9),name:'Current name'}
    const frozen=frozenSourceGroup(snapshot,'source_A',registry)
    expect(frozen?.name).toBe('A');expect(frozen?.revision).toBe(1)
    expect(sourceScopeLabel('source_A',frozen?.name)).toContain('A');expect(sourceScopeLabel('source_A',frozen?.name)).not.toContain('Current name')
    expect(sourceScopeLabel(undefined,undefined)).toBe('')
  })
})


describe('group selection confirmation view', () => {
  it('cancels a pending group switch without requesting or replacing it, and switches only after explicit discard', async () => {
    const fetcher=fetchGroups();vi.stubGlobal('fetch',fetcher)
    const library=media(),manifest=useBatchManifest('p1',()=>{});sessions.push(manifest)
    await library.edit(g1);library.groupPage.value=page([{...group(g2),audio_count:2}]);library.draft.value.background='unsaved A'
    const host=document.createElement('div');document.body.append(host)
    const app=createApp({render:()=>h(ProjectMediaDialog,{media:library,manifest,audios:[],locked:false})});app.mount(host);unmount=()=>app.unmount()
    host.querySelector<HTMLButtonElement>('[data-media-groups]')!.click();await nextTick()
    host.querySelector<HTMLButtonElement>(`[data-media-group="${g2}"]`)!.click();await nextTick();await nextTick()
    expect(document.activeElement).toBe(host.querySelector('[data-media-keep-editing]'))
    expect(fetcher.mock.calls.some(([url])=>url.endsWith(g2))).toBe(false)
    document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await nextTick()
    expect(library.draft.value.background).toBe('unsaved A');expect(library.draft.value.id).toBe(g1)
    expect(host.querySelector('[data-media-confirm-view]')).toBe(null)
    host.querySelector<HTMLButtonElement>(`[data-media-group="${g2}"]`)!.click();await nextTick()
    host.querySelector<HTMLButtonElement>('[data-media-confirm-discard]')!.click();await settle()
    expect(library.draft.value.id).toBe(g2);expect(library.draft.value.background).toBe('背景')
    expect(fetcher.mock.calls.filter(([url])=>url.endsWith(g2))).toHaveLength(1)
  })
})


describe('real inline service node layout', () => {
  it.each(['llm_service','asr_service'])('keeps %s connection controls visible with a nonzero intrinsic fallback and secret-free measured clone', async (type) => {
    const service={id:'service',type,config:{},position:{x:300,y:300}}
    const nodes=[...capabilities.workflow.default_graph.nodes,service,{id:'processor',type:'qwen_asr',config:{},position:{x:500,y:300}}]
    const graph={schema_version:'4.0',project_id:'p1',revision:1,workflow_revision:1,updated_at:'now',nodes,edges:capabilities.workflow.default_graph.edges}
    vi.stubGlobal('fetch',vi.fn(async(url:string)=>url.endsWith('/capabilities')?response(capabilities):url.endsWith('/settings')?response(emptySettings()):url.endsWith('/workflow')?response(graph):response(page([]))))
    const pinia=createPinia();setActivePinia(pinia)
    const host=document.createElement('div');document.body.append(host)
    const app=createApp(WorkflowEditor,{projectId:'p1',audioId:null,audioName:'',audios:[]}).use(pinia);app.mount(host);unmount=()=>app.unmount();await settle()
    const store=useWorkflowStore(), registry=store.registry!
    const group=host.querySelector<SVGGElement>('[data-node-id="service"]')!
    const password=group.querySelector<HTMLInputElement>('input[type="password"]')!
    expect(password).not.toBe(null)
    const foreign=password.closest('foreignObject') as SVGElement
    expect(foreign.style.display).not.toBe('none')
    expect((password.closest('.node-body-fields') as HTMLElement).style.display).not.toBe('none')
    const fields=workflowFieldsHeight(type,registry,'service',[],service.config)
    expect(fields).toBeGreaterThanOrEqual(156)
    expect(Number(foreign.getAttribute('height'))).toBeGreaterThanOrEqual(fields)
    expect(workflowFieldsHeight('qwen_asr',registry)).toBe(0)
    expect(workflowNodeHeight('qwen_asr',registry)).toBeLessThan(workflowNodeHeight(type,registry))
    password.value='private-key-must-not-enter-clone'
    const bounds=vi.spyOn(HTMLElement.prototype,'getBoundingClientRect').mockImplementation(function(this:HTMLElement) {
      const height=this.classList.contains('node-config-fields') && !this.closest('[style*="display: none"]') ? 260 : 0
      return { x:0,y:0,left:0,top:0,right:336,bottom:height,width:336,height,toJSON:()=>({}) } as DOMRect
    })
    const append=document.body.append.bind(document.body)
    const connected=vi.spyOn(document.body,'append').mockImplementation((...elements)=>{
      const clone=elements[0] as HTMLElement
      if(clone.classList.contains('node-measure-host')){
        expect(clone.querySelector('[data-node-connection]')).not.toBe(null)
        expect(clone.querySelector<HTMLInputElement>('input[type="password"]')!.value).toBe('')
      }
      append(...elements)
    })
    const measured=measureNodeContent(group,{width:336,height:92,fields,resource:0,preview:0,bodyY:62})
    expect(measured.fields).toBeGreaterThanOrEqual(260);expect(measured.height).toBeGreaterThanOrEqual(334)
    connected.mockRestore();bounds.mockRestore()
  })
})
