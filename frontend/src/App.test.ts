import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'

let app: VueApp | undefined
let root: HTMLDivElement | undefined
const project: Project = { id: 'created-project', name: 'Voice', preset: 'mock', created_at: '2026-01-01', updated_at: '2026-01-01', source: { original_name: 'voice.wav', language: 'ja', size_bytes: 5 } }
const response = (payload: unknown) => ({ ok: true, json: async () => payload }) as Response
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
    const { store } = await mount()
    let release: ((value: Project) => void) | undefined
    vi.spyOn(store, 'createProject').mockImplementation(() => new Promise<Project>((resolve) => { release = resolve }))
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
    expect(root!.textContent).toContain('2 个已创建')
    expect(root!.querySelector('button[type=submit]')!.textContent).toBe('创建完成')
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
    expect(detail.load).toHaveBeenCalledWith('created-project')
    expect(root!.querySelector('.workspace')).not.toBeNull()
    root!.querySelector<HTMLButtonElement>('.page-heading button')!.click()
    await nextTick()
    expect(root!.textContent).toContain('1 个已创建')
    expect(root!.textContent).toContain('打开项目')
  })

  it('retains partial batch results and retries a failure without creating the successful project again', async () => {
    const { store, detail } = await mount()
    const create = vi.spyOn(store, 'createProject').mockResolvedValueOnce(project).mockRejectedValueOnce(new Error('文件格式不支持')).mockResolvedValueOnce({ ...project, id: 'retry-project' })
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    await select([new File(['x'], 'first.wav'), new File(['y'], 'second.wav'), new File(['z'], 'invalid.exe')])
    expect(root!.textContent).toContain('2 个有效 / 3 个已选')
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true }))
    await settle()
    expect(detail.load).not.toHaveBeenCalled()
    expect(root!.textContent).toContain('1 个已创建')
    expect(root!.textContent).toContain('1 个创建失败')
    expect(root!.textContent).toContain('文件格式不支持')
    expect(root!.querySelector('button[type=submit]')!.textContent).toBe('请重试失败文件')
    root!.querySelector<HTMLButtonElement>('button[aria-label="重试上传 second.wav"]')!.click()
    await settle()
    expect(create).toHaveBeenCalledTimes(3)
    expect(create.mock.calls[2][0].name).toBe('second.wav')
    expect(root!.textContent).toContain('2 个已创建')
    expect(root!.querySelectorAll('.upload-row button').length).toBe(3)
  })
})
