import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, nextTick, ref, type App as VueApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import GlobalSettingsModal from './components/GlobalSettingsModal.vue'
import { useProjectDetailStore, type PipelineConfig } from './stores/projectDetail'
import { useProjectsStore } from './stores/projects'
import stylesheet from './style.css?raw'

const endpoint = '/api/v1/settings/local-asr'
const original = {
  crispasr_executable: './crispasr/crispasr', crispasr_model_dir: '@root/1/models',
  active: { crispasr_executable: './crispasr/crispasr', crispasr_model_dir: '@root/1/models' },
  managed_by_environment: { crispasr_executable: false, crispasr_model_dir: false },
  restart_required: false, editable: true, allowed_roots: ['./crispasr', '@root/1'], revision: 'rev-1',
}
const check = { executable: { ok: true, message: '可执行文件可用', version: '0.8.38' }, model_dir: { ok: true, message: '模型目录可用' } }
const source = { original_name: 'voice.wav', language: 'ja', size_bytes: 5 }
const project = { id: 'p1', name: 'Voice', preset: 'mock', created_at: '2026-01-01', updated_at: '2026-01-01', source }
const config: PipelineConfig = {
  source_language: 'ja', target_language: 'zh-CN',
  preprocess: { provider: 'mock', sample_rate: 16000, channels: 1, preserve_original: true },
  asr: { provider: 'mock', backend: 'qwen3-1.7b', model: 'draft-asr', language: 'ja', vad_enabled: true, chunk_seconds: 30 },
  alignment: { enabled: true, provider: 'mock', model: 'align-model', fallback: 'keep_asr_timestamps' },
  translation: { provider: 'mock', model: 'translation', target_language: 'zh-CN', base_url: null, api_key_env: null, batch_size: 8, temperature: .2, glossary: {}, instructions: null, timeout_seconds: 120 },
  export: { formats: ['srt'], bilingual: true, naming: 'language' },
}
const response = (value: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => value }) as Response
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
async function settle() { for (let i = 0; i < 20; i++) await Promise.resolve(); await nextTick() }
let app: VueApp | undefined
let root: HTMLDivElement | undefined
function input(key = 'crispasr_executable') { return root!.querySelector<HTMLInputElement>(`#settings-${key}`)! }
async function edit(value: string, key = 'crispasr_executable') { input(key).value = value; input(key).dispatchEvent(new Event('input', { bubbles: true })); await nextTick() }
function button(label: string) { return Array.from(root!.querySelectorAll<HTMLButtonElement>('button')).find((item) => item.textContent?.trim() === label)! }
function submit() { root!.querySelector<HTMLFormElement>('.global-settings-form')!.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true })) }
function expectPrivatePrefixesHidden() {
  for (const prefix of ['/home/', '/Users/', 'C:\\Users\\']) expect(root!.outerHTML).not.toContain(prefix)
}

async function mountModal(handler: (url: string, init?: RequestInit) => Promise<Response> = () => Promise.resolve(response(original))) {
  const fetch = vi.fn(handler)
  vi.stubGlobal('fetch', fetch)
  const Harness = defineComponent({ components: { GlobalSettingsModal }, setup() { return { open: ref(false) } }, template: '<button id="launcher" @click="open = true">设置</button><GlobalSettingsModal v-if="open" @close="open = false" />' })
  root = document.createElement('div'); document.body.append(root)
  app = createApp(Harness); app.mount(root)
  const launch = root.querySelector<HTMLButtonElement>('#launcher')!
  launch.focus(); launch.click(); await settle()
  return { fetch, launch }
}

