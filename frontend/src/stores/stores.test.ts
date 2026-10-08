import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectDetailStore, type Job, type StageName } from './projectDetail'
import { useProjectsStore, type Project } from './projects'

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response
}

const project: Project = {
  id: 'project-1',
  name: 'Demo project',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  preset: 'mock',
  source: {
    original_name: 'demo.wav',
    language: 'en',
    size_bytes: 123,
    media_type: 'audio/wav',
  },
}

const stageNames: StageName[] = ['preprocess', 'asr', 'alignment', 'translation', 'export']

function makeJob(overrides: Partial<Job> = {}): Job {
  const stages = Object.fromEntries(
    stageNames.map((name) => [
      name,
      {
        status: name === 'asr' ? 'running' : 'pending',
        progress: name === 'asr' ? 0.25 : 0,
      },
    ]),
  ) as Job['stages']
  return {
    id: 'job-1',
    project_id: project.id,
    status: 'running',
    current_stage: 'asr',
    progress: 0.05,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    stages,
    cancel_requested: false,
    ...overrides,
  }
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  readonly url: string
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    // Deliberately do not call onclose: tests can stop a store without scheduling reconnects.
  }
}

const detailStores: ReturnType<typeof useProjectDetailStore>[] = []

afterEach(() => {
  detailStores.forEach((store) => store.stop())
  detailStores.length = 0
  FakeWebSocket.instances.length = 0
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('audio workspace scope and switching', () => {
  const audioIds = ['aud_primary', 'aud_second', 'aud_third']
  const audioItems = () => audioIds.map((id, index) => ({ id, source: { ...project.source, original_name: `${id}.wav`, language: index === 2 ? 'ko' : 'en' }, job: makeJob({ id: `job-${id}`, audio_id: id, status: 'pending', current_stage: null }) }))

  function setup(override?: (path: string, init?: RequestInit) => Promise<Response> | undefined) {
    setActivePinia(createPinia())
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost').pathname
      const custom = override?.(path, init)
      if (custom) return custom
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: audioItems() }))
      const audio = audioItems().find((entry) => path.includes(`/audios/${entry.id}/`)) || audioItems()[0]
      if (path.endsWith('/config')) return Promise.resolve(jsonResponse(init?.method === 'PUT' ? JSON.parse(String(init.body)) : { source_language: audio.source.language, target_language: 'zh-CN' }))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(audio.job))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, language: audio.source.language, stage: 'asr', segments: [{ id: audio.id, source_text: audio.id }] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [{ name: `${audio.id}.srt`, size_bytes: 3, download_url: `${path}/download` }] }))
      if (path.endsWith('/audios/run')) return Promise.resolve(jsonResponse({ items: JSON.parse(String(init?.body)).audio_ids.map((audio_id: string) => ({ audio_id, job_id: `new-${audio_id}`, status: 'pending' })) }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)
    return { store, fetchMock }
  }

  it('applies the full workflow only to checked audio and starts only that selection with fresh snapshots', async () => {
    const { store, fetchMock } = setup()
    await store.load(project.id)
    expect(store.selectedAudioIds).toEqual(['aud_primary'])
    store.selectedAudioIds = ['aud_second', 'aud_third']
    store.config!.translation.model = 'selected-model'
    store.markConfigDirty()
    await store.applyConfigToSelected()
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === 'PUT')
    expect(writes.map(([url]) => url)).toEqual(['/api/v1/projects/project-1/audios/aud_second/config', '/api/v1/projects/project-1/audios/aud_third/config'])
    expect(writes.map(([, init]) => JSON.parse(String(init!.body)).asr.language)).toEqual(['en', 'ko'])
    expect(writes.every(([, init]) => JSON.parse(String(init!.body)).translation.model === 'selected-model')).toBe(true)
    await store.runSelected()
    const run = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/audios/run'))!
    expect(JSON.parse(String(run[1]!.body))).toEqual({ audio_ids: ['aud_second', 'aud_third'], resume: false })
    expect(store.batchResults).toHaveLength(2)
    expect(store.activeAudioId).toBe('aud_primary')
  })

  it('blocks starting a known config failure until it is successfully reapplied or deselected', async () => {
    let fail = true
    const { store, fetchMock } = setup((path, init) => path.includes('/aud_second/config') && init?.method === 'PUT' && fail ? Promise.resolve(jsonResponse({ error: { message: '模型不可用' } }, 422)) : undefined)
    await store.load(project.id)
    store.selectAll(true)
    await store.applyConfigToSelected()
    expect(store.batchResults.filter((entry) => entry.error)).toHaveLength(1)
    expect(store.selectedConfigFailed).toBe(true)
    await store.runSelected()
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/audios/run'))).toBe(false)
    store.selectedAudioIds = ['aud_third']
    await store.runSelected()
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/audios/run'))).toBe(true)
    fail = false
    store.selectedAudioIds = ['aud_second']
    await store.applyConfigToSelected()
    expect(store.selectedConfigFailed).toBe(false)
  })

  it('locks queued configurations and keeps whole-project deletion blocked', async () => {
    const { store } = setup((path) => path.endsWith('/aud_primary/job') ? Promise.resolve(jsonResponse(makeJob({ status: 'pending', config_snapshot: { source_language: 'en' } as Job['config_snapshot'] }))) : undefined)
    await store.load(project.id)
    expect(store.configLocked).toBe(true)
    expect(store.isRunning).toBe(true)
    expect(store.anyRunning).toBe(true)
    await expect(store.saveConfig()).rejects.toThrow('只读')
  })

  it('ignores old REST resources and websocket messages after switching the focused audio', async () => {
    const deferred = new Map<string, (response: Response) => void>()
    let hold = false
    const { store } = setup((path) => hold && path.includes('/aud_second/') ? new Promise<Response>((resolve) => deferred.set(path.split('/').pop()!, resolve)) : undefined)
    await store.load(project.id)
    const oldSocket = FakeWebSocket.instances[0]
    hold = true
    const switching = store.selectAudio('aud_second')
    await store.selectAudio('aud_third')
    expect(store.config!.source_language).toBe('ko')
    expect(store.outputs[0].name).toBe('aud_third.srt')
    oldSocket.onmessage?.({ data: JSON.stringify({ type: 'job.snapshot', data: makeJob({ id: 'stale-job' }) }) } as MessageEvent)
    deferred.get('job')!(jsonResponse(makeJob({ id: 'stale-job' })))
    deferred.get('config')!(jsonResponse({ source_language: 'en', target_language: 'ja' }))
    deferred.get('transcript')!(jsonResponse({ transcript: { segments: [{ id: 'stale' }] } }))
    deferred.get('outputs')!(jsonResponse({ items: [{ name: 'stale.srt' }] }))
    await switching
    expect(store.activeAudioId).toBe('aud_third')
    expect(store.job!.id).toBe('job-aud_third')
    expect(store.config!.source_language).toBe('ko')
    expect(store.transcript!.segments[0].id).toBe('aud_third')
    expect(store.outputs[0].name).toBe('aud_third.srt')
    expect(FakeWebSocket.instances[FakeWebSocket.instances.length - 1].url).toContain('/audios/aud_third/events')
  })

  it('does not let an older project load failure overwrite the newly opened project', async () => {
    let rejectOld: (reason: Error) => void = () => undefined
    const { store } = setup((path) => path === '/api/v1/projects/old' ? new Promise<Response>((_, reject) => { rejectOld = reject }) : undefined)
    const oldLoad = store.load('old')
    await store.load(project.id)
    rejectOld(new Error('stale error'))
    await oldLoad
    expect(store.project!.id).toBe(project.id)
    expect(store.error).toBe('')
    expect(store.loading).toBe(false)
  })
})

