import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Project } from './projects'

export type JobStatus = 'pending' | 'running' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
export type StageStatus = 'pending' | 'running' | 'completed' | 'failed'
export type StageName = 'preprocess' | 'asr' | 'alignment' | 'translation' | 'export'

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
  status: JobStatus
  current_stage?: StageName | null
  progress: number
  created_at: string
  updated_at: string
  stages: Record<StageName, Stage>
  cancel_requested: boolean
  error?: JobError | null
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

interface TranscriptResponse {
  stage: string
  transcript: Transcript
}

interface EventMessage {
  type?: string
  data?: Record<string, unknown>
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
  const job = ref<Job | null>(null)
  const transcript = ref<Transcript | null>(null)
  const outputs = ref<Output[]>([])
  const loading = ref(false)
  const actionLoading = ref(false)
  const error = ref('')
  const projectId = ref<string | null>(null)
  const socket = ref<WebSocket | null>(null)
  const reconnectTimer = ref<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttempt = ref(0)
  let connectionGeneration = 0
  let stopped = true

  const progressPercent = computed(() => Math.round((job.value?.progress || 0) * 100))
  const isRunning = computed(() => job.value?.status === 'running' || job.value?.status === 'cancelling')

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${path}`, init)
    if (!response.ok) throw await parseError(response)
    return (await response.json()) as T
  }

  async function fetchProject(id = projectId.value || '') {
    if (!id) return null
    const value = await request<Project>(`/projects/${encodeURIComponent(id)}`)
    project.value = value
    return value
  }

  async function fetchJob(id = projectId.value || '') {
    if (!id) return null
    const value = normalizeJob(await request<Job>(`/projects/${encodeURIComponent(id)}/job`))
    job.value = value
    return value
  }

  async function fetchTranscript(id = projectId.value || '') {
    if (!id) return null
    try {
      const value = await request<TranscriptResponse>(`/projects/${encodeURIComponent(id)}/transcript`)
      transcript.value = value.transcript
      return value.transcript
    } catch (reason) {
      // A transcript is expected to be absent before ASR starts.
      if (reason instanceof Error && reason.message.includes('字幕不存在')) {
        transcript.value = null
        return null
      }
      throw reason
    }
  }

  async function fetchOutputs(id = projectId.value || '') {
    if (!id) return []
    const value = await request<{ items: Output[] }>(`/projects/${encodeURIComponent(id)}/outputs`)
    outputs.value = value.items
    return value.items
  }

  async function fetchAll(id = projectId.value || '') {
    if (!id) return
    await Promise.all([fetchProject(id), fetchJob(id), fetchTranscript(id), fetchOutputs(id)])
  }

  function websocketUrl(id: string) {
    const base = new URL(API_BASE_URL, typeof window === 'undefined' ? 'http://localhost' : window.location.origin)
    base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:'
    base.pathname = `${base.pathname.replace(/\/+$/, '')}/projects/${encodeURIComponent(id)}/events`
    return base.toString()
  }

  function applyEvent(message: EventMessage) {
    const type = message.type
    const data = message.data || {}
    if (type === 'job.snapshot' && data && typeof data === 'object') {
      job.value = normalizeJob(data as unknown as Job)
      return
    }
    if (!job.value) return
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
  }

  function scheduleReconnect(id: string, generation: number) {
    if (stopped || generation !== connectionGeneration || reconnectTimer.value) return
    const delay = Math.min(10_000, 500 * 2 ** reconnectAttempt.value)
    reconnectAttempt.value += 1
    reconnectTimer.value = setTimeout(() => {
      reconnectTimer.value = null
      void fetchJob(id).catch(() => undefined)
      connect(id, generation)
    }, delay)
  }

  function connect(id = projectId.value || '', generation = connectionGeneration) {
    if (!id || stopped || generation !== connectionGeneration) return
    socket.value?.close()
    let ws: WebSocket
    try {
      ws = new WebSocket(websocketUrl(id))
    } catch {
      scheduleReconnect(id, generation)
      return
    }
    socket.value = ws
    ws.onopen = () => {
      reconnectAttempt.value = 0
      void fetchJob(id).catch(() => undefined)
    }
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as EventMessage
        applyEvent(message)
        const stage = message.data?.stage
        if (message.type === 'job.completed' || (message.type === 'stage.completed' && ['asr', 'alignment', 'translation'].includes(String(stage)))) {
          void fetchTranscript(id).catch(() => undefined)
        }
        if (message.type === 'job.completed' || (message.type === 'stage.completed' && stage === 'export')) {
          void fetchOutputs(id).catch(() => undefined)
        }
      } catch {
        // Ignore malformed event payloads and keep REST as the source of truth.
      }
    }
    ws.onerror = () => ws.close()
    ws.onclose = () => {
      if (socket.value === ws) socket.value = null
      if (!stopped && generation === connectionGeneration) {
        void fetchJob(id).catch(() => undefined)
        scheduleReconnect(id, generation)
      }
    }
  }

  function stop() {
    stopped = true
    connectionGeneration += 1
    if (reconnectTimer.value) clearTimeout(reconnectTimer.value)
    reconnectTimer.value = null
    socket.value?.close()
    socket.value = null
  }

  async function load(id: string) {
    stop()
    projectId.value = id
    stopped = false
    loading.value = true
    error.value = ''
    try {
      await fetchAll(id)
      connectionGeneration += 1
      connect(id, connectionGeneration)
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '项目详情加载失败'
      throw reason
    } finally {
      loading.value = false
    }
  }

  async function run(resume = true) {
    if (!projectId.value) return
    actionLoading.value = true
    error.value = ''
    try {
      await request(`/projects/${encodeURIComponent(projectId.value)}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume }),
      })
      await fetchJob()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '任务启动失败'
      throw reason
    } finally {
      actionLoading.value = false
    }
  }

  async function cancel() {
    if (!projectId.value) return
    actionLoading.value = true
    error.value = ''
    try {
      await request(`/projects/${encodeURIComponent(projectId.value)}/cancel`, { method: 'POST' })
      await fetchJob()
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '取消任务失败'
      throw reason
    } finally {
      actionLoading.value = false
    }
  }

  return {
    project,
    job,
    transcript,
    outputs,
    loading,
    actionLoading,
    error,
    progressPercent,
    isRunning,
    load,
    stop,
    fetchProject,
    fetchJob,
    fetchTranscript,
    fetchOutputs,
    run,
    cancel,
  }
})
