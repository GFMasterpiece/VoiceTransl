import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'

let app: VueApp | undefined
let root: HTMLDivElement | undefined
const project: Project = { id: 'created-project', name: 'Voice', preset: 'mock', created_at: '2026-01-01', updated_at: '2026-01-01', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 5 } }
const response = (payload: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload }) as Response
async function settle() { for (let i = 0; i < 8; i++) await Promise.resolve(); await nextTick() }

async function mount(policyAvailable = true) {
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(String(url).endsWith('/upload-policy')
    ? policyAvailable ? response({ allowed_extensions: ['wav', 'ogg'], max_upload_bytes: 1024 }) : { ok: false }
    : response({ items: [] }))))
  const pinia = createPinia()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(App).use(pinia)
  app.mount(root)
  await settle()
  const button = root.querySelector<HTMLButtonElement>('.page-heading button')!
  button.click()
  await nextTick()
  return { store: useProjectsStore(pinia), detail: useProjectDetailStore(pinia) }
}

async function select(files: File[]) {
  const picker = root!.querySelector<HTMLInputElement>('input[type=file]')!
  Object.defineProperty(picker, 'files', { configurable: true, value: files })
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

  it('opens a successful single file in the existing workspace and retains its queue on return', async () => {
    const { store, detail } = await mount()
    vi.spyOn(store, 'createProject').mockResolvedValue(project)
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'voice.wav')])
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
    const input = root!.querySelector<HTMLInputElement>('#project-name')!
    expect(input.disabled).toBe(false)
    input.value = 'Shared album'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(create.mock.calls[0].slice(1)).toEqual(['Shared album', 'ja'])
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
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent === '添加音频')!.click()
    await nextTick()
    expect(root!.textContent).toContain('添加音频 · Voice')
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
      if (path.endsWith('/projects')) return Promise.resolve(response({ items: [project] }))
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
    vi.spyOn(detail, 'load').mockImplementation(async () => {
      detail.project = project
      detail.audios = [{ ...firstAudio, job: jobFor(firstAudio.id) }, { ...secondAudio, job: jobFor(secondAudio.id) }]
      detail.activeAudioId = firstAudio.id
      detail.loading = false
    })
    vi.spyOn(detail, 'selectAudio').mockImplementation(async (id: string) => {
      detail.activeAudioId = id
      detail.job = jobFor(id)
      detail.loading = false
    })
    root!.querySelector<HTMLElement>('.project-card')!.click()
    detail.project = project
    detail.audios = [{ ...firstAudio, job: jobFor(firstAudio.id) }, { ...secondAudio, job: jobFor(secondAudio.id) }]
    detail.activeAudioId = firstAudio.id
    detail.loading = false
    await nextTick()
    expect(root!.querySelector('.workspace')).not.toBeNull()
    expect(findButton('节点工作流 首版')).not.toBeUndefined()
    return { pinia, detail }
  }

  function jobFor(id: string) {
    return {
      id: `job-${id}`, project_id: project.id, audio_id: id, status: 'failed' as const, progress: 0,
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', cancel_requested: false,
      stages: {
        preprocess: { status: 'failed' as const, progress: 0 },
        asr: { status: 'pending' as const, progress: 0 },
        alignment: { status: 'pending' as const, progress: 0 },
        translation: { status: 'pending' as const, progress: 0 },
        export: { status: 'pending' as const, progress: 0 },
      },
    }
  }

  function findButton(text: string) {
    return Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.trim() === text)
  }

  async function openDirtyWorkflow() {
    const state = await mountWorkflow()
    findButton('节点工作流 首版')!.click()
    await settle()
    const workflow = useWorkflowStore(state.pinia)
    workflow.markDirty()
    workflow.draft.nodes[0].position.x += 10
    await nextTick()
    return { ...state, workflow }
  }

  it('asks before switching tabs with a dirty workflow and stays when the confirmation is cancelled', async () => {
    await openDirtyWorkflow()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    findButton('五阶段流程')!.click()
    await settle()
    expect(confirm).not.toHaveBeenCalled()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    expect(findButton('校验并保存')).not.toBeUndefined()
    findButton('继续编辑')!.click()
    await nextTick()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(root!.querySelector('.workflow-discard')).toBeNull()
  })

  it('uses the same explicit discard flow for returning to the project list and creating a project', async () => {
    await openDirtyWorkflow()
    root!.querySelector<HTMLButtonElement>('.back-button')!.click()
    await settle()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    expect(root!.querySelector('.workspace')).not.toBeNull()
    findButton('继续编辑')!.click()
    await nextTick()
    root!.querySelector<HTMLAnchorElement>('.brand')!.click()
    await settle()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    findButton('继续编辑')!.click()
    await nextTick()
    root!.querySelector<HTMLButtonElement>('button[aria-label="新建项目"]')!.click()
    await nextTick()
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    findButton('放弃修改')!.click()
    await settle()
    expect(root!.querySelector('.create-form')).not.toBeNull()
  })

  it('switches workflow audio only after the dirty draft is explicitly discarded', async () => {
    const { detail } = await openDirtyWorkflow()
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="切换节点工作流音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(detail.activeAudioId).toBe('aud_primary')
    expect(root!.querySelector('.workflow-discard')).not.toBeNull()
    findButton('放弃修改')!.click()
    await settle()
    expect(detail.activeAudioId).toBe('aud_second')
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
  })

  it('switches to another audio without stopping its live pipeline connection', async () => {
    const { detail } = await openDirtyWorkflow()
    const picker = root!.querySelector<HTMLSelectElement>('select[aria-label="切换节点工作流音频"]')!
    picker.value = 'aud_second'
    picker.dispatchEvent(new Event('change', { bubbles: true }))
    await settle()
    expect(findButton('放弃修改')).not.toBeUndefined()
    const stop = vi.spyOn(detail, 'stop')
    findButton('放弃修改')!.click()
    await settle()
    expect(detail.activeAudioId).toBe('aud_second')
    expect(root!.querySelector('.node-workflow')).not.toBeNull()
    expect(stop).not.toHaveBeenCalled()
  })
})
