import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App as VueApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import App from './App.vue'
import stylesheet from './style.css?raw'
import { useProjectDetailStore, type Job, type PipelineConfig, type StageDetail, type StageName } from './stores/projectDetail'

const names: StageName[] = ['preprocess', 'asr', 'alignment', 'translation', 'export']
const labels = ['预处理', '语音识别', '时间轴对齐', '翻译', '导出']
const source = { original_name: 'voice.wav', language: 'ja', size_bytes: 16000, media_type: 'audio/wav' }
const project = { id: 'p1', name: 'Stage project', source, preset: 'mock', created_at: '2026-01-01', updated_at: '2026-01-01' }
const config: PipelineConfig = {
  source_language: 'ja', target_language: 'zh-CN',
  preprocess: { provider: 'ffmpeg', sample_rate: 16000, channels: 1, preserve_original: true },
  asr: { provider: 'mock', backend: 'qwen3-1.7b', model: 'draft-asr', language: 'ja', vad_enabled: true, chunk_seconds: 30 },
  alignment: { enabled: true, provider: 'mock', model: 'align-model', fallback: 'keep_asr_timestamps' },
  translation: { provider: 'mock', model: 'draft-translation', target_language: 'zh-CN', base_url: null, api_key_env: null, batch_size: 8, temperature: .2, glossary: {}, instructions: null, timeout_seconds: 120 },
  export: { formats: ['srt'], bilingual: true, naming: 'language' },
}
function makeJob(id = 'j1', audioId = 'a1'): Job {
  return { id, audio_id: audioId, project_id: 'p1', status: 'completed', current_stage: null, progress: 1, created_at: '2026-01-01', updated_at: '2026-01-01', cancel_requested: false,
    config_snapshot: { ...structuredClone(config), asr: { ...config.asr, model: 'actual-asr' } },
    stages: Object.fromEntries(names.map((stage) => [stage, { status: 'completed', progress: 1, started_at: '2026-01-01T01:00:00Z', finished_at: '2026-01-01T01:01:00Z' }])) as Job['stages'],
  }
}
function stageResponse(stage: StageName, job: Job): StageDetail {
  return { stage, job_id: job.id, audio_id: job.audio_id!, state: job.stages[stage], config: job.config_snapshot?.[stage] as unknown as Record<string, unknown>, result_error: null,
    result: job.stages[stage].status !== 'completed' ? null : stage === 'preprocess' ? { kind: 'preprocess', data: { sample_rate: 16000, channels: 1, duration: 90 } }
      : stage === 'export' ? { kind: 'outputs', items: [{ name: 'voice.srt', size_bytes: 20, download_url: '/p1/a1/voice.srt' }] }
      : { kind: 'transcript', transcript: { project_id: 'p1', stage: stage === 'asr' ? 'raw' : stage === 'alignment' ? 'aligned' : 'translated', language: 'ja', duration: 90, segments: [{ id: 'seg-1', start: stage === 'alignment' ? 2 : 1, end: 3, source_text: `${stage}-source`, translated_text: stage === 'translation' ? 'translated-only' : null }] } },
  }
}
const response = (payload: unknown, status = 200) => ({ ok: status === 200, status, json: async () => payload }) as Response
class FakeSocket {
  static instances: FakeSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  constructor() { FakeSocket.instances.push(this) }
  close() {}
}
let app: VueApp | undefined
let root: HTMLDivElement | undefined
let store: ReturnType<typeof useProjectDetailStore>
async function settle() { for (let i = 0; i < 16; i++) await Promise.resolve(); await nextTick() }
function setup(override?: (path: string, init?: RequestInit) => Promise<Response> | undefined) {
  const jobs = { a1: makeJob(), a2: makeJob('j2', 'a2') }
  const pinia = createPinia()
  setActivePinia(pinia)
  store = useProjectDetailStore()
  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const custom = override?.(path, init)
    if (custom) return custom
    const audioId = path.includes('/a2/') ? 'a2' : 'a1'
    const job = jobs[audioId]
    if (path.endsWith('/upload-policy')) return Promise.resolve(response({ allowed_extensions: ['wav'], max_upload_bytes: 50000 }))
    if (path.endsWith('/projects')) return Promise.resolve(response({ items: [project] }))
    if (path.endsWith('/audios')) return Promise.resolve(response({ items: Object.entries(jobs).map(([id, job]) => ({ id, source: { ...source, original_name: `${id}.wav` }, job })) }))
    if (path.endsWith('/job')) return Promise.resolve(response(job))
    if (path.endsWith('/config')) return Promise.resolve(response(init?.method === 'PUT' ? JSON.parse(String(init.body)) : config))
    if (path.includes('/stages/')) return Promise.resolve(response(stageResponse(path.split('/').pop() as StageName, job)))
    if (path.endsWith('/transcript')) return Promise.resolve(response({ transcript: { stage: 'translated', language: 'zh-CN', segments: [{ id: 'latest', source_text: 'latest-source', translated_text: 'latest-translation' }] } }))
    if (path.endsWith('/outputs')) return Promise.resolve(response({ items: [] }))
    if (path.endsWith('/run')) { jobs[audioId] = makeJob('rerun', audioId); return Promise.resolve(response({ job_id: 'rerun', status: 'completed' })) }
    return Promise.resolve(response(project))
  })
  vi.stubGlobal('fetch', fetch)
  vi.stubGlobal('WebSocket', FakeSocket)
  return { pinia, jobs, fetch }
}
async function mountWorkspace(override?: Parameters<typeof setup>[0]) {
  const fixture = setup(override)
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(App).use(fixture.pinia)
  app.mount(root)
  await settle()
  root.querySelector<HTMLElement>('.project-card')!.click()
  await settle()
  return fixture
}
async function chooseStage(index: number) {
  root!.querySelectorAll<HTMLButtonElement>('.pipeline-node')[index].click()
  await settle()
}
afterEach(() => { app?.unmount(); app = undefined; store?.stop(); root?.remove(); root = undefined; FakeSocket.instances.length = 0; vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('stage workspace navigation and configuration', () => {
  it('keeps the locked stage label typography while scoping larger type to the plus icon', async () => {
    await mountWorkspace()
    store.job!.status = 'pending'
    await nextTick()
    const style = document.createElement('style')
    style.textContent = stylesheet
    document.head.append(style)
    try {
      const button = root!.querySelector<HTMLButtonElement>('.stage-config-button')!
      const suffix = button.querySelector('span')!
      const icon = root!.querySelector<HTMLElement>('button[aria-label="新建项目"] > span')!
      expect(button.textContent).toBe('配置此阶段 · 只读')
      expect(button.disabled).toBe(false)

      // jsdom does not reliably resolve inherited font properties. Check the real CSS rules
      // to ensure neither the text suffix nor loader overrides button typography.
      const rules = Array.from(style.sheet!.cssRules).filter(
        (rule): rule is CSSStyleRule => rule.type === CSSRule.STYLE_RULE,
      )
      const buttonRule = rules.find((rule) => rule.selectorText === '.primary-button')!
      expect(button.matches(buttonRule.selectorText)).toBe(true)
      expect(buttonRule.style.getPropertyValue('font-size')).toBe('13px')
      expect(buttonRule.style.getPropertyValue('font-weight')).toBe('700')
      const iconRule = rules.find((rule) => rule.style.getPropertyValue('font-size') === '19px' && icon.matches(rule.selectorText))!
      expect(icon.getAttribute('aria-hidden')).toBe('true')
      expect(iconRule.style.getPropertyValue('font-weight')).toBe('400')
      expect(iconRule.style.getPropertyValue('line-height')).toBe('1')
      expect(suffix.matches(iconRule.selectorText)).toBe(false)

      const hasNoTypographyOverride = (element: Element) => {
        const matching = rules.filter((rule) => element.matches(rule.selectorText))
        for (const property of ['font', 'font-family', 'font-size', 'font-weight', 'line-height']) {
          expect(matching.every((rule) => !rule.style.getPropertyValue(property))).toBe(true)
        }
      }
      hasNoTypographyOverride(suffix)
      store.job!.status = 'completed'
      store.actionLoading = true
      await nextTick()
      const loader = root!.querySelector<HTMLElement>('.workspace-actions .button-loader')!
      hasNoTypographyOverride(loader)
      expect(loader.matches(iconRule.selectorText)).toBe(false)
      const loaderRule = rules.find((rule) => rule.selectorText === '.button-loader')!
      expect(loader.matches(loaderRule.selectorText)).toBe(true)
      expect(loaderRule.style.getPropertyValue('width')).toBe('14px')
      expect(loaderRule.style.getPropertyValue('height')).toBe('14px')
    } finally {
      style.remove()
    }
  })

  it('has one navigation and one workspace; every stage selects its own real content without opening a modal', async () => {
    await mountWorkspace()
    expect(root!.querySelector('.config-overview')).toBeNull()
    expect(root!.textContent).not.toContain('CONFIGURATION MAP')
    expect(root!.querySelectorAll('.pipeline-node')).toHaveLength(5)
    expect(root!.querySelectorAll('.stage-workspace')).toHaveLength(1)
    for (let index = 0; index < names.length; index++) {
      await chooseStage(index)
      const pane = root!.querySelector('.stage-workspace')!
      expect(pane.querySelector('h3')!.textContent).toBe(labels[index])
      expect(root!.querySelector('[role=dialog]')).toBeNull()
      expect(root!.querySelectorAll('.pipeline-node')[index].getAttribute('aria-label')).toBe(`选择${labels[index]}阶段`)
      expect(root!.querySelectorAll('.pipeline-node')[index].getAttribute('aria-pressed')).toBe('true')
      expect(pane.textContent).not.toContain('latest-translation')
      if (index === 0) { expect(pane.textContent).toContain('预处理结果 · 产物元数据'); expect(pane.textContent).not.toContain('实际音频元数据'); expect(pane.textContent).toContain('sample_rate'); expect(pane.textContent).toContain('16000'); expect(root!.querySelector('.waveform')).toBeNull() }
      else if (index === 4) { expect(pane.querySelector('a')!.getAttribute('href')).toBe('/p1/a1/voice.srt'); expect(root!.querySelector('.outputs-rail')).toBeNull() }
      else { expect(pane.textContent).toContain(`${names[index]}-source`); expect(pane.textContent).not.toContain(index === 3 ? 'asr-source' : 'translated-only') }
    }
    await chooseStage(1)
    expect(root!.querySelector('.stage-config-summary')!.textContent).toContain('actual-asr')
    expect(root!.querySelector('.stage-config-summary')!.textContent).toContain('draft-asr')
  })

  it('opens each stage modal from its independent button, preserving cancellation, unsaved prompts and full workflow saves', async () => {
    const { fetch } = await mountWorkspace()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    for (let index = 0; index < names.length; index++) {
      await chooseStage(index)
      root!.querySelector<HTMLButtonElement>('.stage-config-button')!.click()
      await nextTick()
      expect(root!.querySelector('[role=dialog] h3')!.textContent).toBe(`${labels[index]}配置`)
      root!.querySelector<HTMLButtonElement>('.modal-close')!.click()
      await nextTick()
    }
    await chooseStage(1)
    root!.querySelector<HTMLButtonElement>('.stage-config-button')!.click()
    await nextTick()
    const model = root!.querySelector<HTMLInputElement>('.config-modal input:not([type])')!
    model.value = 'cancelled-edit'; model.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    root!.querySelector<HTMLButtonElement>('.modal-close')!.click(); await nextTick()
    expect(root!.querySelector('[role=dialog]')).not.toBeNull()
    expect(root!.textContent).toContain('有未保存修改')
    root!.querySelector<HTMLButtonElement>('.modal-close')!.click(); await nextTick()
    expect(store.config!.asr.model).toBe('draft-asr')
    expect(store.configDirty).toBe(false)
    root!.querySelector<HTMLButtonElement>('.stage-config-button')!.click(); await nextTick()
    const savedModel = root!.querySelector<HTMLInputElement>('.config-modal input:not([type])')!
    savedModel.value = 'saved-edit'; savedModel.dispatchEvent(new Event('input', { bubbles: true })); await nextTick()
    root!.querySelector('form.config-modal-form')!.dispatchEvent(new Event('submit', { cancelable: true })); await settle()
    expect(root!.querySelector('[role=dialog]')).toBeNull()
    const saved = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PUT')![1]!.body))
    expect(saved.asr.model).toBe('saved-edit')
    expect(saved.translation).toEqual(config.translation)
    expect(saved.export).toEqual(config.export)
    expect(store.job!.config_snapshot!.asr.model).toBe('actual-asr')
  })

  it.each(['running', 'pending'] as const)('lets a %s task inspect configuration with all fields read only', async (status) => {
    const { fetch } = await mountWorkspace()
    store.job!.status = status
    await chooseStage(3)
    root!.querySelector<HTMLButtonElement>('.stage-config-button')!.click(); await nextTick()
    expect(root!.querySelector('[role=dialog]')).not.toBeNull()
    expect(root!.querySelector('.modal-lock-note')!.textContent).toContain('排队')
    expect(Array.from(root!.querySelectorAll<HTMLInputElement>('.config-modal input, .config-modal select, .config-modal textarea')).every((field) => field.disabled)).toBe(true)
    expect(root!.querySelector<HTMLButtonElement>('.config-modal button[type=submit]')!.disabled).toBe(true)
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('explains missing configuration and absent task snapshots without claiming the draft is the actual model', async () => {
    await mountWorkspace((path) => path.endsWith('/config') ? Promise.resolve(response({ error: { message: '配置读取失败' } }, 500)) : undefined)
    store.job!.config_snapshot = null
    await nextTick()
    expect(root!.querySelector<HTMLButtonElement>('.stage-config-button')!.disabled).toBe(true)
    expect(root!.querySelector('#stage-config-note')!.textContent).toContain('尚未加载')
    expect(root!.querySelector('.stage-config-summary')!.textContent).toContain('没有任务配置快照')
  })

  it('retries a failed configuration read without creating a mock fallback, and shows another failure in the existing alert', async () => {
    let fail = true
    await mountWorkspace((path) => path.endsWith('/config') && fail ? Promise.resolve(response({ error: { message: '配置读取失败' } }, 500)) : undefined)
    expect(store.config).toBeNull()
    const retry = root!.querySelector<HTMLButtonElement>('.stage-config-retry')!
    expect(retry.textContent).toBe('重新加载配置')
    retry.click(); await settle()
    expect(store.config).toBeNull()
    expect(root!.querySelector('.alert')!.textContent).toContain('配置读取失败')
    fail = false
    root!.querySelector<HTMLButtonElement>('.stage-config-retry')!.click(); await settle()
    expect(store.config!.asr.model).toBe('draft-asr')
    expect(store.config!.preprocess.provider).toBe('ffmpeg')
    expect(root!.querySelector('.stage-config-retry')).toBeNull()
    expect(root!.querySelector<HTMLButtonElement>('.stage-config-button')!.disabled).toBe(false)
    expect(root!.querySelector('.alert')).toBeNull()
  })

  it('reestablishes the audio websocket after a failed initial config read, preserving stage and checked audio', async () => {
    let fail = true
    await mountWorkspace((path) => path.endsWith('/config') && fail ? Promise.resolve(response({ error: { message: '配置读取失败' } }, 500)) : undefined)
    expect(FakeSocket.instances).toHaveLength(0)
    await chooseStage(3)
    store.selectedAudioIds = ['a2']
    fail = false
    root!.querySelector<HTMLButtonElement>('.stage-config-retry')!.click(); await settle()
    expect(FakeSocket.instances).toHaveLength(1)
    expect(store.activeAudioId).toBe('a1')
    expect(store.selectedStage).toBe('translation')
    expect(store.selectedAudioIds).toEqual(['a2'])
    FakeSocket.instances[0].onmessage?.({ data: JSON.stringify({ type: 'stage.started', job_id: 'j1', audio_id: 'a1', data: { stage: 'translation' } }) } as MessageEvent)
    await settle()
    expect(store.job!.stages.translation.status).toBe('running')
    expect(root!.querySelector('.stage-workspace .stage-badge')!.textContent).toBe('处理中')
  })

  it('shows loading, empty and actionable artifact errors with a retry path', async () => {
    let finish: ((value: Response) => void) | undefined
    let hold = false
    await mountWorkspace((path) => hold && path.endsWith('/stages/asr') ? new Promise((resolve) => { finish = resolve }) : undefined)
    hold = true
    root!.querySelectorAll<HTMLButtonElement>('.pipeline-node')[1].click(); await nextTick()
    expect(root!.querySelector('.stage-result-loading')).not.toBeNull()
    expect(root!.querySelector('.stage-transcript-result')).toBeNull()
    finish!(response({ ...stageResponse('asr', store.job!), result: null, result_error: { code: 'MISSING', message: '识别产物缺失' } })); await settle()
    expect(root!.querySelector('.stage-result-error')!.textContent).toContain('恢复文件后刷新')
    hold = false
    root!.querySelector<HTMLButtonElement>('.stage-result-error button')!.click(); await settle()
    expect(root!.querySelector('.stage-transcript-result')!.textContent).toContain('asr-source')
    store.job!.stages.asr.status = 'skipped'; await settle()
    expect(root!.querySelector('.stage-empty')!.textContent).toContain('已跳过')
  })
})

describe('stage request isolation and event refresh', () => {
  it('ignores delayed responses from a previous stage and previous audio', async () => {
    const delayed: { resolve: (response: Response) => void; payload: StageDetail }[] = []
    let hold = false
    setup((path) => hold && path.includes('/a1/stages/asr') ? new Promise((resolve) => { delayed.push({ resolve, payload: stageResponse('asr', makeJob()) }) }) : undefined)
    await store.load('p1')
    hold = true
    store.selectedStage = 'asr'; await nextTick()
    store.selectedStage = 'translation'; await settle()
    delayed[0].resolve(response(delayed[0].payload)); await settle()
    expect(store.stageDetail!.stage).toBe('translation')
    store.selectedStage = 'asr'; await nextTick()
    await store.selectAudio('a2'); await settle()
    delayed[1].resolve(response(delayed[1].payload)); await settle()
    expect(store.stageDetail!.audio_id).toBe('a2')
    expect(store.stageDetail!.job_id).toBe('j2')
    expect(store.stageDetailLoading).toBe(false)
  })

  it('rejects old run details and old job REST/snapshot/event updates after reprocessing', async () => {
    let releaseStage: ((response: Response) => void) | undefined
    let releaseJob: ((response: Response) => void) | undefined
    let holdStage = false
    let holdJob = false
    setup((path) => {
      if (holdStage && path.endsWith('/stages/asr')) { holdStage = false; return new Promise((resolve) => { releaseStage = resolve }) }
      if (holdJob && path.endsWith('/job')) { holdJob = false; return new Promise((resolve) => { releaseJob = resolve }) }
    })
    await store.load('p1')
    holdStage = true; store.selectedStage = 'asr'; await nextTick()
    holdJob = true; const oldFetch = store.fetchJob()
    await store.run(false); await settle()
    expect(store.stageDetail!.job_id).toBe('rerun')
    releaseStage!(response(stageResponse('asr', makeJob())))
    releaseJob!(response(makeJob())); await oldFetch; await settle()
    const socket = FakeSocket.instances[0]
    socket.onmessage?.({ data: JSON.stringify({ type: 'job.snapshot', job_id: 'j1', audio_id: 'a1', data: makeJob() }) } as MessageEvent)
    socket.onmessage?.({ data: JSON.stringify({ type: 'job.failed', job_id: 'j1', audio_id: 'a1', data: { stage: 'asr', message: 'stale failure' } }) } as MessageEvent)
    socket.onmessage?.({ data: JSON.stringify({ type: 'stage.started', job_id: 'rerun', audio_id: 'a2', data: { stage: 'asr' } }) } as MessageEvent)
    socket.onmessage?.({ data: JSON.stringify({ type: 'job.snapshot', job_id: 'rerun', audio_id: 'a1', data: makeJob('different') }) } as MessageEvent)
    await settle()
    expect(store.job!.id).toBe('rerun')
    expect(store.job!.status).toBe('completed')
    expect(store.stageDetail!.job_id).toBe('rerun')
  })

  it('refreshes only the selected stage on completion and on REST job state updates', async () => {
    const { jobs, fetch } = setup()
    jobs.a1.status = 'running'; jobs.a1.stages.asr.status = 'running'
    await store.load('p1')
    store.selectedStage = 'asr'; await settle()
    expect(store.stageDetail!.result).toBeNull()
    fetch.mockClear()
    jobs.a1.stages.asr.status = 'completed'
    FakeSocket.instances[0].onmessage?.({ data: JSON.stringify({ type: 'stage.completed', job_id: 'j1', audio_id: 'a1', data: { stage: 'asr' } }) } as MessageEvent)
    await settle()
    expect(store.stageDetail!.result?.kind).toBe('transcript')
    expect(fetch.mock.calls.filter(([path]) => String(path).includes('/stages/')).map(([path]) => path)).toEqual(['/api/v1/projects/p1/audios/a1/stages/asr'])
    jobs.a1.stages.asr.status = 'pending'; await store.fetchJob(); await settle()
    expect(store.stageDetail!.result).toBeNull()
  })
})
