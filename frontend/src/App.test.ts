import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { locale, LOCALE_STORAGE_KEY, setLocale } from './i18n'

let app: VueApp | undefined
let root: HTMLDivElement | undefined
const project = { id: 'created-project', name: 'Voice', created_at: '2026-01-01', updated_at: '2026-01-01', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 5 } } satisfies Project
const emptyProject: Project = { ...project, source: null, audios: [], source_language: null, target_language: null }
const response = (payload: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload }) as Response
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }

async function mount() {
  const capabilities = { schema_version: '1.0', workflow: { schema_version: '1.2', nodes: [
    { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
    { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
  ], ports: ['audio'], constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 }, default_graph: {
    nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } }, { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }],
    edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
  } }, asr: { providers: [{ id: 'mock', label: 'Mock' }], status: 'available' } }
  const workflowDocument = { schema_version: '1.0', project_id: project.id, revision: 1, workflow_revision: 1, updated_at: '2026-01-01', ...capabilities.workflow.default_graph }
  const fetcher = vi.fn((request: RequestInfo | URL, init?: RequestInit) => {
    const path = String(request)
    if (path.endsWith('/projects/empty') && init?.method === 'POST') return Promise.resolve(response({ project: emptyProject, job: null }, 201))
    if (path.endsWith('/projects?limit=20&offset=0')) return Promise.resolve(response({ items: [project], total: 1, limit: 20, offset: 0 }))
    if (path.endsWith(`/projects/${project.id}`)) return Promise.resolve(response(emptyProject))
    if (path.endsWith('/capabilities')) return Promise.resolve(response(capabilities))
    if (path.endsWith('/workflow')) return Promise.resolve(response(workflowDocument))
    if (path.endsWith('/workflow-runs?limit=30&offset=0')) return Promise.resolve(response({ items: [], total: 0, limit: 30, offset: 0 }))
    return Promise.resolve(response({ items: [] }))
  })
  vi.stubGlobal('fetch', fetcher)
  const pinia = createPinia(); setActivePinia(pinia)
  root = document.createElement('div'); document.body.append(root)
  app = createApp(App).use(pinia); app.mount(root)
  await settle()
  root.querySelector<HTMLButtonElement>('.page-heading button')!.click()
  await nextTick()
  return { store: useProjectsStore(pinia), detail: useProjectDetailStore(pinia), fetcher, pinia }
}
async function fillName(value: string) {
  const input = root!.querySelector<HTMLInputElement>('#project-name')!
  input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
}
async function switchInterface(value: 'zh-CN' | 'en') {
  const picker = root!.querySelector<HTMLSelectElement>('.locale-picker')!
  picker.value = value; picker.dispatchEvent(new Event('change', { bubbles: true })); await nextTick()
}
afterEach(() => {
  app?.unmount(); app = undefined; root?.remove(); root = undefined
  vi.unstubAllGlobals(); vi.restoreAllMocks(); setLocale('zh-CN')
})