async function mountApp() {
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url.includes('/settings/local-asr')) return Promise.resolve(response(init?.method === 'POST' ? check : original))
    if (url.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 1024 }))
    return Promise.resolve(response({ items: [project] }))
  })
  vi.stubGlobal('fetch', fetch)
  const pinia = createPinia()
  root = document.createElement('div'); document.body.append(root)
  app = createApp(App).use(pinia); app.mount(root); await settle()
  const detail = useProjectDetailStore(pinia)
  const projects = useProjectsStore(pinia)
  vi.spyOn(detail, 'load').mockImplementation(async () => { detail.project = project; detail.config = structuredClone(config) })
  return { detail, projects, fetch }
}
async function openGlobal() { root!.querySelector<HTMLButtonElement>('button[aria-label="全局设置"]')!.click(); await settle() }
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); root = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('global settings data and requests', () => {
  it('loads exactly two paths, active values, allowed roots, and environment sources', async () => {
    await mountModal(() => Promise.resolve(response({ ...original, crispasr_model_dir: './crispasr/new-models', managed_by_environment: { ...original.managed_by_environment, crispasr_executable: true }, restart_required: true })))
    expect(root!.querySelectorAll('.global-settings-modal input')).toHaveLength(2)
    expect(input().readOnly).toBe(true)
    expect(input('crispasr_model_dir').value).toBe('./crispasr/new-models')
    expect(root!.textContent).toContain('环境变量管理 · 只读')
    expect(root!.textContent).toContain('当前生效')
    expect(root!.textContent).toContain('@root/1/models')
    expect(root!.textContent).toContain('允许的根目录')
    expect(root!.querySelector('.settings-roots')!.textContent).toContain('@root/1')
    expect(root!.querySelector('.settings-active')!.textContent).toContain('@root/1/models')
    expect(root!.textContent).toContain('WSL/Linux 路径')
    expect(root!.textContent).toContain('等待重启后端')
    expect(button('保存设置').disabled).toBe(true)
    expect(document.activeElement).toBe(input('crispasr_model_dir'))
    expectPrivatePrefixesHidden()
  })

  it('shows loading, keeps saves unavailable, and retries failed settings independently', async () => {
    const pending = deferred<Response>()
    const { fetch } = await mountModal(vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response(original)))
    expect(root!.textContent).toContain('正在读取全局设置')
    expect(root!.querySelector('.global-settings-form')).toBeNull()
    pending.resolve(response({ error: { code: 'UNAVAILABLE', message: '配置无法读取' } }, 503)); await settle()
    expect(root!.textContent).toContain('配置无法读取')
    button('重试读取').click(); await settle()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(input().value).toBe(original.crispasr_executable)
  })

  it('checks both draft paths without saving and reports each result independently', async () => {
    const { fetch } = await mountModal((_, init) => Promise.resolve(response(init?.method === 'POST' ? { ...check, model_dir: { ok: false, message: '目录不存在' } } : original)))
    await edit('./crispasr/new-executable')
    button('检查路径').click(); await settle()
    const request = fetch.mock.calls[1]
    expect(request[0]).toBe(`${endpoint}/check`)
    expect(JSON.parse(String(request[1]?.body))).toEqual({ crispasr_executable: './crispasr/new-executable', crispasr_model_dir: original.crispasr_model_dir })
    expect(root!.querySelectorAll('.settings-check-result')).toHaveLength(2)
    expect(root!.textContent).toContain('0.8.38')
    expect(root!.textContent).toContain('目录不存在')
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
    await edit('./crispasr/other')
    expect(root!.querySelector('.settings-check-result')).toBeNull()
  })

  it('saves with the revision and preserves readonly fields, then reports persistence and restart', async () => {
    const settings = { ...original, managed_by_environment: { ...original.managed_by_environment, crispasr_executable: true } }
    const { fetch } = await mountModal((_, init) => Promise.resolve(response(init?.method === 'PUT' ? { ...settings, crispasr_model_dir: '@root/1/new', revision: 'rev-2', restart_required: true } : settings)))
    await edit('@root/1/new', 'crispasr_model_dir')
    expect(button('保存设置').disabled).toBe(false)
    submit(); await settle()
    expect(fetch.mock.calls[1][1]?.method).toBe('PUT')
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ crispasr_executable: original.crispasr_executable, crispasr_model_dir: '@root/1/new', revision: 'rev-1' })
    expect(button('保存设置').disabled).toBe(true)
    expect(root!.textContent).toContain('设置已保存到后端配置文件')
    expect(root!.textContent).toContain('保存不会影响当前任务；新路径需重启后端生效')
    expect(root!.textContent).toContain('请等待运行任务结束后再手动重启')
    expect(root!.querySelector('.settings-active')!.textContent).toContain(original.crispasr_model_dir)
    expectPrivatePrefixesHidden()
  })

  it('uses aliases for check and save and accepts the server canonical representation without creating changes', async () => {
    const normalized = { ...original, crispasr_executable: '@root/1/crispasr', crispasr_model_dir: './crispasr/models', revision: 'rev-2', restart_required: true }
    const { fetch } = await mountModal((_, init) => Promise.resolve(response(init?.method === 'POST' ? check : init?.method === 'PUT' ? normalized : original)))
    await edit('@root/1/bin/../crispasr')
    await edit('./crispasr/models', 'crispasr_model_dir')
    button('检查路径').click(); await settle()
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ crispasr_executable: '@root/1/bin/../crispasr', crispasr_model_dir: './crispasr/models' })
    submit(); await settle()
    expect(JSON.parse(String(fetch.mock.calls[2][1]?.body))).toEqual({ crispasr_executable: '@root/1/bin/../crispasr', crispasr_model_dir: './crispasr/models', revision: 'rev-1' })
    expect(input().value).toBe('@root/1/crispasr')
    expect(button('保存设置').disabled).toBe(true)
    expect(root!.textContent).toContain('没有未保存修改')
    expectPrivatePrefixesHidden()
    const confirm = vi.spyOn(window, 'confirm')
    button('取消').click(); await settle()
    expect(confirm).not.toHaveBeenCalled()
  })

  it('lets a user submit their chosen absolute path and displays the safe representation after saving', async () => {
    const { fetch } = await mountModal((_, init) => Promise.resolve(response(init?.method === 'PUT' ? { ...original, crispasr_executable: '@root/1/crispasr', restart_required: true } : original)))
    const chosen = ['', 'home', 'privacy-fixture', 'crispasr'].join('/')
    await edit(chosen)
    submit(); await settle()
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body)).crispasr_executable).toBe(chosen)
    expect(input().value).toBe('@root/1/crispasr')
    expect(button('保存设置').disabled).toBe(true)
    expectPrivatePrefixesHidden()
  })

  it.each([
    ['load', 'network'], ['check', 'network'], ['save', 'network'],
    ['load', 'json'], ['check', 'json'], ['save', 'json'],
  ])('shows an actionable Chinese error for %s %s failures without raw browser details', async (operation, failure) => {
    const privatePrefix = ['', 'home', 'privacy-fixture'].join('/')
    const failureResponse = () => failure === 'network'
      ? Promise.reject(new TypeError(`Failed to fetch ${privatePrefix}`))
      : Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError(`Unexpected token ${privatePrefix}`)) } as Response)
    await mountModal((_, init) => operation === 'load' || (operation === 'check' && init?.method === 'POST') || (operation === 'save' && init?.method === 'PUT') ? failureResponse() : Promise.resolve(response(original)))
    if (operation === 'check') { button('检查路径').click(); await settle() }
    if (operation === 'save') { await edit('@root/1/new'); submit(); await settle() }
    const alert = root!.querySelector('[role="alert"]')!
    expect(alert.textContent).toContain('失败，请检查后端')
    expect(alert.textContent).toContain('重试')
    expect(root!.outerHTML).not.toContain('Failed to fetch')
    expect(root!.outerHTML).not.toContain('Unexpected token')
    expectPrivatePrefixesHidden()
  })

  it('disables save for blank drafts and a readonly service configuration', async () => {
    await mountModal(() => Promise.resolve(response({ ...original, editable: false })))
    expect(input().readOnly).toBe(true)
    expect(input('crispasr_model_dir').readOnly).toBe(true)
    expect(button('保存设置').disabled).toBe(true)
    expect(root!.textContent).toContain('服务端配置文件不可写')
    app!.unmount(); app = undefined; root!.remove()
    await mountModal(); await edit('   ')
    expect(button('保存设置').disabled).toBe(true)
    expect(button('检查路径').disabled).toBe(true)
    expect(root!.textContent).toContain('请填写两个路径')
  })

  it('retains a conflicted draft and reloads the latest revision without discarding it', async () => {
    const handler = vi.fn().mockResolvedValueOnce(response(original)).mockResolvedValueOnce(response({ error: { code: 'SETTINGS_CONFLICT', message: '设置已由其他操作更改' } }, 409)).mockResolvedValueOnce(response({ ...original, crispasr_executable: './crispasr/elsewhere', revision: 'rev-2' })).mockImplementation(() => Promise.resolve(response(original)))
    const { fetch } = await mountModal(handler)
    await edit('./crispasr/draft'); submit(); await settle()
    expect(input().value).toBe('./crispasr/draft')
    expect(button('保存设置').disabled).toBe(true)
    button('重新加载，保留修改').click(); await settle()
    expect(input().value).toBe('./crispasr/draft')
    expect(root!.textContent).toContain('你的未保存修改仍保留')
    submit(); await settle()
    expect(JSON.parse(String(fetch.mock.calls[3][1]?.body)).revision).toBe('rev-2')
  })

  it('shows server validation errors without resetting draft paths', async () => {
    await mountModal((_, init) => Promise.resolve(init?.method === 'PUT' ? response({ error: { code: 'SETTINGS_PATH_INVALID', message: '路径不在允许的根目录内' } }, 422) : response(original)))
    await edit('@root/99/crispasr'); submit(); await settle()
    expect(input().value).toBe('@root/99/crispasr')
    expect(root!.textContent).toContain('路径不在允许的根目录内')
    expect(button('保存设置').disabled).toBe(false)
    expectPrivatePrefixesHidden()
  })

  it('ignores late checks when the draft changes and on close/reopen', async () => {
    const pending = deferred<Response>()
    const { fetch, launch } = await mountModal((_, init) => init?.method === 'POST' ? pending.promise : Promise.resolve(response(original)))
    button('检查路径').click(); await nextTick()
    await edit('./crispasr/changed')
    expect((fetch.mock.calls[1][1]?.signal as AbortSignal).aborted).toBe(true)
    pending.resolve(response(check)); await settle()
    expect(root!.querySelector('.settings-check-result')).toBeNull()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    button('取消').click(); await settle(); launch.click(); await settle()
    expect(input().value).toBe(original.crispasr_executable)
    expect(root!.querySelector('.settings-check-result')).toBeNull()
  })

  it('ignores a closed session load arriving after a new session has loaded', async () => {
    const pending = deferred<Response>()
    const { launch } = await mountModal(vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response(original)))
    root!.querySelector<HTMLButtonElement>('[aria-label="关闭全局设置"]')!.click(); await settle()
    launch.click(); await settle()
    pending.resolve(response({ ...original, crispasr_executable: './crispasr/stale' })); await settle()
    expect(input().value).toBe(original.crispasr_executable)
  })
})

