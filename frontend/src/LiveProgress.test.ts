import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useProjectDetailStore, type Job, type StageDetail, type StageName } from './stores/projectDetail'

const stages: StageName[] = ['preprocess', 'asr', 'alignment', 'translation', 'export']
const source = { original_name: 'voice.wav', language: 'ja', size_bytes: 10, media_type: 'audio/wav' }
const response = (value: unknown) => ({ ok: true, json: async () => structuredClone(value) }) as Response
function makeJob(id = 'j1', audioId = 'a1', status: Job['status'] = 'running', projectId = 'p1'): Job {
  return {
    id, audio_id: audioId, project_id: projectId, status, progress: status === 'completed' ? 1 : .1,
    current_stage: status === 'completed' ? null : 'asr', cancel_requested: false, created_at: '2026-01-01', updated_at: '2026-01-01',
    stages: Object.fromEntries(stages.map((stage) => [stage, { status: status === 'completed' ? 'completed' : stage === 'asr' ? 'running' : 'pending', progress: status === 'completed' ? 1 : 0 }])) as Job['stages'],
  }
}
function detail(stage: StageName, job: Job): StageDetail {
  return { audio_id: job.audio_id!, job_id: job.id, stage, state: job.stages[stage], config: null, result_error: null,
    result: job.stages[stage].status === 'completed' ? { kind: 'transcript', transcript: { project_id: job.project_id, stage, language: 'ja', segments: [{ id: job.id, source_text: `${job.id}-artifact` }] } } : null,
  }
}
class Socket {
  static instances: Socket[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor() { Socket.instances.push(this) }
  close() {}
  emit(type: string, job: Job, data: unknown = {}) {
    this.onmessage?.({ data: JSON.stringify({ type, project_id: job.project_id, audio_id: job.audio_id, job_id: job.id, data }) } as MessageEvent)
  }
}
let store: ReturnType<typeof useProjectDetailStore> | undefined
async function settle() { for (let index = 0; index < 24; index++) await Promise.resolve(); await nextTick() }
function setup(override?: (path: string, init?: RequestInit) => Promise<Response> | undefined) {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  const jobs = { a1: makeJob(), a2: makeJob('j2', 'a2'), b1: makeJob('jb', 'b1', 'running', 'p2') }
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const custom = override?.(path, init)
    if (custom) return custom
    const projectId = path.includes('/p2') ? 'p2' : 'p1'
    const audioId = path.includes('/a2/') ? 'a2' : projectId === 'p2' ? 'b1' : 'a1'
    const job = jobs[audioId]
    if (path.endsWith('/audios')) return Promise.resolve(response({ items: (projectId === 'p2' ? ['b1'] as const : ['a1', 'a2'] as const).map((id) => ({ id, source, job: jobs[id] })) }))
    if (path.endsWith('/job')) return Promise.resolve(response(job))
    if (path.endsWith('/config')) return Promise.resolve(response({ source_language: 'ja', target_language: 'zh-CN' }))
    if (path.includes('/stages/')) return Promise.resolve(response(detail(path.split('/').pop() as StageName, job)))
    if (path.endsWith('/transcript')) return Promise.resolve(response({ transcript: { project_id: projectId, language: 'ja', stage: 'translation', segments: [{ id: job.id, source_text: `${job.id}-latest` }] } }))
    if (path.endsWith('/outputs')) return Promise.resolve(response({ items: [{ name: `${job.id}.srt`, size_bytes: 10, download_url: '/download' }] }))
    return Promise.resolve(response({ id: projectId, name: projectId, source, preset: 'mock', created_at: '', updated_at: '' }))
  })
  vi.stubGlobal('fetch', fetch)
  vi.stubGlobal('WebSocket', Socket)
  store = useProjectDetailStore()
  return { store, jobs, fetch }
}
afterEach(() => { store?.stop(); store = undefined; Socket.instances = []; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('live progress and REST recovery', () => {
  it('updates focused progress and its selected artifact from the real event envelope', async () => {
    const { store, jobs, fetch } = setup()
    await store.load('p1'); store.selectedStage = 'asr'; await settle(); fetch.mockClear()
    Socket.instances[0].emit('stage.progress', jobs.a1, { stage: 'asr', progress: .75 })
    expect(store.job!.stages.asr.progress).toBe(.75)
    expect(store.audios[0].job.stages.asr.progress).toBe(.75)
    jobs.a1.stages.asr.status = 'completed'
    Socket.instances[0].emit('stage.completed', jobs.a1, { stage: 'asr' }); await settle()
    expect(store.stageDetail!.result?.kind).toBe('transcript')
    expect(fetch.mock.calls.filter(([path]) => String(path).includes('/stages/')).map(([path]) => path)).toEqual(['/api/v1/projects/p1/audios/a1/stages/asr'])
  })

  it.each(['unavailable', 'disconnected'])('reconciles completion, nonfocused audios and artifacts with websocket %s', async (mode) => {
    const { store, jobs, fetch } = setup()
    if (mode === 'unavailable') vi.stubGlobal('WebSocket', class { constructor() { throw new Error('offline') } })
    await store.load('p1'); store.selectedStage = 'asr'; await settle()
    if (mode === 'disconnected') { Socket.instances[0].onclose?.(); await settle() }
    store.config!.translation.model = 'unsaved draft'; store.markConfigDirty()
    jobs.a1 = makeJob('j1', 'a1', 'completed'); jobs.a2 = makeJob('j2', 'a2', 'completed')
    fetch.mockClear()
    await vi.advanceTimersByTimeAsync(3000); await settle()
    expect(store.job!.status).toBe('completed')
    expect(store.progressPercent).toBe(100)
    expect(store.audios.map((audio) => audio.job.status)).toEqual(['completed', 'completed'])
    expect(store.stageDetail!.result?.kind).toBe('transcript')
    expect(store.transcript!.segments[0].id).toBe('j1')
    expect(store.outputs[0].name).toBe('j1.srt')
    expect(store.config!.translation.model).toBe('unsaved draft'); expect(store.configDirty).toBe(true)
    expect(fetch.mock.calls.filter(([path]) => String(path).includes('/stages/')).map(([path]) => path)).toEqual(['/api/v1/projects/p1/audios/a1/stages/asr'])
    fetch.mockClear(); await vi.advanceTimersByTimeAsync(3000); await settle()
    expect(fetch.mock.calls.some(([path]) => /\/stages\/|\/transcript$|\/outputs$|\/config$/.test(String(path)))).toBe(false)
  })

  it('recovers a new batch job that starts and completes between POST and GET, rejecting old poll, events and artifacts', async () => {
    let releasePost: (value: Response) => void = () => undefined
    let releaseJob: (value: Response) => void = () => undefined
    let releasePoll: (value: Response) => void = () => undefined
    let releaseTranscript: (value: Response) => void = () => undefined
    let releaseOutputs: (value: Response) => void = () => undefined
    let holdPoll = false, holdJob = false, holdArtifacts = false
    const { store, jobs, fetch } = setup((path) => {
      if (path.endsWith('/audios/run')) return new Promise((resolve) => { releasePost = resolve })
      if (holdPoll && path.endsWith('/audios')) { holdPoll = false; return new Promise((resolve) => { releasePoll = resolve }) }
      if (holdJob && path.endsWith('/job')) { holdJob = false; return new Promise((resolve) => { releaseJob = resolve }) }
      if (holdArtifacts && path.endsWith('/transcript')) return new Promise((resolve) => { releaseTranscript = resolve })
      if (holdArtifacts && path.endsWith('/outputs')) return new Promise((resolve) => { releaseOutputs = resolve })
    })
    await store.load('p1'); store.selectedStage = 'asr'; await settle()
    holdPoll = true; const oldPoll = store.fetchAudios()
    holdArtifacts = true; const oldTranscript = store.fetchTranscript(); const oldOutputs = store.fetchOutputs(); holdArtifacts = false
    const old = structuredClone(jobs.a1)
    const run = store.runSelected()
    jobs.a1 = makeJob('new', 'a1', 'completed')
    Socket.instances[0].emit('job.started', jobs.a1)
    holdJob = true; releasePost(response({ items: [{ audio_id: 'a1', job_id: 'new', status: 'pending' }] })); await settle()
    Socket.instances[0].emit('job.completed', jobs.a1)
    releaseJob(response(jobs.a1)); await run; await settle()
    releasePoll(response({ items: [{ id: 'a1', source, job: old }, { id: 'a2', source, job: jobs.a2 }] })); await oldPoll
    releaseTranscript(response({ transcript: { segments: [{ id: 'stale' }] } })); releaseOutputs(response({ items: [{ name: 'stale.srt' }] })); await Promise.all([oldTranscript, oldOutputs])
    Socket.instances[0].emit('job.snapshot', old, old); Socket.instances[0].emit('job.failed', old, { message: 'stale failure' }); await settle()
    expect(store.job).toMatchObject({ id: 'new', status: 'completed', progress: 1 })
    expect(store.audios[0].job.id).toBe('new')
    expect(store.stageDetail!.job_id).toBe('new')
    expect(store.transcript!.segments[0].id).toBe('new'); expect(store.outputs[0].name).toBe('new.srt')
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('does not regress same-job websocket progress when an older poll finishes', async () => {
    let release: (value: Response) => void = () => undefined
    let hold = false
    const { store, jobs } = setup((path) => hold && path.endsWith('/audios') ? new Promise((resolve) => { release = resolve }) : undefined)
    await store.load('p1')
    const old = structuredClone(jobs.a1)
    hold = true; const poll = store.fetchAudios()
    Socket.instances[0].emit('stage.progress', jobs.a1, { stage: 'asr', progress: .9 })
    release(response({ items: [{ id: 'a1', source, job: old }, { id: 'a2', source, job: makeJob('j2', 'a2', 'completed') }] })); await poll
    expect(store.job!.stages.asr.progress).toBe(.9); expect(store.audios[0].job.stages.asr.progress).toBe(.9)
    expect(store.audios[1].job.status).toBe('completed')
  })

  it('ignores events and repeated close callbacks from a socket replaced during reconnect', async () => {
    const { store, jobs } = setup()
    await store.load('p1')
    const old = Socket.instances[0]
    old.onclose?.(); await vi.advanceTimersByTimeAsync(500); await settle()
    expect(Socket.instances).toHaveLength(2)
    Socket.instances[1].emit('stage.progress', jobs.a1, { stage: 'asr', progress: .8 })
    old.emit('stage.progress', jobs.a1, { stage: 'asr', progress: .1 }); old.onclose?.()
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.job!.stages.asr.progress).toBe(.8)
    expect(Socket.instances).toHaveLength(2)
  })

  it('waits for a pending poll to finish before scheduling another, and stops after navigation', async () => {
    let release: (value: Response) => void = () => undefined
    let hold = false
    const { store, jobs, fetch } = setup((path) => hold && path.endsWith('/p1/audios') ? new Promise((resolve) => { release = resolve }) : undefined)
    await store.load('p1'); fetch.mockClear(); hold = true
    await vi.advanceTimersByTimeAsync(3000); await vi.advanceTimersByTimeAsync(12000)
    expect(fetch.mock.calls.filter(([path]) => String(path).endsWith('/audios'))).toHaveLength(1)
    hold = false
    release(response({ items: [{ id: 'a1', source, job: jobs.a1 }, { id: 'a2', source, job: jobs.a2 }] })); await settle()
    await vi.advanceTimersByTimeAsync(2999)
    expect(fetch.mock.calls.filter(([path]) => String(path).endsWith('/audios'))).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetch.mock.calls.filter(([path]) => String(path).endsWith('/audios'))).toHaveLength(2)
    hold = true; await vi.advanceTimersByTimeAsync(3000)
    await store.load('p2')
    release(response({ items: [{ id: 'a1', source, job: jobs.a1 }] })); await settle()
    expect(store.audios.map((audio) => audio.id)).toEqual(['b1']); expect(store.job!.id).toBe('jb')
    fetch.mockClear(); store.stop(); await vi.advanceTimersByTimeAsync(30000)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('discards a pending list response when switching audio in the same project', async () => {
    let release: (value: Response) => void = () => undefined
    let hold = false
    const { store, jobs } = setup((path) => hold && path.endsWith('/audios') ? new Promise((resolve) => { release = resolve }) : undefined)
    await store.load('p1'); hold = true; const poll = store.fetchAudios(); await store.selectAudio('a2')
    release(response({ items: [{ id: 'a1', source, job: jobs.a1 }, { id: 'a2', source, job: makeJob('stale', 'a2') }] })); await poll
    expect(store.job!.id).toBe('j2'); expect(store.audios[1].job.id).toBe('j2')
  })
})