describe('name-only project creation', () => {
  it('shows only a name field and creates the project through JSON without uploads or an upload-policy request', async () => {
    const { fetcher, detail } = await mount()
    expect(root!.querySelectorAll('form input')).toHaveLength(1)
    expect(root!.querySelector('#source-language')).toBeNull()
    expect(root!.querySelector('#target-language')).toBeNull()
    expect(root!.querySelector('input[type="file"]')).toBeNull()
    expect(root!.querySelector('[data-test="batch-import"]')).toBeNull()
    expect(root!.querySelector('.upload-queue')).toBeNull()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    expect(root!.textContent).toContain('请输入项目名称')
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    await fillName('  Empty canvas  ')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle(); await settle()
    const creates = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(creates).toHaveLength(1)
    expect(String(creates[0]![0])).toBe('/api/v1/projects/empty')
    expect(JSON.parse(creates[0]![1]!.body as string)).toEqual({ name: 'Empty canvas' })
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/upload-policy'))).toBe(false)
    expect(detail.project?.source_language).toBeNull()
    expect(detail.project?.target_language).toBeNull()
    expect(root!.querySelector('.is-canvas-workspace .node-workflow')).not.toBeNull()
    expect(root!.querySelector('.workflow-audio-picker')).toBeNull()
    expect(root!.querySelector('button[aria-label="返回上传队列"]')).toBeNull()
  })

  it('preserves the entered name and a failed response, then allows retry', async () => {
    const { fetcher } = await mount()
    const originalFetch = fetch
    let attempts = 0
    vi.stubGlobal('fetch', vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
      if (String(path).endsWith('/projects/empty') && init?.method === 'POST' && ++attempts === 1) return Promise.resolve(response({ error: { message: '暂时无法创建' } }, 503))
      return originalFetch(path, init)
    }))
    await fillName('Keep my name')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle()
    expect(root!.querySelector('[role="alert"]')?.textContent).toContain('暂时无法创建')
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.value).toBe('Keep my name')
    expect(root!.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled).toBe(false)
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle(); await settle()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(attempts).toBe(2)
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/upload-policy'))).toBe(false)
  })

  it('protects navigation, unload and the request snapshot while creation is pending, and keeps language switching usable', async () => {
    const { store, detail, pinia } = await mount()
    const originalFetch = fetch
    let release!: (value: Response) => void
    const requests: RequestInit[] = []
    vi.stubGlobal('fetch', vi.fn((path: RequestInfo | URL, init?: RequestInit) => {
      if (String(path).endsWith('/projects/empty') && init?.method === 'POST') { requests.push(init); return new Promise<Response>((resolve) => { release = resolve }) }
      return originalFetch(path, init)
    }))
    await fillName('Frozen name')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle()
    expect(store.creating).toBe(true)
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('.form-actions button')!.disabled).toBe(true)
    const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
    await app!._instance!.exposed!.openProject(project.id)
    app!._instance!.exposed!.showCreate()
    await switchInterface('en')
    expect(root!.querySelector('.create-form')).not.toBeNull()
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.value).toBe('Frozen name')
    expect(root!.querySelector('button[type="submit"]')?.textContent).toBe('Creating project…')
    expect(JSON.parse(requests[0]!.body as string)).toEqual({ name: 'Frozen name' })
    expect(requests).toHaveLength(1)
    release(response({ project: emptyProject, job: null }, 201)); await settle(); await settle()
    expect(detail.project?.id).toBe(project.id)
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(store.creating).toBe(false)
    const workflow = useWorkflowStore(pinia)
    workflow.setCleanBaseline(workflow.draft)
    const finishedUnload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(finishedUnload)
    expect(finishedUnload.defaultPrevented).toBe(false)
  })

  it('switches the name form and validation language without resetting user input', async () => {
    await mount()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await nextTick()
    await fillName('自定义 Project')
    const form = root!.querySelector('form')
    await switchInterface('en')
    expect(locale.value).toBe('en')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
    expect(root!.querySelector('form')).toBe(form)
    expect(root!.querySelector('[role="alert"]')?.textContent).toContain('Enter a project name.')
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.value).toBe('自定义 Project')
    expect(root!.querySelector('button[type="submit"]')?.textContent).toBe('Create project and open canvas')
    expect(root!.querySelector('h1')?.textContent).toBe('New project')
    expect(root!.querySelectorAll('form input')).toHaveLength(1)
  })

  it('renders unnamed-language projects without null labels and retains legacy source metadata, counts and pagination', async () => {
    const { store } = await mount()
    vi.spyOn(store, 'fetchProjects').mockResolvedValue(undefined)
    store.projects = [emptyProject, { ...project, source_language: 'xx-CUSTOM' }, { ...emptyProject, id: 'uploaded-empty', audios: [{ id: 'aud_new', source: { ...project.source, original_name: 'new-resource.wav' } }] }]
    store.total = 1200; store.offset = 20
    app!._instance!.exposed!.showList(); await nextTick()
    const cards = root!.querySelectorAll('.project-card')
    expect(cards[0]!.textContent).toContain('0 条音频 · 0 KB')
    expect(cards[0]!.textContent).toContain('尚无音频资源')
    expect(cards[0]!.textContent).not.toMatch(/undefined|null/)
    expect(cards[0]!.querySelectorAll('.project-meta span')).toHaveLength(1)
    expect(cards[1]!.textContent).toContain('XX-CUSTOM')
    expect(cards[2]!.textContent).toContain('new-resource.wav')
    await switchInterface('en')
    expect(cards[0]!.textContent).toContain('0 audio files · 0 KB')
    expect(cards[1]!.textContent).toContain('1 audio file')
    expect(root!.querySelector('nav[aria-label="Project pagination"]')?.textContent).toContain('Page 2 / 60 · Projects: 1,200')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    root!.querySelector<HTMLButtonElement>('button[aria-label="Delete project"]')!.click()
    expect(confirm).toHaveBeenCalledWith('Delete project “Voice”? This removes all audio, subtitles, and output files in the project.')
    expect(store.projects).toHaveLength(3)
  })

  it('waits for the audio list before mounting the editor and automatically loads its initial history context', async () => {
    const { detail } = await mount()
    const workflow = useWorkflowStore()
    const originalFetch = fetch
    let resolveAudios!: (response: Response) => void
    const audioResponse = new Promise<Response>((resolve) => { resolveAudios = resolve })
    const fetcher = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      const path = String(url)
      if (path.endsWith(`/projects/${project.id}`)) return Promise.resolve(response(project))
      if (path.endsWith(`/projects/${project.id}/audios`)) return audioResponse
      if (path.endsWith('/workflow-runs?limit=30&offset=0')) return Promise.resolve(response({ items: [{ id: 'older-run', workflow_revision: 1, status: 'completed', created_at: '2026-01-01', finished_at: '2026-01-01', error: null }], total: 1, limit: 30, offset: 0 }))
      return originalFetch(url, init)
    })
    vi.stubGlobal('fetch', fetcher)
    const opening = app!._instance!.exposed!.openProject(project.id)
    await settle()
    expect(detail.project?.id).toBe(project.id)
    expect(detail.loading).toBe(true)
    expect(detail.activeAudioId).toBeNull()
    expect(root!.querySelector('.node-workflow')).toBeNull()
    expect(root!.querySelector('.is-canvas-workspace .workspace-loading-shell .workspace-header-back')).not.toBeNull()
    expect(root!.querySelector('.workspace-loading-shell .locale-picker')).not.toBeNull()
    expect(root!.querySelector('.topbar')).toBeNull()
    expect(root!.querySelector('.page-heading')).toBeNull()
    expect(fetcher.mock.calls.some(([path]) => String(path).endsWith('/workflow'))).toBe(false)
    resolveAudios(response({ items: [{ id: 'initial-audio', source: project.source }] }))
    await opening; await settle(); await settle(); await settle()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(workflow.audioId).toBe('initial-audio')
    expect(detail.activeAudioId).toBe('initial-audio')
    expect(workflow.history.map((run) => run.id)).toEqual(['older-run'])
    expect(fetcher.mock.calls.some(([path]) => String(path).includes('/audios/initial-audio/workflow-runs?'))).toBe(true)
  })

  it('retains a compact route back to projects when the project cannot be loaded', async () => {
    const { detail } = await mount()
    vi.spyOn(detail, 'load').mockImplementation(async () => {
      detail.project = null
      detail.loading = false
      detail.error = '项目详情加载失败'
      throw new Error('Unavailable project')
    })
    await app!._instance!.exposed!.openProject(project.id)
    await settle()
    expect(root!.querySelector('.workspace-alert[role="alert"]')?.textContent).toContain('项目详情加载失败')
    expect(root!.querySelector('.node-workflow')).toBeNull()
    root!.querySelector<HTMLButtonElement>('.workspace-header-back')!.click()
    await settle()
    expect(root!.querySelector('.list-layout')).not.toBeNull()
    expect(root!.querySelector('.is-canvas-workspace')).toBeNull()
  })

})