describe('global settings modal interactions', () => {
  it.each(['cancel', 'escape', 'backdrop'])('confirms unsaved dismissal through %s and restores launcher focus', async (method) => {
    const { launch } = await mountModal()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    await edit('./crispasr/draft')
    const dismiss = () => method === 'cancel' ? button('取消').click() : method === 'escape' ? document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })) : root!.querySelector<HTMLElement>('.global-settings-backdrop')!.click()
    dismiss(); await settle()
    expect(root!.querySelector('[role="dialog"]')).not.toBeNull()
    expect(input().value).toBe('./crispasr/draft')
    dismiss(); await settle()
    expect(confirm).toHaveBeenCalledTimes(2)
    expect(root!.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(launch)
  })

  it('traps keyboard focus and redirects attempts to focus the background', async () => {
    const { launch } = await mountModal()
    expect(document.body.style.overflow).toBe('hidden')
    const close = root!.querySelector<HTMLButtonElement>('[aria-label="关闭全局设置"]')!
    close.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(button('取消'))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(close)
    await edit('@root/1/new')
    button('保存设置').focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(close)
    launch.focus()
    expect(document.activeElement).toBe(close)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    button('取消').click(); await settle()
    expect(document.body.style.overflow).toBe('')
  })

  it('disables duplicate requests and dismissals while a save is being persisted', async () => {
    const pending = deferred<Response>()
    const { fetch } = await mountModal((_, init) => init?.method === 'PUT' ? pending.promise : Promise.resolve(response(original)))
    await edit('@root/1/new'); submit(); await nextTick(); submit()
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1)
    expect(button('取消').disabled).toBe(true)
    expect(input().disabled).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(root!.querySelector('[role="dialog"]')).not.toBeNull()
    pending.resolve(response({ ...original, crispasr_executable: '@root/1/new' })); await settle()
    expect(button('取消').disabled).toBe(false)
  })
})

