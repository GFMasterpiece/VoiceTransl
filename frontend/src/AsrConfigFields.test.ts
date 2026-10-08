import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, nextTick, ref, type App as VueApp } from 'vue'
import AsrConfigFields from './components/AsrConfigFields.vue'
import type { PipelineConfig } from './stores/projectDetail'

const endpoint = '/api/v1/settings/local-asr/models'
const initialConfig: PipelineConfig['asr'] = { provider: 'crispasr', backend: 'qwen3-1.7b', model: 'legacy.gguf', language: 'ja', vad_enabled: true, chunk_seconds: 30 }
const catalog = {
  provider: 'crispasr', executable: { ok: true, version: '0.8.38', message: 'CrispASR 可用' }, model_dir: './crispasr/models', restart_required: false,
  backends: ['qwen3', 'qwen3-1.7b'], truncated: false, warnings: [],
  items: [
    { model: 'Qwen3-ASR-0.6B.gguf', backend: 'qwen3', size_bytes: 1024 * 1024, architecture: 'qwen3asr', selectable: true, reason: null },
    { model: 'unknown.gguf', backend: null, size_bytes: 500, architecture: null, selectable: false, reason: '无法确定识别后端，保留手动文件名' },
  ],
}
const response = (value: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => value }) as Response
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
async function settle() { for (let i = 0; i < 20; i++) await Promise.resolve(); await nextTick() }
let app: VueApp | undefined
let root: HTMLDivElement | undefined
function picker() { return root!.querySelector<HTMLSelectElement>('#asr-discovered-model')! }
function refresh() { root!.querySelector<HTMLButtonElement>('.asr-catalog-heading button')!.click() }
async function choose(value = 'Qwen3-ASR-0.6B.gguf') { picker().value = value; picker().dispatchEvent(new Event('change', { bubbles: true })); await nextTick() }
function expectNoPrivatePaths() { for (const prefix of ['/home/', '/Users/', 'C:\\Users\\']) expect(root!.outerHTML).not.toContain(prefix) }