describe('projects store', () => {
  it('loads projects and clears loading state on success', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [project] }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()

    await store.fetchProjects()

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects')
    expect(store.projects).toEqual([project])
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
  })

  it('surfaces the backend error message when fetching projects fails', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: { message: '项目服务不可用' } }, 503))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()

    await store.fetchProjects()

    expect(store.projects).toEqual([])
    expect(store.loading).toBe(false)
    expect(store.error).toBe('项目服务不可用')
  })

  it('creates a project with the expected multipart form and prepends it', async () => {
    setActivePinia(createPinia())
    const created = { ...project, id: 'created-project' }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ project: created }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    store.projects.push(project)
    const file = new File(['audio'], 'voice.wav', { type: 'audio/wav' })

    await expect(store.createProject(file, '  New voice  ', 'zh', 'custom')).resolves.toEqual(created)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/projects')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    const form = init.body as FormData
    expect(form.get('file')).toBe(file)
    expect(form.get('name')).toBe('New voice')
    expect(form.get('source_language')).toBe('zh')
    expect(form.get('preset')).toBe('custom')
    expect(store.projects).toEqual([created, project])
    expect(store.creating).toBe(false)
  })

  it('parses backend error.message when creating a project fails', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ error: { message: '文件格式不支持' } }, 400))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    const file = new File(['data'], 'voice.bin')

    await expect(store.createProject(file, '', 'en')).rejects.toThrow('文件格式不支持')
    expect(store.error).toBe('文件格式不支持')
    expect(store.creating).toBe(false)
  })
})

