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
const project: Project = { id: 'created-project', name: 'Voice', created_at: '2026-01-01', updated_at: '2026-01-01', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 5 } }
const response = (payload: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload }) as Response
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }

async function mount(policyAvailable = true) {
  const capabilities = { schema_version: '1.0', workflow: { nodes: [
    { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
    { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
  ], ports: ['audio'] }, asr: { providers: [{ id: 'mock', label: 'Mock' }], status: 'available' } }
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
    const { store } = await mount()
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
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
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

  it('adds audio from a project workspace to the same project and resets the target for a new project', async () => {
    const { store, detail } = await mount()
    const create = vi.spyOn(store, 'createProject').mockResolvedValue(project)
    const append = vi.spyOn(store, 'addAudio').mockResolvedValue({ id: 'aud_extra', source: project.source })
    vi.spyOn(detail, 'load').mockImplementation(async () => { detail.project = project })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await select([new File(['x'], 'voice.wav')])
    await chooseLanguages()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === '添加音频')!.click()
    await nextTick()
    expect(root!.textContent).toContain('添加音频 · Voice')
    expect(root!.querySelector('#target-language')).toBeNull()
    expect(root!.querySelector('#source-language')).not.toBeNull()
    await select([new File(['y'], 'more.wav')])
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create).toHaveBeenCalledTimes(1)
    expect(append.mock.calls[0][0]).toBe(project.id)
    root!.querySelector<HTMLButtonElement>('button[aria-label="返回上传队列"]')!.click()
    await nextTick()
    Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === '清空队列')!.click()
    await nextTick()
    expect(root!.textContent).not.toContain('添加音频 · Voice')
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.disabled).toBe(false)
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
    const capabilities = { schema_version: '1.0', workflow: { nodes: [
      { type: 'audio_input', label: '音频输入', id_prefix: 'input', input_ports: [], output_ports: ['audio'], terminal: false, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] }, position_x: 96 },
      { type: 'audio_output', label: '音频输出', id_prefix: 'output', input_ports: ['audio'], output_ports: [], terminal: true, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] }, position_x: 420 },
    ], ports: ['audio'] }, asr: { providers: [{ id: 'mock', label: 'Mock' }] }, export: { formats: [], naming_modes: [] } }
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
    workflow.updateNode('input', { config: {} })
    workflow.markDirty()
    await nextTick()
    // App navigation tests exercise the page-level guards; the child editor's modal flow is covered separately.
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    return { ...state, workflow }
  }

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
    expect(root!.querySelector('select[aria-label="Switch node workflow audio"]')).not.toBeNull()
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
    expect(root!.querySelector('.workspace')).not.toBeNull()
    expect(root!.querySelector('.back-button')).not.toBeNull()
    expect(root!.querySelector('button[aria-label="新建项目"]')).not.toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).some((item) => item.textContent?.trim() === '返回项目工作区')).toBe(false)
  })

  it('selects the requested audio through the node workspace audio picker', async () => {
    const state = await mountWorkflow()
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="切换节点工作流音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(state.detail.activeAudioId).toBe('aud_second')
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
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

  it('opens the add-audio view once with a single confirmation and target setup', async () => {
    await mountWorkflow()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const exposed = app!._instance!.exposed as { addProjectAudio: () => void }
    exposed.addProjectAudio()
    await nextTick()
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.value).toBe('Voice')
    await select([new File(['x'], 'pending.wav')])
    expect(root!.textContent).toContain('pending.wav')
    findButton('打开项目')!.click()
    await settle()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    exposed.addProjectAudio()
    await settle()
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(root!.querySelector('.upload-queue')).toBeNull()
    expect(root!.querySelector('h1')?.textContent).toBe('添加音频 · Voice')
    expect(root!.querySelector<HTMLInputElement>('#project-name')!.value).toBe('Voice')
    expect(root!.querySelector('#target-language')).toBeNull()
  })

  it('does not expose legacy pipeline mode controls after switching audio', async () => {
    await mountWorkflow()
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="切换节点工作流音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(root!.querySelector('.workspace-mode-switch')).toBeNull()
    expect(root!.querySelector('.pipeline-panel')).toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
  })
})