describe('workflow navigation guards', () => {
  async function mountWorkflow() {
    const firstAudio = { id: 'aud_primary', source: project.source }
    const secondAudio = { id: 'aud_second', source: { ...project.source, original_name: 'second.wav' } }
    const capabilities = { schema_version: '1.0', workflow: { schema_version: '1.2', nodes: [
      { type: 'audio_input', label: '音频输入', id_prefix: 'input', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] }, position_x: 96 },
      { type: 'audio_output', label: '音频输出', id_prefix: 'output', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] }, position_x: 420 },
    ], ports: ['audio'], constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 }, default_graph: {
      nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } }, { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }],
      edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
    } }, asr: { providers: [{ id: 'mock', label: 'Mock' }] }, export: { formats: [], naming_modes: [] } }
    const workflowDocument = {
      schema_version: '1.0', project_id: project.id, revision: 1, workflow_revision: 1,
      updated_at: '2026-01-01T00:00:00Z',
      nodes: [
        { id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } },
        { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } },
      ],
      edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
    }
    vi.stubGlobal('fetch', vi.fn((request: string) => {
      const path = String(request)
      if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 1024 }))
      if (path.endsWith('/projects?limit=20&offset=0')) return Promise.resolve(response({ items: [project], total: 1, limit: 20, offset: 0 }))
      if (path.endsWith('/capabilities')) return Promise.resolve(response(capabilities))
      if (path.endsWith('/workflow/validate')) return Promise.resolve(response({ valid: true, errors: [] }))
      if (path.endsWith('/workflow-runs?limit=30&offset=0')) return Promise.resolve(response({ items: [], total: 0, limit: 30, offset: 0 }))
      if (path.endsWith('/workflow')) return Promise.resolve(response(workflowDocument))
      return Promise.resolve(response({ items: [] }))
    }))
    const pinia = createPinia()
    setActivePinia(pinia)
    root = document.createElement('div')
    document.body.append(root)
    app = createApp(App).use(pinia)
    app.mount(root)
    await settle()
    const detail = useProjectDetailStore(pinia)
    const workflow = useWorkflowStore(pinia)
    vi.spyOn(workflow, 'load').mockImplementation(async () => {
      workflow.capabilities = readCapabilities(capabilities)
      await nextTick()
      workflow.document = workflowDocument
      workflow.setCleanBaseline(workflowDocument)
      workflow.selectedNodeId = 'input'
    })
    vi.spyOn(detail, 'load').mockImplementation(async () => {
      detail.project = project
      detail.audios = [{ ...firstAudio, job: jobFor(firstAudio.id) }, { ...secondAudio, job: jobFor(secondAudio.id) }]
      detail.activeAudioId = firstAudio.id
      detail.loading = false
    })
    vi.spyOn(detail, 'selectAudio').mockImplementation((id: string) => {
      detail.activeAudioId = id
      detail.loading = false
      return true
    })
    root!.querySelector<HTMLElement>('.project-card')!.click()
    detail.project = project
    detail.audios = [{ ...firstAudio, job: jobFor(firstAudio.id) }, { ...secondAudio, job: jobFor(secondAudio.id) }]
    detail.activeAudioId = firstAudio.id
    detail.loading = false
    await nextTick()
    expect(root!.querySelector('.workspace')).not.toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(root!.querySelector('.workspace-mode-switch')).toBeNull()
    expect(root!.textContent).not.toContain('五阶段')
    return { pinia, detail }
  }

  function jobFor(id: string) {
    return { id: `job-${id}`, status: 'failed' as const }
  }

  function findButton(text: string) {
    return Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.trim() === text)
  }

  async function openDirtyWorkflow() {
    const state = await mountWorkflow()
    const workflow = useWorkflowStore(state.pinia)
    await nextTick()
    const fixture = { nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } }, { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }], edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }] }
    workflow.setCleanBaseline(fixture)
    workflow.draft.nodes[0]!.position.x += 24
    workflow.markDirty()
    await nextTick()
    // App navigation tests exercise the page-level guards; the child editor's modal flow is covered separately.
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(workflow.dirty).toBe(true)
    return { ...state, workflow }
  }

  it('locks history selection during save, validation, upload and active runs without diverging detail context', async () => {
    const { pinia, detail } = await mountWorkflow()
    const workflow = useWorkflowStore(pinia)
    for (const lock of ['saving', 'validating', 'resourceUploading', 'loading'] as const) {
      workflow[lock] = true
      await settle()
      const select = root!.querySelector<HTMLSelectElement>('.workflow-audio-picker select')!
      expect(select.disabled).toBe(true)
      select.value = 'aud_second'; select.dispatchEvent(new Event('change', { bubbles: true }))
      await settle()
      expect(detail.activeAudioId).toBe('aud_primary')
      expect(select.value).toBe('aud_primary')
      workflow[lock] = false
    }
  })

  it('locks history selection, project deletion and navigation while the start request is pending', async () => {
    const { pinia, detail } = await mountWorkflow()
    const workflow = useWorkflowStore(pinia)
    vi.mocked(workflow.load).mockRestore()
    await workflow.load(project.id, 'aud_primary')
    await settle()
    const originalFetch = fetch
    let release!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn((request: RequestInfo | URL, init?: RequestInit) => {
      if (String(request).endsWith('/workflow-runs') && init?.method === 'POST') return new Promise<Response>((resolve) => { release = resolve })
      return originalFetch(request, init)
    }))
    const starting = workflow.startRun().catch(() => null)
    await settle()
    expect(workflow.actionLoading).toBe(true)
    const picker = root!.querySelector<HTMLSelectElement>('.workflow-audio-picker select')!
    expect(picker.disabled).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('button[aria-label="删除项目"]')!.disabled).toBe(true)
    picker.value = 'aud_second'; picker.dispatchEvent(new Event('change', { bubbles: true }))
    root!.querySelector<HTMLButtonElement>('.workspace-header-back')!.click()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(detail.activeAudioId).toBe('aud_primary')
    expect(picker.value).toBe('aud_primary')
    expect(root!.querySelector('.is-canvas-workspace')).not.toBeNull()
    expect(root!.querySelector('.create-form')).toBeNull()
    release(response({}, 500))
    await starting; await settle()
    expect(workflow.actionLoading).toBe(false)
    expect(picker.disabled).toBe(false)
  })

  it('does not expose a legacy pipeline switch and keeps the node canvas as the only workspace', async () => {
    await mountWorkflow()
    expect(root!.querySelector('.workspace-mode-switch')).toBeNull()
    expect(root!.querySelector('.pipeline-panel')).toBeNull()
    expect(root!.querySelectorAll('.node-workflow')).toHaveLength(1)
    expect(root!.textContent).not.toContain('五阶段')
  })

  it('switches workspace language without reloading the selected audio or clearing an unsaved graph', async () => {
    const { pinia, detail } = await mountWorkflow()
    const workflow = useWorkflowStore(pinia)
    workflow.draft.nodes[0]!.position.x += 24
    workflow.markDirty()
    const document = workflow.document
    const draft = workflow.draft
    expect(workflow.dirty).toBe(true)
    const workspace = root!.querySelector('.workspace')
    const canvas = root!.querySelector('.node-workflow')
    const calls = vi.mocked(detail.load).mock.calls.length
    await switchInterface('en')
    expect(root!.querySelector('.workspace')).toBe(workspace)
    expect(root!.querySelector('.node-workflow')).toBe(canvas)
    expect(root!.querySelector('.back-button')?.textContent).toBe('← Back to projects')
    expect(root!.querySelector('select[aria-label="Choose audio to view its history"]')).not.toBeNull()
    expect(root!.querySelector('h1')?.textContent).toBe('Voice')
    expect(detail.activeAudioId).toBe('aud_primary')
    expect(detail.project).toEqual(project)
    expect(workflow.document).toBe(document)
    expect(workflow.draft).toBe(draft)
    expect(workflow.dirty).toBe(true)
    expect(detail.load).toHaveBeenCalledTimes(calls)
  })

  it('keeps the node workspace reachable and provides project navigation', async () => {
    await mountWorkflow()
    expect(root!.querySelector('.app-shell.is-canvas-workspace')).not.toBeNull()
    expect(root!.querySelector('.topbar')).toBeNull()
    expect(root!.querySelector('.page-heading')).toBeNull()
    expect(root!.querySelector('.workspace-title')).toBeNull()
    expect(root!.querySelector('.workflow-workspace-header .workspace-project-title')?.textContent).toBe('Voice')
    expect(root!.querySelectorAll('.locale-picker')).toHaveLength(1)
    expect(root!.querySelector('.workspace')).not.toBeNull()
    expect(root!.querySelector('.back-button')).not.toBeNull()
    expect(root!.querySelector('button[aria-label="新建项目"]')).not.toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).some((item) => item.textContent?.trim() === '返回项目工作区')).toBe(false)
  })

  it('keeps unsaved navigation guards on the compact back, create and delete controls', async () => {
    const { workflow } = await openDirtyWorkflow()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const discard = vi.spyOn(workflow, 'discardDraft')
    for (const selector of ['.workspace-header-back', 'button[aria-label="新建项目"]', 'button[aria-label="删除项目"]']) {
      root!.querySelector<HTMLButtonElement>(selector)!.click()
      await nextTick()
      expect(root!.querySelector('.canvas-dialog[role="dialog"][aria-modal="true"] .workflow-discard'), selector).not.toBeNull()
      expect(root!.querySelector('.is-canvas-workspace')).not.toBeNull()
      expect(workflow.dirty).toBe(true)
      findButton('继续编辑')!.click()
      await nextTick()
    }
    expect(confirm).not.toHaveBeenCalled()
    expect(discard).not.toHaveBeenCalled()
    root!.querySelector<HTMLButtonElement>('.workspace-header-back')!.click()
    await nextTick()
    findButton('放弃修改')!.click()
    await settle()
    expect(discard).toHaveBeenCalledTimes(1)
    expect(root!.querySelector('.is-canvas-workspace')).toBeNull()
    expect(root!.querySelector('.list-layout')).not.toBeNull()
    expect(root!.querySelector('.topbar')).not.toBeNull()
  })

  it('blocks compact navigation while saving, validating or running and keeps busy project deletion disabled', async () => {
    const { pinia, detail } = await mountWorkflow()
    const workflow = useWorkflowStore(pinia)
    for (const lock of ['saving', 'validating'] as const) {
      workflow[lock] = true
      await nextTick()
      for (const selector of ['.workspace-header-back', 'button[aria-label="新建项目"]']) {
        root!.querySelector<HTMLButtonElement>(selector)!.click()
        await nextTick()
        expect(root!.querySelector('.is-canvas-workspace')).not.toBeNull()
        expect(root!.querySelector('.create-form')).toBeNull()
      }
      expect(root!.querySelector<HTMLButtonElement>('button[aria-label="删除项目"]')!.disabled).toBe(true)
      workflow[lock] = false
    }
    workflow.run = { id: 'active-run', project_id: project.id, audio_id: 'aud_primary', workflow_revision: 1, status: 'running', created_at: '2026-01-01', started_at: '2026-01-01', finished_at: null, cancel_requested: false, error: null, workflow: workflow.draft, nodes: [] }
    await nextTick()
    const picker = root!.querySelector<HTMLSelectElement>('.workflow-audio-picker select')!
    expect(picker.disabled).toBe(true)
    picker.value = 'aud_second'; picker.dispatchEvent(new Event('change', { bubbles: true }))
    root!.querySelector<HTMLButtonElement>('.workspace-header-back')!.click()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(detail.activeAudioId).toBe('aud_primary')
    expect(picker.value).toBe('aud_primary')
    expect(root!.querySelector('.is-canvas-workspace')).not.toBeNull()
    expect(root!.querySelector<HTMLButtonElement>('button[aria-label="删除项目"]')!.disabled).toBe(true)
    expect(root!.textContent).toContain('节点运行尚未结束')
    expect(root!.querySelectorAll('.alert[role="alert"]')).toHaveLength(1)
    workflow.run = null
    detail.audios[1]!.job = { id: 'other-job', status: 'running' }
    await nextTick()
    expect(root!.querySelector<HTMLButtonElement>('button[aria-label="删除项目"]')!.disabled).toBe(true)
  })

  it('changes audio history context without leaving, discarding, or remounting the project canvas', async () => {
    const state = await mountWorkflow()
    const workflow = useWorkflowStore(state.pinia)
    workflow.capabilities = { ...workflow.capabilities!, nodes: workflow.capabilities!.nodes.map((node) => node.isSource ? { ...node, resourceInput: { resourceType: 'audio', configKey: 'audio_id' } } : node) }
    workflow.draft.nodes[0]!.config.audio_id = 'aud_primary'
    workflow.draft.nodes[0]!.position.x += 24
    workflow.markDirty()
    const draft = workflow.draft
    const canvas = root!.querySelector('.node-workflow')
    const leave = vi.spyOn(workflow, 'leaveEditor')
    const discard = vi.spyOn(workflow, 'discardDraft')
    const loadCalls = vi.mocked(workflow.load).mock.calls.length
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="选择要查看历史的音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(state.detail.activeAudioId).toBe('aud_second')
    expect(workflow.boundAudioId).toBe('aud_primary')
    expect(workflow.draft.nodes[0]!.config.audio_id).toBe('aud_primary')
    expect(root!.querySelector('.node-workflow')).toBe(canvas)
    expect(workflow.draft).toBe(draft)
    expect(workflow.dirty).toBe(true)
    expect(leave).not.toHaveBeenCalled()
    expect(discard).not.toHaveBeenCalled()
    expect(workflow.load).toHaveBeenCalledTimes(loadCalls)
  })

  it('registers node uploads and audio selections in project detail without replacing the canvas', async () => {
    const { detail, pinia } = await mountWorkflow()
    const workflow = useWorkflowStore(pinia)
    workflow.draft.nodes[0]!.position.x += 24
    workflow.markDirty()
    const canvas = root!.querySelector('.node-workflow') as HTMLElement & { __vueParentComponent: { emit: (event: string, value: unknown) => void } }
    const audio = { id: 'aud_uploaded', source: { ...project.source, original_name: 'new.wav' } }
    canvas.__vueParentComponent.emit('uploaded', audio)
    await settle()
    expect(detail.audios.find((item) => item.id === audio.id)).toEqual(audio)
    expect(detail.project?.audios?.find((item) => item.id === audio.id)).toEqual(audio)
    expect(detail.activeAudioId).toBe(audio.id)
    expect(workflow.dirty).toBe(true)
    expect(root!.querySelector('.node-workflow')).toBe(canvas)
    canvas.__vueParentComponent.emit('selectAudio', 'aud_second')
    await settle()
    expect(detail.activeAudioId).toBe('aud_second')
    expect(root!.querySelector('.node-workflow')).toBe(canvas)
  })

  it('switches to a different project with the previous project still loaded', async () => {
    const { detail } = await mountWorkflow()
    const original = detail.project
    expect(original?.id).toBe(project.id)
    const other = { ...project, id: 'other-project', name: 'Other project' }
    const load = vi.spyOn(detail, 'load').mockImplementation(async (id: string) => {
      detail.project = id === other.id ? other : project
      detail.audios = [{ id: 'aud_primary', source: project.source }]
      detail.activeAudioId = 'aud_primary'
      detail.loading = false
    })
    ;(app!._instance!.exposed as { openProject: (id: string) => Promise<void> }).openProject(other.id)
    await settle()
    expect(load.mock.calls.map(([id]) => id)).toEqual([other.id])
    expect(detail.project?.id).toBe(other.id)
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
  })

  it('does not expose legacy pipeline mode controls after switching audio', async () => {
    await mountWorkflow()
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="选择要查看历史的音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.workspace-mode-switch')).toBeNull()
    expect(root!.querySelector('.pipeline-panel')).toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
  })
})