describe('project detail store', () => {
  it('loads, validates, and saves the pipeline configuration', async () => {
    setActivePinia(createPinia())
    const config = {
      source_language: 'en',
      target_language: 'zh-CN',
      preprocess: { sample_rate: 16000, channels: 1, preserve_original: true },
      asr: { provider: 'mock', model: 'mock', language: 'en', vad_enabled: true, chunk_seconds: 30 },
      alignment: { enabled: true, provider: 'mock', model: 'mock', fallback: 'keep_asr_timestamps' },
      translation: { provider: 'mock', model: 'mock', target_language: 'zh-CN', batch_size: 8, temperature: 0.2, glossary: {}, instructions: null },
      export: { formats: ['srt', 'lrc'], bilingual: true, naming: 'language' },
    }
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/config') && init?.method === 'PUT') return Promise.resolve(jsonResponse(config))
      if (path.endsWith('/config')) return Promise.resolve(jsonResponse(config))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(makeJob({ status: 'pending', current_stage: null })))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, stage: 'asr', language: 'en', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)

    await store.load(project.id)
    expect(store.configValid).toBe(true)
    expect(store.config?.asr.language).toBe('en')
    store.config!.target_language = 'en'
    store.markConfigDirty()
    expect(store.configValid).toBe(false)
    expect(store.configErrors[0]).toContain('不能相同')

    store.config!.target_language = 'zh-CN'
    store.config!.translation.target_language = 'zh-CN'
    store.markConfigDirty()
    await store.saveConfig()
    expect(store.configDirty).toBe(false)
    const saveCall = fetchMock.mock.calls.find(([input, init]) => String(input).endsWith('/config') && init?.method === 'PUT')
    expect(saveCall).toBeDefined()
  })

  it('allows configuration edits for completed jobs', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/config')) return Promise.resolve(jsonResponse({ source_language: 'en', target_language: 'zh-CN' }))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(makeJob({ status: 'completed', progress: 1, current_stage: null })))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'translated', transcript: { project_id: project.id, stage: 'translated', language: 'zh-CN', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)
    await store.load(project.id)
    expect(store.configLocked).toBe(false)
    await expect(store.saveConfig()).resolves.toBeDefined()
  })

  it('normalizes protocol-specific translation settings for the node modal', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/config')) {
        return Promise.resolve(jsonResponse({
          source_language: 'en',
          target_language: 'zh-CN',
          asr: { language: 'en' },
          translation: {
            provider: 'openai_responses',
            model: 'gpt-4.1-mini',
            target_language: 'zh-CN',
            base_url: 'https://api.openai.com/v1',
            api_key_env: 'OPENAI_API_KEY',
            batch_size: 12,
            temperature: 0.3,
            timeout_seconds: 180,
            instructions: '保留专有名词',
          },
        }))
      }
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(makeJob({ status: 'pending', current_stage: null })))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, stage: 'asr', language: 'en', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)

    await store.load(project.id)

    expect(store.config?.translation).toMatchObject({
      provider: 'openai_responses',
      model: 'gpt-4.1-mini',
      base_url: 'https://api.openai.com/v1',
      api_key_env: 'OPENAI_API_KEY',
      batch_size: 12,
      temperature: 0.3,
      timeout_seconds: 180,
      instructions: '保留专有名词',
    })
  })

  it('fetches all project resources and normalizes missing job stages', async () => {
    setActivePinia(createPinia())
    const incompleteJob = {
      ...makeJob(),
      stages: { asr: { status: 'running', progress: 0.4 } },
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(incompleteJob))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, stage: 'asr', language: 'en', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [{ name: 'out.srt', size_bytes: 20, download_url: '/download' }] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)

    await store.load(project.id)

    expect(store.project).toEqual(project)
    expect(store.job?.stages.asr).toMatchObject({ status: 'running', progress: 0.4 })
    expect(store.job?.stages.preprocess).toMatchObject({ status: 'pending', progress: 0 })
    expect(store.job?.stages.export).toMatchObject({ status: 'pending', progress: 0 })
    expect(store.transcript?.stage).toBe('asr')
    expect(store.outputs[0].name).toBe('out.srt')
  })

  it('sends run and cancel requests for the loaded project', async () => {
    setActivePinia(createPinia())
    const initialJob = makeJob({ status: 'pending', current_stage: null })
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/run') || path.endsWith('/cancel')) return Promise.resolve(jsonResponse({ ok: true }))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(initialJob))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, stage: 'asr', language: 'en', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)

    await store.load(project.id)
    await store.run(false)
    await store.cancel()

    const runCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/run'))
    expect(runCall).toBeDefined()
    expect(runCall?.[1]).toMatchObject({ method: 'POST', headers: { 'Content-Type': 'application/json' } })
    expect(JSON.parse(String(runCall?.[1] && (runCall[1] as RequestInit).body))).toEqual({ resume: false })
    const cancelCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/cancel'))
    expect(cancelCall?.[1]).toMatchObject({ method: 'POST' })
    expect(store.actionLoading).toBe(false)
  })

  it('applies websocket snapshots, stage updates, and terminal job states', async () => {
    setActivePinia(createPinia())
    const runningJob = makeJob()
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: [{ id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending', current_stage: null }) }] }))
      if (path.endsWith('/job')) return Promise.resolve(jsonResponse(runningJob))
      if (path.endsWith('/transcript')) return Promise.resolve(jsonResponse({ stage: 'asr', transcript: { project_id: project.id, stage: 'asr', language: 'en', segments: [] } }))
      if (path.endsWith('/outputs')) return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.resolve(jsonResponse(project))
    })
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('WebSocket', FakeWebSocket)
    const store = useProjectDetailStore()
    detailStores.push(store)
    await store.load(project.id)
    const ws = FakeWebSocket.instances[0]
    expect(ws).toBeDefined()

    const emit = (type: string, data: Record<string, unknown> = {}) => {
      ws.onmessage?.({ data: JSON.stringify({ type, data }) } as MessageEvent)
    }
    const snapshot = makeJob({ stages: { asr: { status: 'pending', progress: 0 } } as Job['stages'], status: 'pending', current_stage: null })
    emit('job.snapshot', snapshot as unknown as Record<string, unknown>)
    expect(store.job?.status).toBe('pending')
    expect(store.job?.stages.export).toMatchObject({ status: 'pending', progress: 0 })

    emit('stage.started', { stage: 'preprocess' })
    expect(store.job?.status).toBe('running')
    expect(store.job?.current_stage).toBe('preprocess')
    expect(store.job?.stages.preprocess.status).toBe('running')
    emit('stage.progress', { stage: 'preprocess', progress: 0.6 })
    expect(store.job?.stages.preprocess.progress).toBe(0.6)
    emit('stage.completed', { stage: 'preprocess' })
    expect(store.job?.stages.preprocess).toMatchObject({ status: 'completed', progress: 1 })

    emit('job.completed')
    expect(store.job).toMatchObject({ status: 'completed', progress: 1, current_stage: null })

    emit('job.snapshot', makeJob({ status: 'running', current_stage: 'asr' }) as unknown as Record<string, unknown>)
    emit('job.failed', { stage: 'asr', code: 'ASR_FAILED', message: '识别失败' })
    expect(store.job).toMatchObject({ status: 'failed', current_stage: 'asr' })
    expect(store.job?.stages.asr.status).toBe('failed')
    expect(store.job?.error).toMatchObject({ type: 'ASR_FAILED', message: '识别失败', stage: 'asr', retryable: true })

    emit('job.snapshot', makeJob({ status: 'cancelling', current_stage: 'asr', cancel_requested: true }) as unknown as Record<string, unknown>)
    emit('job.cancelled')
    expect(store.job).toMatchObject({ status: 'cancelled', cancel_requested: true, current_stage: null })
    expect(store.job?.stages.asr).toMatchObject({ status: 'pending', progress: 0 })
  })
})
