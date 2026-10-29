import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectDetailStore, type AudioJobSummary } from './projectDetail'
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
  source: {
    original_name: 'demo.wav',
    language: 'en',
    size_bytes: 123,
    media_type: 'audio/wav',
  },
}

function makeJob(overrides: AudioJobSummary = {}): AudioJobSummary {
  return { id: 'job-1', status: 'running', ...overrides }
}

const detailStores: ReturnType<typeof useProjectDetailStore>[] = []

afterEach(() => {
  detailStores.forEach((store) => store.stop())
  detailStores.length = 0
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('projects store', () => {
  it('loads projects and clears loading state on success', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [project], total: 1, limit: 20, offset: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()

    await store.fetchProjects()

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects?limit=20&offset=0')
    expect(store.projects).toEqual([project])
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
  })

  it('loads paginated projects and navigates first, middle, last and empty pages', async () => {
    setActivePinia(createPinia())
    const pages: Record<string, unknown> = {
      '0': { items: [project], total: 45, limit: 20, offset: 0 },
      '20': { items: [{ ...project, id: 'project-21' }], total: 45, limit: 20, offset: 20 },
      '40': { items: [{ ...project, id: 'project-41' }], total: 45, limit: 20, offset: 40 },
      '60': { items: [], total: 45, limit: 20, offset: 60 },
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      return Promise.resolve(jsonResponse(pages[url.searchParams.get('offset') || '0']))
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    await store.fetchProjects()
    expect(store.total).toBe(45)
    expect(store.pageCount).toBe(3)
    expect(store.hasPrevious).toBe(false)
    expect(store.hasNext).toBe(true)
    await store.nextPage()
    expect(store.offset).toBe(20)
    await store.nextPage()
    expect(store.offset).toBe(40)
    expect(store.hasNext).toBe(false)
    await store.fetchProjects(60)
    expect(store.projects).toEqual([])
    expect(store.total).toBe(45)
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/projects?limit=20&offset=0',
      '/api/v1/projects?limit=20&offset=20',
      '/api/v1/projects?limit=20&offset=40',
      '/api/v1/projects?limit=20&offset=60',
    ])
  })

  it('ignores an older page response that resolves after a newer request', async () => {
    setActivePinia(createPinia())
    let resolveOld!: (value: Response) => void
    let resolveNew!: (value: Response) => void
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveNew = resolve }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    const oldRequest = store.fetchProjects(0)
    const newRequest = store.fetchProjects(20)
    resolveNew(jsonResponse({ items: [{ ...project, id: 'new-page' }], total: 40, limit: 20, offset: 20 }))
    await newRequest
    resolveOld(jsonResponse({ items: [{ ...project, id: 'old-page' }], total: 40, limit: 20, offset: 0 }))
    await oldRequest
    expect(store.offset).toBe(20)
    expect(store.projects.map((item) => item.id)).toEqual(['new-page'])
    expect(store.loading).toBe(false)
  })

  it('does not let an older failed page request overwrite a newer successful page', async () => {
    setActivePinia(createPinia())
    let rejectOld!: (reason: Error) => void
    let resolveNew!: (value: Response) => void
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((_, reject) => { rejectOld = reject }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveNew = resolve }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    const oldRequest = store.fetchProjects(0)
    const newRequest = store.fetchProjects(20)
    resolveNew(jsonResponse({ items: [{ ...project, id: 'fresh' }], total: 40, limit: 20, offset: 20 }))
    await newRequest
    rejectOld(new Error('stale failure'))
    await oldRequest
    expect(store.error).toBe('')
    expect(store.projects.map((item) => item.id)).toEqual(['fresh'])
    expect(store.loading).toBe(false)
  })

  it('invalidates an in-flight page before create and reloads the authoritative page', async () => {
    setActivePinia(createPinia())
    let resolveOld!: (value: Response) => void
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce(jsonResponse({ project: { ...project, id: 'new' }, job: makeJob() }))
      .mockResolvedValueOnce(jsonResponse({ items: [{ ...project, id: 'new' }], total: 1, limit: 20, offset: 0 }))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    const pendingList = store.fetchProjects(0)
    await store.createProject(new File(['x'], 'x.wav'), 'new', 'ja', 'zh-CN')
    resolveOld(jsonResponse({ items: [{ ...project, id: 'old' }], total: 1, limit: 20, offset: 0 }))
    await pendingList
    expect(store.projects.map((item) => item.id)).toEqual(['new'])
    expect(store.error).toBe('')
  })

  it('moves to the previous page when deleting the only item on the last page', async () => {
    setActivePinia(createPinia())
    const responses = [
      { items: [{ ...project, id: 'last-item' }], total: 21, limit: 20, offset: 20 },
      { items: [project], total: 20, limit: 20, offset: 0 },
    ]
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'DELETE') return Promise.resolve(jsonResponse(null, 204))
      return Promise.resolve(jsonResponse(responses.shift()))
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    await store.fetchProjects(20)
    await store.deleteProject('last-item')
    expect(store.offset).toBe(0)
    expect(store.projects.map((item) => item.id)).toEqual(['project-1'])
  })

  it('refills the current page after deleting one item when more projects remain', async () => {
    setActivePinia(createPinia())
    const responses = [
      { items: [{ ...project, id: 'a' }, { ...project, id: 'b' }], total: 5, limit: 2, offset: 2 },
      { items: [{ ...project, id: 'c' }, { ...project, id: 'd' }], total: 4, limit: 2, offset: 2 },
    ]
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => init?.method === 'DELETE' ? Promise.resolve(jsonResponse(null, 204)) : Promise.resolve(jsonResponse(responses.shift())))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    await store.fetchProjects(2)
    await store.deleteProject('a')
    expect(store.projects.map((item) => item.id)).toEqual(['c', 'd'])
    expect(store.total).toBe(4)
  })

  it('rejects an incomplete paginated response without replacing the current page', async () => {
    setActivePinia(createPinia())
    const store = useProjectsStore()
    store.projects = [project]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ items: [project] })))
    await store.fetchProjects()
    expect(store.projects).toEqual([project])
    expect(store.error).toContain('响应不完整')
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
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => init?.method === 'POST'
      ? Promise.resolve(jsonResponse({ project: created }))
      : Promise.resolve(jsonResponse({ items: [created], total: 1, limit: 20, offset: 0 })))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectsStore()
    store.projects.push(project)
    const file = new File(['audio'], 'voice.wav', { type: 'audio/wav' })

    await expect(store.createProject(file, '  New voice  ', 'zh')).resolves.toEqual(created)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/projects')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    const form = init.body as FormData
    expect(form.get('file')).toBe(file)
    expect(form.get('name')).toBe('New voice')
    expect(form.get('source_language')).toBe('zh')
    expect(form.get('preset')).toBeNull()
    expect(store.projects).toEqual([created])
    expect(store.total).toBe(1)
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
  function workspaceResponse(path: string, audios: unknown[]) {
    if (path.endsWith('/audios')) return Promise.resolve(jsonResponse({ items: audios }))
    return Promise.resolve(jsonResponse(project))
  }

  it('loads project and audios, selects the requested audio, and exposes only node workspace context', async () => {
    setActivePinia(createPinia())
    const audios = [
      { id: 'aud_primary', source: project.source, job: makeJob({ id: 'job-primary', status: 'pending' }) },
      { id: 'aud_second', source: project.source, job: makeJob({ id: 'job-second', status: 'running' }) },
    ]
    const fetchMock = vi.fn((input: RequestInfo | URL) => workspaceResponse(new URL(String(input), 'http://localhost').pathname, audios))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectDetailStore()
    detailStores.push(store)

    await store.load(project.id, 'aud_second')

    expect(store.project).toEqual(project)
    expect(store.audios).toEqual(audios)
    expect(store.activeAudioId).toBe('aud_second')
    expect(store.activeAudio?.source).toEqual(project.source)
    expect(store.anyRunning).toBe(true)
    expect(store.error).toBe('')
    expect('config' in store).toBe(false)
    expect('job' in store).toBe(false)
    expect('selectedStage' in store).toBe(false)
  })

  it('selects another loaded audio without issuing old pipeline requests', async () => {
    setActivePinia(createPinia())
    const audios = [
      { id: 'aud_primary', source: project.source, job: makeJob({ status: 'pending' }) },
      { id: 'aud_second', source: { ...project.source, language: 'ko' }, job: makeJob({ id: 'job-2', status: 'completed' }) },
    ]
    const fetchMock = vi.fn((input: RequestInfo | URL) => workspaceResponse(new URL(String(input), 'http://localhost').pathname, audios))
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectDetailStore()
    detailStores.push(store)
    await store.load(project.id)
    fetchMock.mockClear()

    expect(store.selectAudio('aud_second')).toBe(true)
    expect(store.activeAudioId).toBe('aud_second')
    expect(store.activeAudio?.source.language).toBe('ko')
    expect(store.anyRunning).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects malformed audio lists and does not let an older load overwrite a newer project', async () => {
    setActivePinia(createPinia())
    let rejectOld: (reason: Error) => void = () => undefined
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path === '/api/v1/projects/old') return new Promise<Response>((_, reject) => { rejectOld = reject })
      if (path.endsWith('/audios') && path.includes('/projects/broken/')) return Promise.resolve(jsonResponse({ items: null }))
      return workspaceResponse(path, [{ id: 'aud_primary', source: project.source, job: makeJob() }])
    })
    vi.stubGlobal('fetch', fetchMock)
    const store = useProjectDetailStore()
    detailStores.push(store)

    const oldLoad = store.load('old')
    await store.load(project.id)
    rejectOld(new Error('stale error'))
    await oldLoad
    expect(store.project!.id).toBe(project.id)
    expect(store.error).toBe('')

    await expect(store.load('broken')).rejects.toThrow('音频响应不完整')
    expect(store.error).toContain('音频响应不完整')
  })
})
