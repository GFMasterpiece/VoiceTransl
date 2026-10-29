import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export interface Audio {
  id: string
  source: Project['source']
}

export interface ProjectPage {
  items: Project[]
  total: number
  limit: number
  offset: number
}

export interface Project {
  id: string
  name: string
  created_at: string
  updated_at: string
  preset?: string
  audios?: Audio[]
  source: {
    original_name: string
    language: string
    size_bytes: number
    media_type?: string | null
  }
}

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')

async function parseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as {
      error?: { message?: string }
      detail?: { message?: string; error?: { message?: string } } | string
    }
    const apiMessage = payload.error?.message
    const detail = payload.detail
    const message = typeof detail === 'string' ? detail : detail?.error?.message || detail?.message
    return new Error(apiMessage || message || `请求失败（${response.status}）`)
  } catch {
    return new Error(`请求失败（${response.status}）`)
  }
}

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<Project[]>([])
  const total = ref(0)
  const limit = ref(20)
  const offset = ref(0)
  const loading = ref(false)
  let fetchGeneration = 0
  const creating = ref(false)
  const error = ref('')

  async function fetchProjects(nextOffset = offset.value) {
    const requestedOffset = Number.isInteger(nextOffset) && nextOffset >= 0 ? nextOffset : 0
    const generation = ++fetchGeneration
    loading.value = true
    error.value = ''
    try {
      const response = await fetch(`${API_BASE_URL}/projects?limit=${limit.value}&offset=${requestedOffset}`)
      if (!response.ok) throw await parseError(response)
      const payload = (await response.json()) as { items?: unknown; total?: unknown; limit?: unknown; offset?: unknown }
      if (!Array.isArray(payload.items) || !Number.isInteger(payload.total) || Number(payload.total) < 0
        || !Number.isInteger(payload.limit) || Number(payload.limit) < 1 || !Number.isInteger(payload.offset) || Number(payload.offset) < 0) {
        throw new Error('项目列表响应不完整，请检查后端版本后重试。')
      }
      if (generation !== fetchGeneration) return
      projects.value = payload.items as Project[]
      total.value = Number(payload.total)
      limit.value = Number(payload.limit)
      offset.value = Number(payload.offset)
    } catch (reason) {
      if (generation === fetchGeneration) error.value = reason instanceof Error ? reason.message : '项目列表加载失败'
    } finally {
      if (generation === fetchGeneration) loading.value = false
    }
  }

  async function createProject(file: File, name: string, language: string, targetLanguage?: string) {
    ++fetchGeneration
    creating.value = true
    error.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      if (name.trim()) form.append('name', name.trim())
      form.append('source_language', language)
      if (targetLanguage) form.append('target_language', targetLanguage)

      const response = await fetch(`${API_BASE_URL}/projects`, { method: 'POST', body: form })
      if (!response.ok) throw await parseError(response)
      const payload = (await response.json()) as { project: Project }
      total.value += 1
      // Server ordering is authoritative; refresh instead of locally guessing order.
      await fetchProjects(offset.value)
      return payload.project
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '项目创建失败'
      throw reason
    } finally {
      creating.value = false
    }
  }

  async function deleteProject(id: string) {
    ++fetchGeneration
    error.value = ''
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!response.ok) throw await parseError(response)
    const before = projects.value.length
    projects.value = projects.value.filter((project) => project.id !== id)
    const removed = before - projects.value.length
    total.value = Math.max(0, total.value - removed)
    if (!removed) return
    if (!projects.value.length && offset.value > 0) await fetchProjects(Math.max(0, offset.value - limit.value))
    else if (total.value > offset.value + projects.value.length) await fetchProjects(offset.value)
  }

  async function addAudio(projectId: string, file: File, language: string) {
    const form = new FormData()
    form.append('file', file)
    form.append('source_language', language)
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/audios`, { method: 'POST', body: form })
    if (!response.ok) throw await parseError(response)
    const payload = await response.json() as { audio: Audio }
    const project = projects.value.find((entry) => entry.id === projectId)
    if (project) project.audios = [...(project.audios || [{ id: 'aud_primary', source: project.source }]), payload.audio]
    return payload.audio
  }

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
  const hasPrevious = computed(() => offset.value > 0)
  const hasNext = computed(() => offset.value + limit.value < total.value)
  async function previousPage() { await fetchProjects(Math.max(0, offset.value - limit.value)) }
  async function nextPage() { if (hasNext.value) await fetchProjects(offset.value + limit.value) }
  return { projects, total, limit, offset, pageCount, hasPrevious, hasNext, loading, creating, error, fetchProjects, previousPage, nextPage, createProject, addAudio, deleteProject }
})
