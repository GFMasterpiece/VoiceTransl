import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import type { Audio, Project } from './projects'

export type JobStatus = 'pending' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed' | 'interrupted'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export type StageName = 'preprocess' | 'asr' | 'alignment' | 'translation' | 'export'

export interface PipelineConfig {
  schema_version?: string
  source_language: string
  target_language: string
  preprocess: {
    provider: string
    sample_rate: number
    channels: number
    preserve_original: boolean
  }
  asr: {
    provider: string
    backend: string
    model: string
    language: string
    vad_enabled: boolean
    chunk_seconds: number
  }
  alignment: {
    enabled: boolean
    provider: string
    model: string
    fallback: string
  }
  translation: {
    provider: string
    base_url: string | null
    api_key_env: string | null
    model: string
    target_language: string
    batch_size: number
    temperature: number
    glossary: Record<string, string>
    instructions: string | null
    timeout_seconds: number
  }
  export: {
    formats: string[]
    bilingual: boolean
    naming: string
  }
}

export interface Stage {
  status: StageStatus
  progress: number
  started_at?: string | null
  finished_at?: string | null
  artifact?: string | null
  error?: string | null
}

export interface JobError {
  type: string
  message: string
  stage?: StageName | null
  retryable?: boolean
}

export interface Job {
  schema_version?: string
  id: string
  project_id: string
  audio_id?: string
  status: JobStatus
  current_stage?: StageName | null
  progress: number
  created_at: string
  updated_at: string
  stages: Record<StageName, Stage>
  cancel_requested: boolean
  error?: JobError | null
  config_snapshot?: PipelineConfig | null
}

export interface TranscriptSegment {
  id: string
  start?: number | null
  end?: number | null
  source_text: string
  normalized_text?: string | null
  translated_text?: string | null
  language?: string
  channel?: string | null
  speaker?: string | null
  confidence?: number | null
  metadata?: Record<string, unknown>
}

export interface Transcript {
  schema_version?: string
  project_id: string
  stage: string
  language: string
  duration?: number | null
  created_at?: string
  segments: TranscriptSegment[]
  metadata?: Record<string, unknown>
}

export interface Output {
  name: string
  size_bytes: number
  download_url: string
}

export interface StageDetail {
  stage: StageName
  job_id: string
  audio_id: string
  state: Stage
  config: Record<string, unknown> | null
  result: { kind: 'preprocess'; data: Record<string, unknown> }
    | { kind: 'transcript'; transcript: Transcript }
    | { kind: 'outputs'; items: Output[] }
    | null
  result_error: { code: string; message: string } | null
}

export interface AudioItem extends Audio { job: Job }
export interface AudioActionResult { audio_id: string; job_id?: string; status?: string; error?: { code?: string; message: string } }

interface TranscriptResponse {
  stage: string
  transcript: Transcript
}

interface EventMessage {
  type?: string
  job_id?: string
  audio_id?: string
  data?: Record<string, unknown>
}

function defaultConfig(sourceLanguage = 'ja'): PipelineConfig {
  const targetLanguage = sourceLanguage.toLowerCase() === 'zh' ? 'en' : 'zh-CN'
  return {
    schema_version: '1.0',
    source_language: sourceLanguage,
    target_language: targetLanguage,
    preprocess: { provider: 'mock', sample_rate: 16000, channels: 1, preserve_original: true },
    asr: { provider: 'mock', backend: 'qwen3-1.7b', model: 'mock', language: sourceLanguage, vad_enabled: true, chunk_seconds: 30 },
    alignment: { enabled: true, provider: 'mock', model: 'mock', fallback: 'keep_asr_timestamps' },
    translation: {
      provider: 'mock',
      base_url: null,
      api_key_env: null,
      model: 'mock',
      target_language: targetLanguage,
      batch_size: 8,
      temperature: 0.2,
      glossary: {},
      instructions: null,
      timeout_seconds: 120,
    },
    export: { formats: ['srt', 'lrc'], bilingual: true, naming: 'language' },
  }
}