describe('application settings integration', () => {
  it('keeps the gear entry in list, create, and project views, independent of stage configuration', async () => {
    const { detail } = await mountApp()
    expect(root!.querySelector('[aria-label="全局设置"] svg')).not.toBeNull()
    await openGlobal()
    expect(root!.querySelector('main')!.hasAttribute('inert')).toBe(true)
    button('取消').click(); await settle()
    root!.querySelector<HTMLButtonElement>('[aria-label="新建项目"]')!.click(); await settle()
    await openGlobal()
    expect(root!.querySelector('.create-form')).not.toBeNull()
    button('取消').click(); await settle()
    root!.querySelector<HTMLAnchorElement>('.brand')!.click(); await settle()
    root!.querySelector<HTMLElement>('.project-card')!.click(); await settle()
    detail.configDirty = true
    const originalDraft = JSON.stringify(detail.config)
    const stop = vi.spyOn(detail, 'stop')
    await openGlobal()
    expect(root!.querySelector('.workspace')).not.toBeNull()
    expect(stop).not.toHaveBeenCalled()
    button('取消').click(); await settle()
    expect(JSON.stringify(detail.config)).toBe(originalDraft)
    expect(detail.configDirty).toBe(true)
    root!.querySelector<HTMLButtonElement>('.stage-config-button')!.click(); await settle()
    expect(root!.querySelector('[aria-label="全局设置"]')!.hasAttribute('disabled')).toBe(true)
    expect(root!.querySelector('.global-settings-modal')).toBeNull()
    expect(root!.querySelector('[aria-label="全局设置"]')!.getAttribute('title')).toContain('关闭节点配置')
  })

  it('opens throughout an upload without stopping the upload or erasing the queue', async () => {
    const { projects, detail } = await mountApp()
    root!.querySelector<HTMLButtonElement>('[aria-label="新建项目"]')!.click(); await settle()
    const pending = deferred<typeof project>()
    vi.spyOn(projects, 'createProject').mockReturnValue(pending.promise)
    vi.spyOn(detail, 'load').mockResolvedValue(undefined)
    const picker = root!.querySelector<HTMLInputElement>('input[type=file]')!
    Object.defineProperty(picker, 'files', { configurable: true, value: [new File(['x'], 'upload.wav')] })
    picker.dispatchEvent(new Event('change', { bubbles: true })); await settle()
    root!.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle()
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.disabled).toBe(true)
    await openGlobal()
    expect(root!.querySelector('.global-settings-modal')).not.toBeNull()
    button('取消').click(); await settle()
    expect(root!.textContent).toContain('upload.wav')
    expect(root!.querySelector<HTMLInputElement>('#source-language')!.disabled).toBe(true)
    pending.resolve(project); await settle()
    expect(detail.load).toHaveBeenCalledWith('p1', undefined)
  })

  it('retains task state and project alerts while settings load fails', async () => {
    const { detail, fetch } = await mountApp()
    root!.querySelector<HTMLElement>('.project-card')!.click(); await settle()
    detail.job = { id: 'running', project_id: 'p1', status: 'running', current_stage: 'asr', progress: .4, stages: { preprocess: { status: 'completed', progress: 1 }, asr: { status: 'running', progress: .4 }, alignment: { status: 'pending', progress: 0 }, translation: { status: 'pending', progress: 0 }, export: { status: 'pending', progress: 0 } }, cancel_requested: false, created_at: '2026-01-01', updated_at: '2026-01-01' }
    detail.error = '项目原有提示'
    const stop = vi.spyOn(detail, 'stop')
    fetch.mockImplementationOnce(() => Promise.resolve(response({ error: { code: 'FAILED', message: '全局配置不可读' } }, 500)))
    await openGlobal()
    expect(root!.textContent).toContain('全局配置不可读')
    expect(detail.error).toBe('项目原有提示')
    expect(detail.job.status).toBe('running')
    expect(detail.job.progress).toBe(.4)
    expect(stop).not.toHaveBeenCalled()
    button('关闭').click(); await settle()
    expect(root!.textContent).toContain('项目原有提示')
  })

  it('scopes responsive styles, wraps server paths, and fits narrow modal viewports', () => {
    expect(stylesheet).toMatch(/\.global-settings-modal\s*\{[^}]*width: min\(640px, 100%\)[^}]*overflow-wrap: anywhere/)
    expect(stylesheet).toMatch(/\.global-settings-modal code\s*\{[^}]*overflow-wrap: anywhere/)
    expect(stylesheet).toContain('.global-settings-backdrop { padding: 16px; }')
    expect(stylesheet).toContain('.settings-check-action { align-items: flex-start; flex-direction: column; gap: 8px; }')
  })
})