async function mount(override?: (url: string, init?: RequestInit) => Promise<Response>, values = initialConfig, locked = false) {
  const fetch = vi.fn(override || (() => Promise.resolve(response(catalog))))
  vi.stubGlobal('fetch', fetch)
  const config = ref(structuredClone(values))
  const audioKey = ref('p1/a1')
  const isLocked = ref(locked)
  const changes = vi.fn((patch: Partial<PipelineConfig['asr']>) => { config.value = { ...config.value, ...patch } })
  const Harness = defineComponent({ components: { AsrConfigFields }, setup() { return { config, audioKey, isLocked, changes } }, template: '<AsrConfigFields :config="config" :audio-key="audioKey" :locked="isLocked" @change="changes" />' })
  root = document.createElement('div'); document.body.append(root)
  app = createApp(Harness); app.mount(root); await settle()
  return { fetch, config, audioKey, isLocked, changes }
}
afterEach(() => { app?.unmount(); app = undefined; root?.remove(); root = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('local ASR model discovery configuration', () => {
  it('requests the catalog only for CrispASR, retaining an existing model without any automatic changes', async () => {
    const { fetch, config, changes } = await mount()
    expect(fetch.mock.calls).toHaveLength(1)
    expect(fetch.mock.calls[0][0]).toBe(endpoint)
    expect(fetch.mock.calls[0][1]?.method).toBeUndefined()
    expect(fetch.mock.calls[0][1]?.body).toBeUndefined()
    expect(config.value).toEqual(initialConfig)
    expect(changes).not.toHaveBeenCalled()
    expect(picker().value).toBe('')
    expect(root!.textContent).toContain('当前文件名「legacy.gguf」未匹配可用模型，已保留')
    expect(root!.textContent).toContain('发现文件不代表已验证推理可用')
    expectNoPrivatePaths()
  })

  it('explicitly selects qwen3 and its filename together, preserving language and other parameters without saving', async () => {
    const { config, changes, fetch } = await mount()
    await choose()
    expect(changes).toHaveBeenCalledTimes(1)
    expect(changes).toHaveBeenCalledWith({ model: 'Qwen3-ASR-0.6B.gguf', backend: 'qwen3' })
    expect(config.value).toEqual({ ...initialConfig, model: 'Qwen3-ASR-0.6B.gguf', backend: 'qwen3' })
    expect(picker().value).toBe('Qwen3-ASR-0.6B.gguf')
    expect(root!.textContent).toContain('Qwen3 ASR（通用）')
    expect(fetch.mock.calls).toHaveLength(1)
  })

  it('retains the manual fields and original language behavior while Mock needs no catalog service', async () => {
    const { fetch, config, changes } = await mount(() => Promise.reject(new TypeError('offline')), { ...initialConfig, provider: 'mock' })
    expect(fetch).not.toHaveBeenCalled()
    expect(root!.querySelector('.asr-model-catalog')).toBeNull()
    const inputs = root!.querySelectorAll<HTMLInputElement>('input:not([type])')
    inputs[0].value = 'manual-model'; inputs[0].dispatchEvent(new Event('input', { bubbles: true }))
    inputs[1].value = 'en'; inputs[1].dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    expect(config.value.model).toBe('manual-model')
    expect(config.value.language).toBe('en')
    expect(changes).toHaveBeenCalledTimes(2)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('disables unknown and runtime-unsupported entries and never clears an old filename', async () => {
    const { config, changes } = await mount(() => Promise.resolve(response({ ...catalog, backends: ['qwen3-1.7b'] })))
    expect(picker().disabled).toBe(true)
    expect(Array.from(picker().options).filter((option) => option.value).every((option) => option.disabled)).toBe(true)
    await choose('unknown.gguf'); await choose()
    expect(config.value).toEqual(initialConfig)
    expect(changes).not.toHaveBeenCalled()
    expect(root!.textContent).toContain('无法确定识别后端')
    expect(root!.textContent).toContain('没有可选模型')
    expect(root!.querySelector<HTMLInputElement>('input:not([type])')!.disabled).toBe(false)
  })

  it('explains empty directories and preserves manual fallback values', async () => {
    const { config, changes } = await mount(() => Promise.resolve(response({ ...catalog, items: [] })))
    expect(root!.textContent).toContain('当前目录没有发现模型')
    expect(root!.querySelector('#asr-discovered-model')).toBeNull()
    expect(config.value.model).toBe('legacy.gguf')
    expect(changes).not.toHaveBeenCalled()
  })

  it('shows executable failure and pending restart against the current safe directory', async () => {
    const { config } = await mount(() => Promise.resolve(response({ ...catalog, model_dir: '@root/1/models', restart_required: true, executable: { ok: false, version: null, message: '可执行文件不存在，请检查配置' }, truncated: true, warnings: ['目录扫描达到条目限额'] })))
    expect(picker().disabled).toBe(true)
    expect(root!.textContent).toContain('CrispASR 不可用')
    expect(root!.textContent).toContain('当前生效目录：@root/1/models')
    expect(root!.textContent).toContain('此列表仍来自当前目录')
    expect(root!.textContent).toContain('列表已截断')
    expect(root!.textContent).toContain('目录扫描达到条目限额')
    expect(config.value.model).toBe('legacy.gguf')
    expectNoPrivatePaths()
  })

  it('allows queued/running users to refresh and inspect but prevents field or model changes', async () => {
    const { config, changes, fetch } = await mount(undefined, initialConfig, true)
    expect(picker().disabled).toBe(true)
    expect(Array.from(root!.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')).every((field) => field.disabled)).toBe(true)
    await choose()
    expect(changes).not.toHaveBeenCalled()
    expect(config.value).toEqual(initialConfig)
    expect(root!.textContent).toContain('任务运行、取消或排队中，只能查看列表')
    expect(root!.querySelector<HTMLButtonElement>('.asr-catalog-heading button')!.type).toBe('button')
    refresh(); await settle()
    expect(fetch).toHaveBeenCalledTimes(2)
  })

  it('tracks replacement config objects without retaining a stale selected model', async () => {
    const { config, fetch, changes } = await mount()
    config.value = { ...initialConfig, model: 'Qwen3-ASR-0.6B.gguf', backend: 'qwen3' }; await nextTick()
    expect(picker().value).toBe('Qwen3-ASR-0.6B.gguf')
    config.value = structuredClone(initialConfig); await nextTick()
    expect(picker().value).toBe('')
    expect(root!.querySelector<HTMLInputElement>('input:not([type])')!.value).toBe('legacy.gguf')
    expect(changes).not.toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('shows loading and retries server directory errors without submitting the workflow', async () => {
    const pending = deferred<Response>()
    const handler = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response(catalog))
    const { fetch, changes } = await mount(handler)
    expect(root!.textContent).toContain('正在检查当前生效目录')
    expect(root!.querySelector<HTMLButtonElement>('.asr-catalog-heading button')!.disabled).toBe(true)
    pending.resolve(response({ error: { code: 'MODEL_DIR_MISSING', message: '模型目录不存在，请检查全局设置' } }, 400)); await settle()
    expect(root!.querySelector('[role="alert"]')!.textContent).toContain('模型目录不存在')
    expect(root!.textContent).toContain('顶部「设置」')
    refresh(); await settle()
    expect(picker()).not.toBeNull()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(changes).not.toHaveBeenCalled()
  })

  it.each(['network', 'json', 'shape', 'private-path', 'private-message'])('handles %s failures without dumping native errors or server paths', async (failure) => {
    const prefix = ['', 'home', 'privacy-fixture'].join('/')
    const handler = () => failure === 'network' ? Promise.reject(new TypeError(`Failed to fetch ${prefix}`))
      : failure === 'json' ? Promise.resolve({ ok: true, json: () => Promise.reject(new SyntaxError(`Unexpected token ${prefix}`)) } as Response)
      : failure === 'shape' ? Promise.resolve(response({ ...catalog, items: [{ ...catalog.items[0], model: 123 }] }))
      : failure === 'private-path' ? Promise.resolve(response({ ...catalog, model_dir: `${prefix}/models` }))
      : Promise.resolve(response({ error: { message: `模型目录不存在：${prefix}/models，请检查设置` } }, 400))
    const { changes } = await mount(handler)
    expect(root!.querySelector('[role="alert"]')).not.toBeNull()
    expect(root!.textContent).toMatch(/重试|检查/)
    expect(root!.outerHTML).not.toMatch(/Failed to fetch|Unexpected token/)
    expectNoPrivatePaths()
    expect(changes).not.toHaveBeenCalled()
  })

  it.each(['invalid filename.gguf', '模型.gguf', `${'x'.repeat(129)}.gguf`])('rejects unsafe catalog filename %s before it can be selected', async (model) => {
    await mount(() => Promise.resolve(response({ ...catalog, items: [{ ...catalog.items[0], model }] })))
    expect(root!.textContent).toContain('模型列表响应不完整')
    expect(root!.querySelector('#asr-discovered-model')).toBeNull()
  })

  it('sanitizes paths in valid warnings, version text, and disabled-item reasons', async () => {
    const privatePath = ['', 'home', 'privacy-fixture', 'models'].join('/')
    await mount(() => Promise.resolve(response({ ...catalog, executable: { ok: true, version: `0.8.38 ${privatePath}`, message: `目录位于 ${privatePath}` }, warnings: [`跳过 ${privatePath}/bad.gguf`], items: [catalog.items[0], { ...catalog.items[1], reason: `不能读取 ${privatePath}/unknown.gguf` }] })))
    expect(root!.textContent).toContain('路径已隐藏')
    expectNoPrivatePaths()
  })

  it('ignores a late provider or audio response, and never changes a different audio', async () => {
    const pending = deferred<Response>()
    const { config, audioKey, fetch, changes } = await mount(vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({ ...catalog, items: [] })))
    config.value = { ...initialConfig, provider: 'mock' }; await settle()
    expect((fetch.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true)
    pending.resolve(response(catalog)); await settle()
    expect(root!.querySelector('.asr-model-catalog')).toBeNull()
    config.value = structuredClone(initialConfig); audioKey.value = 'p1/a2'; await settle()
    expect(root!.textContent).toContain('当前目录没有发现模型')
    expect(config.value).toEqual(initialConfig)
    expect(changes).not.toHaveBeenCalled()
  })

  it('aborts a previous audio request and an unmounted modal request', async () => {
    const pending = deferred<Response>()
    const { audioKey, fetch, changes } = await mount(vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({ ...catalog, items: [] })))
    audioKey.value = 'p1/a2'; await settle()
    expect((fetch.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true)
    pending.resolve(response(catalog)); await settle()
    expect(root!.querySelector('#asr-discovered-model')).toBeNull()
    expect(changes).not.toHaveBeenCalled()
    refresh(); await settle()
    const lastSignal = fetch.mock.calls[fetch.mock.calls.length - 1][1]!.signal as AbortSignal
    app!.unmount(); app = undefined
    expect(lastSignal.aborted).toBe(true)
  })

  it('reports a client timeout with an actionable message', async () => {
    vi.useFakeTimers()
    await mount((_, init) => new Promise((_, reject) => init!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))))
    await vi.advanceTimersByTimeAsync(25000); await settle()
    expect(root!.textContent).toContain('读取本地模型超时')
    expect(root!.textContent).toContain('确认后端服务和 CrispASR 状态后重试')
    expect(root!.querySelector<HTMLButtonElement>('.asr-catalog-heading button')!.disabled).toBe(false)
  })
})