function normalizeConfig(value: Partial<PipelineConfig> | null | undefined, sourceLanguage = 'ja'): PipelineConfig {
  const base = defaultConfig(sourceLanguage)
  const input = value || {}
  return {
    ...base,
    ...input,
    preprocess: { ...base.preprocess, ...(input.preprocess || {}) },
    asr: { ...base.asr, ...(input.asr || {}) },
    alignment: { ...base.alignment, ...(input.alignment || {}) },
    translation: { ...base.translation, ...(input.translation || {}) },
    export: { ...base.export, ...(input.export || {}) },
  }
}

function validateConfig(value: PipelineConfig | null): string[] {
  if (!value) return ['尚未加载 Pipeline 配置']
  const errors: string[] = []
  if (!value.source_language.trim()) errors.push('请设置源语言')
  if (!value.target_language.trim()) errors.push('请设置目标语言')
  if (value.source_language.toLowerCase() === value.target_language.toLowerCase()) errors.push('源语言和目标语言不能相同')
  if (value.asr.language !== value.source_language) errors.push('识别语言必须与源语言一致')
  if (value.translation.target_language !== value.target_language) errors.push('翻译目标语言必须与目标语言一致')
  if (!['mock', 'ffmpeg'].includes(value.preprocess.provider)) errors.push('预处理 Provider 只支持 mock 或 ffmpeg')
  if (value.asr.provider === 'crispasr' && value.preprocess.provider !== 'ffmpeg') errors.push('CrispASR 需要启用 FFmpeg 预处理以生成标准 WAV')
  if (!value.asr.provider.trim() || !value.asr.model.trim()) errors.push('请设置语音识别 Provider 和模型')
  if (!value.asr.backend.trim()) errors.push('请设置语音识别后端')
  if (value.asr.chunk_seconds < 1 || value.asr.chunk_seconds > 600) errors.push('分段时长必须在 1–600 秒之间')
  if (!value.translation.provider.trim() || !value.translation.model.trim()) errors.push('请设置翻译 Provider 和模型')
  const translationProviders = ['mock', 'openai_chat', 'openai_responses', 'anthropic_messages']
  if (!translationProviders.includes(value.translation.provider)) errors.push('翻译 Provider 不受支持，请选择兼容协议')
  if (value.translation.provider !== 'mock' && !value.translation.base_url?.trim()) errors.push('真实翻译 Provider 需要设置接口地址')
  if (value.translation.api_key_env && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(value.translation.api_key_env)) errors.push('API Key 环境变量名格式不正确')
  if (value.translation.batch_size < 1 || value.translation.batch_size > 128) errors.push('翻译批量大小必须在 1–128 之间')
  if (value.translation.temperature < 0 || value.translation.temperature > 2) errors.push('翻译温度必须在 0–2 之间')
  if (value.translation.timeout_seconds < 1 || value.translation.timeout_seconds > 3600) errors.push('翻译请求超时必须在 1–3600 秒之间')
  if (!value.export.formats.length) errors.push('至少选择一种导出格式')
  return errors
}

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')

async function parseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as {
      error?: { message?: string }
      detail?: { message?: string; error?: { message?: string } } | string
    }
    const message =
      typeof payload.detail === 'string'
        ? payload.detail
        : payload.detail?.error?.message || payload.detail?.message
    return new Error(payload.error?.message || message || `请求失败（${response.status}）`)
  } catch {
    return new Error(`请求失败（${response.status}）`)
  }
}

function stageRecord(value: unknown): Record<StageName, Stage> {
  const source = (value && typeof value === 'object' ? value : {}) as Record<string, Partial<Stage>>
  const names: StageName[] = ['preprocess', 'asr', 'alignment', 'translation', 'export']
  return Object.fromEntries(
    names.map((name) => [
      name,
      {
        status: source[name]?.status || 'pending',
        progress: Number(source[name]?.progress || 0),
        started_at: source[name]?.started_at || null,
        finished_at: source[name]?.finished_at || null,
        artifact: source[name]?.artifact || null,
        error: source[name]?.error || null,
      },
    ]),
  ) as Record<StageName, Stage>
}

