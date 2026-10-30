import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { message, messageRef } from '../i18n'
import type { Audio, Project } from './projects'

export interface AudioJobSummary {
  id?: string
  status?: string
  config_snapshot?: unknown
}

export interface AudioItem extends Audio {
  job?: AudioJobSummary | null
}

function isBusy(job: AudioJobSummary | null | undefined): boolean {
  if (!job) return false
  return job.status === 'running' || job.status === 'cancelling'
    || (job.status === 'pending' && !!job.config_snapshot)
}

export const useProjectDetailStore = defineStore('projectDetail', () => {
  const project = ref<Project | null>(null)
  const audios = ref<AudioItem[]>([])
  const activeAudioId = ref<string | null>(null)
  const loading = ref(false)
  const { text: error, set: setError } = messageRef()
  let generation = 0
  let stopped = true
  let requestedProjectId: string | null = null

  const activeAudio = computed(() => audios.value.find((audio) => audio.id === activeAudioId.value) || null)
  const anyRunning = computed(() => audios.value.some((audio) => isBusy(audio.job)))

  function isCurrent(id: string, token: number) {
    return !stopped && id === requestedProjectId && token === generation
  }

  async function request<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${path}`)
    if (!response.ok) throw await parseApiError(response)
    return await response.json() as T
  }

  async function fetchProject(id: string) {
    const token = generation
    const value = await request<Project>(`/projects/${encodeURIComponent(id)}`)
    if (isCurrent(id, token)) project.value = value
    return value
  }

  async function fetchAudios(id: string) {
    const token = generation
    const payload = await request<{ items?: unknown }>(`/projects/${encodeURIComponent(id)}/audios`)
    if (!Array.isArray(payload.items)) throw message('app.errors.audioIncomplete')
    const items = payload.items as AudioItem[]
    if (isCurrent(id, token)) {
      audios.value = items
      if (activeAudioId.value && !items.some((audio) => audio.id === activeAudioId.value)) activeAudioId.value = null
    }
    return items
  }

  async function load(id: string, audioId?: string) {
    stop()
    stopped = false
    requestedProjectId = id
    const token = generation
    project.value = null
    audios.value = []
    activeAudioId.value = null
    loading.value = true
    error.value = ''
    try {
      await fetchProject(id)
      if (!isCurrent(id, token)) return
      await fetchAudios(id)
      if (!isCurrent(id, token)) return
      const chosen = audios.value.find((audio) => audio.id === audioId)?.id || audios.value[0]?.id || null
      activeAudioId.value = chosen
    } catch (reason) {
      if (isCurrent(id, token)) {
        setError(reason instanceof Error ? reason : message('app.errors.detail'))
        throw reason
      }
    } finally {
      if (isCurrent(id, token)) loading.value = false
    }
  }

  function selectAudio(audioId: string) {
    if (loading.value || !audios.value.some((audio) => audio.id === audioId)) return false
    activeAudioId.value = audioId
    error.value = ''
    return true
  }

  function stop() {
    stopped = true
    generation += 1
    requestedProjectId = null
    loading.value = false
  }

  return {
    project,
    audios,
    activeAudioId,
    activeAudio,
    anyRunning,
    loading,
    error,
    setError,
    load,
    fetchProject,
    fetchAudios,
    selectAudio,
    stop,
  }
})
