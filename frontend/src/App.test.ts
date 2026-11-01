import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import { readCapabilities } from './workflow/capabilities'
import { locale, LOCALE_STORAGE_KEY } from './i18n'

let app: VueApp | undefined
let root: HTMLDivElement | undefined
const project = { id: 'created-project', name: 'Voice', created_at: '2026-01-01', updated_at: '2026-01-01', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 5 } } satisfies Project
const response = (payload: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload }) as Response
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }

async function mount(policyAvailable = true, batchImport = true) {
  const capabilities = { schema_version: '1.0', workflow: { schema_version: '1.2', nodes: [
    { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
    { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
  ], ports: ['audio'], constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 }, default_graph: {
    nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } }, { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }],
    edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
  } }, asr: { providers: [{ id: 'mock', label: 'Mock' }], status: 'available' } }
  const workflowDocument = { schema_version: '1.0', project_id: 'created-project', revision: 1, workflow_revision: 1, updated_at: '2026-01-01', nodes: [{ id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } }, { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } }], edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }] }
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(String(url).endsWith('/upload-policy')
    ? policyAvailable ? response({ allowed_extensions: ['wav', 'ogg'], max_upload_bytes: 1024 }) : { ok: false }
    : String(url).endsWith('/capabilities') ? response(capabilities)
      : String(url).endsWith('/workflow') ? response(workflowDocument)
        : String(url).endsWith('/workflow-runs?limit=30&offset=0') ? response({ items: [], total: 0, limit: 30, offset: 0 })
          : response({ items: [] }))))
  const pinia = createPinia()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(App).use(pinia)
  app.mount(root)
  await settle()
  const workflow = useWorkflowStore(pinia)
  workflow.capabilities = readCapabilities(capabilities)
  workflow.document = workflowDocument
  workflow.setCleanBaseline(workflowDocument)
  vi.spyOn(workflow, 'load').mockResolvedValue(undefined)
  const button = root.querySelector<HTMLButtonElement>('.page-heading button')!
  button.click()
  await nextTick()
  if (batchImport) { root.querySelector<HTMLButtonElement>('[data-test=batch-import]')!.click(); await nextTick() }
  return { store: useProjectsStore(pinia), detail: useProjectDetailStore(pinia) }
}
async function chooseLanguages(source = 'ja', target = 'zh-CN') {
  const sourceInput = root!.querySelector<HTMLInputElement>('#source-language')!
  sourceInput.value = source
  sourceInput.dispatchEvent(new Event('input', { bubbles: true }))
  const targetInput = root!.querySelector<HTMLInputElement>('#target-language')!
  targetInput.value = target
  targetInput.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

async function select(files: File[]) {
  const batchImport = root!.querySelector<HTMLButtonElement>('[data-test=batch-import]')
  if (batchImport) { batchImport.click(); await nextTick() }
  const picker = root!.querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(picker, 'files', { configurable: true, value: files })
  picker.dispatchEvent(new Event('change', { bubbles: true }))
  await nextTick()
}

async function switchInterface(value: 'zh-CN' | 'en') {
  const picker = root!.querySelector<HTMLSelectElement>('.locale-picker')!
  picker.value = value
  picker.dispatchEvent(new Event('change', { bubbles: true }))
  await nextTick()
}

afterEach(() => {
  app?.unmount()
  app = undefined
  root?.remove()
  root = undefined
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('empty project canvas entry', () => {
  const emptyProject: Project = { ...project, source: null, audios: [], source_language: 'xx-CUSTOM', target_language: 'zh-CN' }

  it('creates an empty project without uploading files or requiring upload policy, then mounts its canvas', async () => {
    const { store, detail } = await mount(false, false)
    const create = vi.spyOn(store, 'createEmptyProject').mockResolvedValue(emptyProject)
    const upload = vi.spyOn(store, 'createProject')
    vi.spyOn(detail, 'load').mockImplementation(async () => { detail.project = emptyProject; detail.audios = []; detail.activeAudioId = null })
    expect(root!.querySelector('.upload-zone')).toBeNull()
    expect(root!.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(false)
    await chooseLanguages('xx-CUSTOM', 'zh-CN')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    expect(root!.textContent).toContain('请输入项目名称')
    const name = root!.querySelector<HTMLInputElement>('#project-name')!
    name.value = 'Empty canvas'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create).toHaveBeenCalledWith('Empty canvas', 'xx-CUSTOM', 'zh-CN')
    expect(upload).not.toHaveBeenCalled()
    expect(detail.load).toHaveBeenCalledWith(project.id, undefined)
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(root!.querySelector('.workflow-empty.workspace-empty')).toBeNull()
    expect(root!.querySelector('.workflow-audio-picker')).toBeNull()
  })

  it('waits for the audio list before mounting the editor and automatically loads its initial history context', async () => {
    const { detail } = await mount()
    const workflow = useWorkflowStore()
    vi.mocked(workflow.load).mockRestore()
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
    const { detail } = await mount(true, false)
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

  it('keeps the empty-project form and language validation when changing interface language', async () => {
    const { store } = await mount(true, false)
    const create = vi.spyOn(store, 'createEmptyProject')
    await chooseLanguages('ja', 'JA')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    expect(root!.textContent).toContain('源语言与目标语言不能相同')
    await switchInterface('en')
    expect(root!.textContent).toContain('Source and target languages must differ.')
    expect(root!.querySelector('button[type=submit]')?.textContent).toBe('Create project and open canvas')
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.value).toBe('ja')
    expect(create).not.toHaveBeenCalled()
  })

  it('renders empty projects with zero audio resources while retaining legacy source metadata', async () => {
    const { store } = await mount(true, false)
    vi.spyOn(store, 'fetchProjects').mockResolvedValue(undefined)
    store.projects = [emptyProject, project, { ...emptyProject, id: 'uploaded-empty', audios: [{ id: 'aud_new', source: { ...project.source, original_name: 'new-resource.wav' } }] }]
    ;(app!._instance!.exposed as { showList: () => void }).showList()
    await nextTick()
    const cards = root!.querySelectorAll('.project-card')
    expect(cards[0]!.textContent).toContain('0 条音频 · 0 KB')
    expect(cards[0]!.textContent).toContain('尚无音频资源')
    expect(cards[0]!.textContent).toContain('XX-CUSTOM')
    expect(cards[1]!.textContent).toContain('voice.wav')
    expect(cards[1]!.textContent).toContain('1 条音频')
    expect(cards[2]!.textContent).toContain('new-resource.wav')
    expect(cards[2]!.textContent).toContain('1 条音频')
    await switchInterface('en')
    expect(cards[0]!.textContent).toContain('0 audio files · 0 KB')
    expect(cards[0]!.textContent).toContain('No audio resources yet')
  })
})

describe('upload view integration', () => {
  it('switches form, validation, queue labels and confirmations without resetting user input or files', async () => {
    await mount()
    const name = root!.querySelector<HTMLInputElement>('#project-name')!
    name.value = '自定义 Project'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    await chooseLanguages('xx-CUSTOM', 'zh-CN')
    const selected = new File(['x'], '原始 bad.exe')
    await select([selected])
    const row = root!.querySelector('.upload-row')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await switchInterface('en')
    expect(locale.value).toBe('en')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
    expect(root!.querySelector('.locale-picker')?.getAttribute('aria-label')).toBe('Interface language')
    expect(root!.querySelector('.brand')?.getAttribute('aria-label')).toBe('VoiceTransl home')
    expect(root!.querySelector('h1')?.textContent).toBe('New project')
    expect(name.value).toBe('自定义 Project')
    expect(name.placeholder).toBe('Use the first valid filename if left blank')
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.value).toBe('xx-CUSTOM')
    expect(root!.querySelector<HTMLInputElement>('#target-language')!.value).toBe('zh-CN')
    expect(root!.querySelector('.upload-row')).toBe(row)
    expect(row?.textContent).toContain('原始 bad.exe')
    expect(row?.textContent).toContain('Unsupported format')
    expect(root!.querySelector('button[aria-label="Remove 原始 bad.exe"]')).not.toBeNull()
    Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === 'Clear queue')!.click()
    expect(confirm).toHaveBeenLastCalledWith('Clear this upload queue and start a new project? Uploaded audio will remain in its original project.')
    await switchInterface('zh-CN')
    expect(root!.querySelector('.upload-row')).toBe(row)
    expect(row?.textContent).toContain('格式不支持')
    expect(name.value).toBe('自定义 Project')
  })

  it('keeps the language selector usable during uploads and preserves the frozen request snapshot', async () => {
    const { store, detail } = await mount()
    let release!: (value: Project) => void
    const create = vi.spyOn(store, 'createProject').mockImplementation(() => new Promise<Project>((resolve) => { release = resolve }))
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    const file = new File(['x'], 'voice.wav')
    await select([file])
    await chooseLanguages('ja', 'zh-CN')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    const form = root!.querySelector('form')
    const picker = root!.querySelector<HTMLSelectElement>('.locale-picker')!
    expect(picker.disabled).toBe(false)
    await switchInterface('en')
    expect(root!.querySelector('form')).toBe(form)
    expect(root!.querySelector('button[type=submit]')?.textContent).toBe('Uploading audio…')
    expect(root!.querySelector('.upload-status')?.textContent).toBe('Uploading')
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.value).toBe('ja')
    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0]).toEqual([file, 'voice', 'ja', 'zh-CN'])
    release(project)
    await settle()
    root!.querySelector<HTMLButtonElement>('button[aria-label="Back to upload queue"]')!.click()
    await nextTick()
    expect(root!.textContent).toContain('Project name: voice · Source language: JA')
    expect(root!.querySelector('button[type=submit]')?.textContent).toBe('Upload complete')
  })

  it('retranslates already visible form and upload-policy errors', async () => {
    await mount(false)
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    expect(root!.textContent).toContain('请选择或输入源语言')
    await switchInterface('en')
    expect(root!.querySelector('.upload-feedback')?.textContent).toBe('Select or enter the source language.')
    expect(root!.querySelector('.upload-policy-error')?.textContent).toContain('Cannot load upload limits.')
    expect(root!.querySelector('.upload-policy-error')?.textContent).toContain('Retry loading limits')
  })

  it('uses singular and plural English audio file labels in the same unchanged queue', async () => {
    await mount()
    await switchInterface('en')
    await select([new File(['x'], 'one.wav')])
    expect(root!.querySelector('button[type=submit]')?.textContent).toBe('Upload 1 audio file to a new project')
    await select([new File(['y'], 'two.wav')])
    expect(root!.querySelector('button[type=submit]')?.textContent).toBe('Upload 2 audio files to a new project')
    expect(root!.querySelector('.upload-queue-heading')?.textContent).toContain('Valid: 2 / Selected: 2')
  })

  it('localizes list pagination, counts, dates and deletion while preserving server project data', async () => {
    const { store } = await mount()
    vi.spyOn(store, 'fetchProjects').mockResolvedValue(undefined)
    const stored = { ...project, source: { ...project.source, language: 'xx-CUSTOM' } }
    store.projects = [stored]
    store.total = 1200
    store.offset = 20
    ;(app!._instance!.exposed as { showList: () => void }).showList()
    await nextTick()
    const card = root!.querySelector('.project-card')
    await switchInterface('en')
    expect(root!.querySelector('.project-card')).toBe(card)
    expect(card?.textContent).toContain('1 audio file')
    expect(card?.textContent).toContain('XX-CUSTOM')
    expect(root!.querySelector('.project-date')?.textContent).toBe(new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(project.updated_at)))
    expect(root!.querySelector('nav[aria-label="Project pagination"]')?.textContent).toContain('Page 2 / 60 · Projects: 1,200')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    root!.querySelector<HTMLButtonElement>('button[aria-label="Delete project"]')!.click()
    expect(confirm).toHaveBeenCalledWith('Delete project “Voice”? This removes all audio, subtitles, and output files in the project.')
    expect(store.projects).toEqual([stored])
    expect(store.offset).toBe(20)
  })

  it('keeps the form unavailable until accurate server limits can be fetched', async () => {
    await mount(false)
    await select([new File(['x'], 'voice.wav')])
    expect(root!.querySelector<HTMLInputElement>('input[type=file]')!.multiple).toBe(true)
    expect(root!.textContent).toContain('无法获取上传限制')
    expect(root!.textContent).toContain('0 个有效 / 1 个已选')
    expect(root!.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(true)
  })

  it('guards internal navigation, browser unload, and form mutations throughout uploading', async () => {
    const { store, detail } = await mount()
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    let release: ((value: Project) => void) | undefined
    vi.spyOn(store, 'createProject').mockImplementation(() => new Promise<Project>((resolve) => { release = resolve }))
    const append = vi.spyOn(store, 'addAudio').mockImplementation(() => new Promise((resolve) => { release = () => resolve({ id: 'aud_second', source: project.source }) }))
    await select([new File(['x'], 'first.wav'), new File(['y'], 'second.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await nextTick()
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.disabled).toBe(true)
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
    await nextTick()
    expect(root!.querySelector('.create-form')).not.toBeNull()
    release!(project)
    await settle()
    release!({ ...project, id: 'second-project' })
    await settle()
    expect(append.mock.calls[0][0]).toBe('created-project')
    expect(detail.load).toHaveBeenCalledWith('created-project', undefined)
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    expect(root!.textContent).toContain('2 条已上传')
    expect(root!.querySelector('button[type=submit]')!.textContent).toBe('上传完成')
    const finishedUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(finishedUnload)
    expect(finishedUnload.defaultPrevented).toBe(false)
  })

  it('requires explicit source and target languages before creating a project, while accepting custom codes', async () => {
    const { store, detail } = await mount()
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    const create = vi.spyOn(store, 'createProject').mockResolvedValue(project)
    await select([new File(['x'], 'voice.wav')])
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create).not.toHaveBeenCalled()
    expect(root!.textContent).toContain('请选择或输入源语言')
    await chooseLanguages('xx-CUSTOM', 'zh-CN')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create.mock.calls[0].slice(1)).toEqual(['voice', 'xx-CUSTOM', 'zh-CN'])
  })

  it('opens a successful single file in the existing workspace and retains its queue on return', async () => {
    const { store, detail } = await mount()
    vi.spyOn(store, 'createProject').mockResolvedValue(project)
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'voice.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(detail.load).toHaveBeenCalledWith('created-project', undefined)
    expect(root!.querySelector('.workspace')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    expect(root!.textContent).toContain('1 条已上传')
    expect(root!.textContent).toContain('打开项目')
  })

  it('keeps multi-file project naming editable and opens one workspace after every audio uploads', async () => {
    const { store, detail } = await mount()
    const create = vi.spyOn(store, 'createProject').mockResolvedValue(project)
    const append = vi.spyOn(store, 'addAudio').mockResolvedValue({ id: 'aud_second', source: project.source })
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'first.wav'), new File(['y'], 'second.wav')])
    await chooseLanguages()
    const input = root!.querySelector<HTMLInputElement>('#project-name')!
    expect(input.disabled).toBe(false)
    input.value = 'Shared album'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create.mock.calls[0].slice(1)).toEqual(['Shared album', 'ja', 'zh-CN'])
    expect(create).toHaveBeenCalledTimes(1)
    expect(append.mock.calls[0][0]).toBe(project.id)
    expect(detail.load).toHaveBeenCalledTimes(1)
    expect(detail.load).toHaveBeenCalledWith(project.id, undefined)
  })

  it('creates a new parent when choosing New project from the list after a completed upload', async () => {
    const { store, detail } = await mount()
    const create = vi.spyOn(store, 'createProject').mockResolvedValueOnce(project).mockResolvedValueOnce({ ...project, id: 'new-parent' })
    const append = vi.spyOn(store, 'addAudio').mockResolvedValue({ id: 'unused', source: project.source })
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'first.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    root!.querySelector<HTMLButtonElement>('.workspace-header-back')!.click()
    await settle()
    expect(root!.querySelector('button[aria-label="返回上传队列"]')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(root!.querySelector('.upload-queue')).toBeNull()
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.disabled).toBe(false)
    expect(root!.querySelector('h1')!.textContent).toBe('新建项目')
    await select([new File(['y'], 'new.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create).toHaveBeenCalledTimes(2)
    expect(append).not.toHaveBeenCalled()
    expect(detail.load).toHaveBeenLastCalledWith('new-parent', undefined)
  })

  it('asks before discarding unfinished uploads and preserves the queue if the user declines', async () => {
    await mount()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await select([new File(['x'], 'pending.wav')])
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
    await settle()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(root!.querySelector('.list-layout')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    expect(root!.textContent).toContain('pending.wav')
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
    await settle()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(root!.querySelector('.upload-queue')).toBeNull()
    expect(root!.textContent).not.toContain('pending.wav')
  })

  it('retains partial batch results and retries a failure without creating the successful project again', async () => {
    const { store, detail } = await mount()
    const create = vi.spyOn(store, 'createProject').mockResolvedValue(project)
    const append = vi.spyOn(store, 'addAudio').mockRejectedValueOnce(new Error('文件格式不支持')).mockResolvedValueOnce({ id: 'aud_retry', source: project.source })
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'first.wav'), new File(['y'], 'second.wav'), new File(['z'], 'invalid.exe')])
    await chooseLanguages()
    expect(root!.textContent).toContain('2 个有效 / 3 个已选')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(detail.load).not.toHaveBeenCalled()
    expect(root!.textContent).toContain('1 条已上传')
    expect(root!.textContent).toContain('1 条上传失败')
    expect(root!.textContent).toContain('文件格式不支持')
    expect(root!.querySelector('button[type=submit]')!.textContent).toBe('请重试失败文件')
    root!.querySelector<HTMLButtonElement>('.upload-row button')!.click()
    await settle()
    expect(detail.load).toHaveBeenCalledWith(project.id, 'aud_primary')
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    expect(root!.textContent).toContain('1 条已上传')
    expect(root!.textContent).toContain('1 条上传失败')
    expect(root!.textContent).toContain('invalid.exe')
    expect(root!.textContent).toContain('文件格式不支持')
    root!.querySelector<HTMLButtonElement>('button[aria-label="重试上传 second.wav"]')!.click()
    await settle()
    expect(create).toHaveBeenCalledTimes(1)
    expect(append).toHaveBeenCalledTimes(2)
    expect(append.mock.calls[1][0]).toBe('created-project')
    expect(append.mock.calls[1][1].name).toBe('second.wav')
    expect(root!.textContent).toContain('2 条已上传')
    expect(root!.querySelectorAll('.upload-row button').length).toBe(3)
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

  it('reopens the same project from the upload queue without re-entering the navigation guard', async () => {
    const { store, detail } = await mount()
    const other = { ...project, id: 'other-project', name: 'Other project' }
    const otherAudio = { id: 'aud_primary', source: { ...project.source, original_name: 'other.wav' } }
    vi.spyOn(store, 'createProject').mockResolvedValue(project)
    const load = vi.spyOn(detail, 'load').mockImplementation(async (id: string) => {
      detail.project = id === other.id ? other : project
      detail.audios = [id === other.id ? otherAudio : { id: 'aud_primary', source: project.source }]
      detail.activeAudioId = 'aud_primary'
      detail.loading = false
    })
    await select([new File(['x'], 'voice.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(root!.querySelector('.workspace')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    const openOther = Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === '打开项目')
    expect(openOther).toBeTruthy()
    openOther!.click()
    await settle()
    expect(load.mock.calls.map(([id]) => id)).toEqual([project.id, project.id])
    expect(detail.project?.id).toBe(project.id)
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
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