function normalizeJob(value: Job): Job {
  return { ...value, stages: stageRecord(value.stages) }
}

export const useProjectDetailStore = defineStore('projectDetail', () => {
  const project = ref<Project | null>(null)
  const audios = ref<AudioItem[]>([])
  const activeAudioId = ref<string | null>(null)
  const selectedAudioIds = ref<string[]>([])
  const batchResults = ref<AudioActionResult[]>([])
  const failedConfigAudioIds = ref<string[]>([])
  const job = ref<Job | null>(null)
  const transcript = ref<Transcript | null>(null)
  const outputs = ref<Output[]>([])
  const selectedStage = ref<StageName>('preprocess')
  const stageDetail = ref<StageDetail | null>(null)
  const stageDetailLoading = ref(false)
  const stageDetailError = ref('')
  let stageDetailGeneration = 0
  let jobRequestGeneration = 0
  const retiredJobIds = new Set<string>()
  const config = ref<PipelineConfig | null>(null)
  const configLoading = ref(false)
  const configSaving = ref(false)
  const configDirty = ref(false)
  const configErrors = ref<string[]>([])
  const loading = ref(false)
  const actionLoading = ref(false)
  const error = ref('')
  const projectId = ref<string | null>(null)
  const socket = ref<WebSocket | null>(null)
  const reconnectTimer = ref<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttempt = ref(0)
  let connectionGeneration = 0
  let stopped = true
  let statusTimer: ReturnType<typeof setInterval> | null = null
  const activeAudio = computed(() => audios.value.find((audio) => audio.id === activeAudioId.value) || null)
  const isBusyJob = (value: Job | null) => !!value && (['running', 'cancelling'].includes(value.status) || (value.status === 'pending' && !!value.config_snapshot))
  const anyRunning = computed(() => audios.value.some((audio) => isBusyJob(audio.job)))
  const selectedConfigFailed = computed(() => selectedAudioIds.value.some((id) => failedConfigAudioIds.value.includes(id)))
  function audioPath(id = projectId.value || '', audioId = activeAudioId.value || 'aud_primary') {
    return `/projects/${encodeURIComponent(id)}/audios/${encodeURIComponent(audioId)}`
  }
  function isCurrent(id: string, audioId: string, generation: number) {
    return id === projectId.value && audioId === activeAudioId.value && generation === connectionGeneration && !stopped
  }

  const progressPercent = computed(() => Math.round((job.value?.progress || 0) * 100))
  const isRunning = computed(() => isBusyJob(job.value))
  const configLocked = computed(() => isBusyJob(job.value))
  const configValid = computed(() => configErrors.value.length === 0 && !!config.value)

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${path}`, init)
    if (!response.ok) throw await parseError(response)
    return (await response.json()) as T
  }

  async function fetchProject(id = projectId.value || '') {
    if (!id) return null
    const value = await request<Project>(`/projects/${encodeURIComponent(id)}`)
    if (id === projectId.value && !stopped) project.value = value
    return value
  }

  async function fetchAudios(id = projectId.value || '') {
    const value = await request<{ items: AudioItem[] }>(`/projects/${encodeURIComponent(id)}/audios`)
    if (id === projectId.value && !stopped) {
      audios.value = value.items.map((audio) => ({ ...audio, job: normalizeJob(audio.job) }))
      selectedAudioIds.value = selectedAudioIds.value.filter((audioId) => audios.value.some((audio) => audio.id === audioId))
    }
    return value.items
  }

  async function fetchJob(id = projectId.value || '', audioId = activeAudioId.value || 'aud_primary') {
    if (!id) return null
    const generation = connectionGeneration
    const requestGeneration = ++jobRequestGeneration
    const value = normalizeJob(await request<Job>(`${audioPath(id, audioId)}/job`))
    if (isCurrent(id, audioId, generation) && requestGeneration === jobRequestGeneration) {
      if (job.value && job.value.id !== value.id) retiredJobIds.add(job.value.id)
      job.value = value
      const audio = audios.value.find((entry) => entry.id === audioId)
      if (audio) audio.job = value
    }
    return value
  }

  async function fetchStageDetail() {
    const id = projectId.value
    const audioId = activeAudioId.value
    const jobId = job.value?.id
    const stage = selectedStage.value
    const generation = connectionGeneration
    const requestGeneration = ++stageDetailGeneration
    stageDetail.value = null
    stageDetailError.value = ''
    stageDetailLoading.value = false
    if (!id || !audioId || !jobId || stopped) return
    const current = () => isCurrent(id, audioId, generation) && job.value?.id === jobId
      && selectedStage.value === stage && requestGeneration === stageDetailGeneration
    stageDetailLoading.value = true
    try {
      const value = await request<StageDetail>(`${audioPath(id, audioId)}/stages/${stage}`)
      if (!current()) return
      if (value.job_id !== jobId || value.audio_id !== audioId || value.stage !== stage) {
        stageDetailError.value = '阶段详情与当前任务不一致，请刷新阶段详情。'
        return
      }
      stageDetail.value = value
    } catch (reason) {
      if (current()) stageDetailError.value = `${reason instanceof Error ? reason.message : '阶段详情加载失败'}。请检查服务后重试。`
    } finally {
      if (current()) stageDetailLoading.value = false
    }
  }

  // Only the selected stage is fetched. Job replacement and completed-stage
  // notifications invalidate pending responses before loading its own artifact.
  watch(() => [job.value?.id, selectedStage.value, job.value?.stages[selectedStage.value]?.status], () => {
    void fetchStageDetail()
  }, { flush: 'sync' })

  async function fetchTranscript(id = projectId.value || '', audioId = activeAudioId.value || 'aud_primary') {
    if (!id) return null
    const generation = connectionGeneration
    try {
      const value = await request<TranscriptResponse>(`${audioPath(id, audioId)}/transcript`)
      if (isCurrent(id, audioId, generation)) transcript.value = value.transcript
      return value.transcript
    } catch (reason) {
      // A transcript is expected to be absent before ASR starts.
      if (reason instanceof Error && reason.message.includes('字幕不存在')) {
        if (isCurrent(id, audioId, generation)) transcript.value = null
        return null
      }
      throw reason
    }
  }

  async function fetchOutputs(id = projectId.value || '', audioId = activeAudioId.value || 'aud_primary') {
    if (!id) return []
    const generation = connectionGeneration
    const value = await request<{ items: Output[] }>(`${audioPath(id, audioId)}/outputs`)
    if (isCurrent(id, audioId, generation)) outputs.value = value.items
    return value.items
  }

  async function fetchConfig(id = projectId.value || '', audioId = activeAudioId.value || 'aud_primary') {
    if (!id) return null
    configLoading.value = true
    const generation = connectionGeneration
    try {
      const value = await request<Partial<PipelineConfig>>(`${audioPath(id, audioId)}/config`)
      if (!isCurrent(id, audioId, generation)) return null
      config.value = normalizeConfig(value, activeAudio.value?.source.language || value.source_language || 'ja')
      configDirty.value = false
      configErrors.value = validateConfig(config.value)
      return config.value
    } catch (reason) {
      if (!isCurrent(id, audioId, generation)) return null
      config.value = null
      configErrors.value = [reason instanceof Error ? reason.message : 'Pipeline 配置加载失败']
      throw reason
    } finally {
      if (isCurrent(id, audioId, generation)) configLoading.value = false
    }
  }

  function websocketUrl(id: string, audioId: string) {
    const base = new URL(API_BASE_URL, typeof window === 'undefined' ? 'http://localhost' : window.location.origin)
    base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
    base.pathname = `${base.pathname.replace(/\/+$/, '')}${audioPath(id, audioId)}/events`
    return base.toString()
  }

  function applyEvent(message: EventMessage) {
    const type = message.type
    const data = message.data || {}
    if (message.audio_id && message.audio_id !== activeAudioId.value) return false
    if (data.audio_id && data.audio_id !== activeAudioId.value) return false
    if (type === 'job.snapshot' && data && typeof data === 'object') {
      if (message.job_id && message.job_id !== data.id) return false
      if (retiredJobIds.has(String(data.id))) return false
      jobRequestGeneration += 1
      if (job.value && job.value.id !== data.id) retiredJobIds.add(job.value.id)
      job.value = normalizeJob(data as unknown as Job)
      return true
    }
    if (!job.value) return false
    if (message.job_id && message.job_id !== job.value.id) return false
    if (data.job_id && data.job_id !== job.value.id) return false
    const stage = data.stage as StageName | undefined
    if (stage && job.value.stages[stage]) {
      if (type === 'stage.started') {
        job.value.current_stage = stage
        job.value.stages[stage].status = 'running'
        job.value.stages[stage].progress = 0
        job.value.status = 'running'
      } else if (type === 'stage.progress') {
        job.value.stages[stage].progress = Number(data.progress || 0)
      } else if (type === 'stage.completed') {
        job.value.stages[stage].status = 'completed'
        job.value.stages[stage].progress = 1
      }
      const stages = Object.values(job.value.stages)
      job.value.progress = stages.reduce((total, item) => total + Number(item.progress || 0), 0) / stages.length
    }
    if (type === 'job.started') job.value.status = 'running'
    if (type === 'job.completed') {
      job.value.status = 'completed'
      job.value.progress = 1
      job.value.current_stage = null
    }
    if (type === 'job.cancelled') {
      const cancelledStage = job.value.current_stage
      if (cancelledStage && job.value.stages[cancelledStage]) {
        job.value.stages[cancelledStage].status = 'pending'
        job.value.stages[cancelledStage].progress = 0
      }
      job.value.status = 'cancelled'
      job.value.cancel_requested = true
      job.value.current_stage = null
    }
    if (type === 'job.failed') {
      job.value.status = 'failed'
      job.value.current_stage = stage || job.value.current_stage
      if (stage && job.value.stages[stage]) job.value.stages[stage].status = 'failed'
      job.value.error = {
        type: String(data.code || 'JOB_FAILED'),
        message: String(data.message || '任务失败'),
        stage: stage || null,
        retryable: true,
      }
    }
    return true
  }

  function scheduleReconnect(id: string, generation: number, audioId: string) {
    if (stopped || generation !== connectionGeneration || reconnectTimer.value) return
    const delay = Math.min(10_000, 500 * 2 ** reconnectAttempt.value)
    reconnectAttempt.value += 1
    reconnectTimer.value = setTimeout(() => {
      reconnectTimer.value = null
      void fetchJob(id, audioId).catch(() => undefined)
      connect(id, generation, audioId)
    }, delay)
  }

  function connect(id = projectId.value || '', generation = connectionGeneration, audioId = activeAudioId.value || 'aud_primary') {
    if (!id || stopped || generation !== connectionGeneration) return
    socket.value?.close()
    let ws: WebSocket
    try {
      ws = new WebSocket(websocketUrl(id, audioId))
    } catch {
      scheduleReconnect(id, generation, audioId)
      return
    }
    socket.value = ws
    ws.onopen = () => {
      if (!isCurrent(id, audioId, generation)) return
      reconnectAttempt.value = 0
      void fetchJob(id, audioId).catch(() => undefined)
    }
    ws.onmessage = (event) => {
      if (!isCurrent(id, audioId, generation)) return
      try {
        const message = JSON.parse(event.data) as EventMessage
        if (!applyEvent(message)) return
        const audio = audios.value.find((entry) => entry.id === audioId)
        if (audio && job.value) audio.job = job.value
        const stage = message.data?.stage
        if (message.type === 'job.completed' || (message.type === 'stage.completed' && ['asr', 'alignment', 'translation'].includes(String(stage)))) {
          void fetchTranscript(id, audioId).catch(() => undefined)
        }
        if (message.type === 'job.completed' || (message.type === 'stage.completed' && stage === 'export')) {
          void fetchOutputs(id, audioId).catch(() => undefined)
        }
      } catch {
        // Ignore malformed event payloads and keep REST as the source of truth.
      }
    }
    ws.onerror = () => ws.close()
    ws.onclose = () => {
      if (socket.value === ws) socket.value = null
      if (!stopped && generation === connectionGeneration) {
        void fetchJob(id, audioId).catch(() => undefined)
        scheduleReconnect(id, generation, audioId)
      }
    }
  }

  function stop() {
    stopped = true
    connectionGeneration += 1
    jobRequestGeneration += 1
    stageDetailGeneration += 1
    stageDetail.value = null
    stageDetailLoading.value = false
    stageDetailError.value = ''
    if (reconnectTimer.value) clearTimeout(reconnectTimer.value)
    reconnectTimer.value = null
    socket.value?.close()
    socket.value = null
    if (statusTimer) clearInterval(statusTimer)
    statusTimer = null
  }

  async function selectAudio(audioId: string) {
    const id = projectId.value
    if (!id || actionLoading.value || configSaving.value || !audios.value.some((audio) => audio.id === audioId)) return
    stop()
    stopped = false
    activeAudioId.value = audioId
    const generation = connectionGeneration
    job.value = null
    transcript.value = null
    outputs.value = []
    config.value = null
    configDirty.value = false
    configErrors.value = []
    loading.value = true
    error.value = ''
    try {
      await Promise.all([fetchJob(id, audioId), fetchConfig(id, audioId), fetchTranscript(id, audioId), fetchOutputs(id, audioId)])
      if (isCurrent(id, audioId, generation)) {
        connect(id, generation, audioId)
        statusTimer = setInterval(() => { void fetchAudios(id).catch(() => undefined) }, 3000)
      }
    } catch (reason) {
      if (isCurrent(id, audioId, generation)) error.value = reason instanceof Error ? reason.message : '音频详情加载失败'
    } finally {
      if (isCurrent(id, audioId, generation)) loading.value = false
    }
  }

  async function load(id: string, audioId?: string) {
    stop()
    projectId.value = id
    project.value = null
    audios.value = []
    activeAudioId.value = null
    selectedAudioIds.value = []
    batchResults.value = []
    failedConfigAudioIds.value = []
    const generation = connectionGeneration
    stopped = false
    loading.value = true
    error.value = ''
    try {
      await fetchProject(id)
      if (generation !== connectionGeneration || id !== projectId.value) return
      await fetchAudios(id)
      if (generation !== connectionGeneration || id !== projectId.value) return
      const chosen = audios.value.find((audio) => audio.id === audioId)?.id || audios.value[0]?.id
      if (chosen) {
        selectedAudioIds.value = [chosen]
        await selectAudio(chosen)
      }
    } catch (reason) {
      if (generation === connectionGeneration && id === projectId.value) {
        error.value = reason instanceof Error ? reason.message : '项目详情加载失败'
        throw reason
      }
    } finally {
      if (generation === connectionGeneration && id === projectId.value) loading.value = false
    }
  }

  async function run(resume = true) {
    if (!projectId.value) return
    actionLoading.value = true
    error.value = ''
    try {
      const id = projectId.value
      const audioId = activeAudioId.value || 'aud_primary'
      await request(`${audioPath(id, audioId)}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume }),
      })
      await fetchJob(id, audioId)
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '任务启动失败'
      throw reason
    } finally {
      actionLoading.value = false
    }
  }

  async function saveConfig() {
    if (!projectId.value || !config.value) return null
    const errors = validateConfig(config.value)
    configErrors.value = errors
    if (errors.length) throw new Error(errors[0])
    if (configLocked.value) throw new Error('当前任务配置只读')
    configSaving.value = true
    error.value = ''
    const id = projectId.value
    const audioId = activeAudioId.value || 'aud_primary'
    const generation = connectionGeneration
    try {
      const value = await request<PipelineConfig>(`${audioPath(id, audioId)}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config.value),
      })
      if (!isCurrent(id, audioId, generation)) return null
      config.value = normalizeConfig(value, config.value!.source_language)
      configDirty.value = false
      configErrors.value = validateConfig(config.value)
      return config.value
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : 'Pipeline 配置保存失败'
      throw reason
    } finally {
      configSaving.value = false
    }
  }

  function validateCurrentConfig() {
    configErrors.value = validateConfig(config.value)
    return configErrors.value
  }

  function markConfigDirty() {
    configDirty.value = true
    configErrors.value = validateConfig(config.value)
  }

  async function cancel() {
    if (!projectId.value) return
    actionLoading.value = true
    error.value = ''
    try {
      const id = projectId.value
      const audioId = activeAudioId.value || 'aud_primary'
      await request(`${audioPath(id, audioId)}/cancel`, { method: 'POST' })
      await fetchJob(id, audioId)
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '取消任务失败'
      throw reason
    } finally {
      actionLoading.value = false
    }
  }

  function selectAll(selected: boolean) {
    selectedAudioIds.value = selected ? audios.value.map((audio) => audio.id) : []
  }

  async function applyConfigToSelected() {
    if (!projectId.value || !config.value || actionLoading.value || configSaving.value || !selectedAudioIds.value.length) return
    const id = projectId.value
    const selected = [...selectedAudioIds.value]
    const draft = JSON.parse(JSON.stringify(config.value)) as PipelineConfig
    actionLoading.value = true
    batchResults.value = []
    try {
      for (const audioId of selected) {
        const audio = audios.value.find((entry) => entry.id === audioId)!
        const value = normalizeConfig(draft, audio.source.language)
        value.source_language = audio.source.language
        value.asr.language = audio.source.language
        try {
          const errors = validateConfig(value)
          if (errors.length) throw new Error(errors[0])
          if (isBusyJob(audio.job)) throw new Error('此音频正在处理或排队，请完成或取消后再应用配置。')
          const saved = await request<PipelineConfig>(`${audioPath(id, audioId)}/config`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
          failedConfigAudioIds.value = failedConfigAudioIds.value.filter((entry) => entry !== audioId)
          batchResults.value.push({ audio_id: audioId, status: '配置已应用' })
          if (projectId.value === id && activeAudioId.value === audioId) {
            config.value = normalizeConfig(saved, audio.source.language)
            configDirty.value = false
            configErrors.value = validateConfig(config.value)
          }
        } catch (reason) {
          if (!failedConfigAudioIds.value.includes(audioId)) failedConfigAudioIds.value.push(audioId)
          batchResults.value.push({ audio_id: audioId, error: { message: reason instanceof Error ? reason.message : '配置应用失败' } })
        }
      }
    } finally { actionLoading.value = false }
  }

  async function runSelected() {
    if (!projectId.value || !selectedAudioIds.value.length || actionLoading.value || configSaving.value || selectedConfigFailed.value) return
    const id = projectId.value
    const audioIds = [...selectedAudioIds.value]
    actionLoading.value = true
    error.value = ''
    try {
      if (activeAudioId.value && audioIds.includes(activeAudioId.value) && configDirty.value) await saveConfig()
      const value = await request<{ items: AudioActionResult[] }>(`/projects/${encodeURIComponent(id)}/audios/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audio_ids: audioIds, resume: false }) })
      if (id !== projectId.value) return
      batchResults.value = value.items
      await Promise.all([fetchAudios(id), fetchJob(id)])
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '所选音频启动失败'
    } finally { actionLoading.value = false }
  }

  return {
    project,
    audios, activeAudioId, activeAudio, selectedAudioIds, batchResults, anyRunning, selectedConfigFailed,
    job,
    transcript,
    outputs,
    selectedStage, stageDetail, stageDetailLoading, stageDetailError, fetchStageDetail,
    config,
    configLoading,
    configSaving,
    configDirty,
    configErrors,
    loading,
    actionLoading,
    error,
    progressPercent,
    isRunning,
    configLocked,
    configValid,
    load,
    selectAudio, selectAll, fetchAudios, applyConfigToSelected, runSelected,
    stop,
    fetchProject,
    fetchJob,
    fetchTranscript,
    fetchOutputs,
    fetchConfig,
    saveConfig,
    validateCurrentConfig,
    markConfigDirty,
    run,
    cancel,
  }
})
