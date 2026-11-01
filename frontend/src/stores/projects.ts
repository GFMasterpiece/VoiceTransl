import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { message, messageRef } from '../i18n'

export interface Audio {
  id: string
  source: AudioSource
}

export interface AudioSource {
  original_name: string
  language: string
  size_bytes: number
  media_type?: string | null
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
  source_language?: string | null
  target_language?: string | null
  source: AudioSource | null
}

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<Project[]>([])
  const total = ref(0)
  const limit = ref(20)
  const offset = ref(0)
  const loading = ref(false)
  let fetchGeneration = 0
  const creating = ref(false)
  const { text: error, set: setError } = messageRef()

  async function fetchProjects(nextOffset = offset.value) {
    const requestedOffset = Number.isInteger(nextOffset) && nextOffset >= 0 ? nextOffset : 0
    const generation = ++fetchGeneration
    loading.value = true
    error.value = ''
    try {
      const response = await fetch(`${API_BASE_URL}/projects?limit=${limit.value}&offset=${requestedOffset}`)
      if (!response.ok) throw await parseApiError(response)
      const payload = (await response.json()) as { items?: unknown; total?: unknown; limit?: unknown; offset?: unknown }
      if (!Array.isArray(payload.items) || !Number.isInteger(payload.total) || Number(payload.total) < 0
        || !Number.isInteger(payload.limit) || Number(payload.limit) < 1 || !Number.isInteger(payload.offset) || Number(payload.offset) < 0) {
        throw message('app.errors.listIncomplete')
      }
      if (generation !== fetchGeneration) return
      projects.value = payload.items as Project[]
      total.value = Number(payload.total)
      limit.value = Number(payload.limit)
      offset.value = Number(payload.offset)
    } catch (reason) {
      if (generation === fetchGeneration) setError(reason instanceof Error ? reason : message('app.errors.list'))
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
      if (!response.ok) throw await parseApiError(response)
      const payload = (await response.json()) as { project: Project }
      total.value += 1
      // Server ordering is authoritative; refresh instead of locally guessing order.
      await fetchProjects(offset.value)
      return payload.project
    } catch (reason) {
      setError(reason instanceof Error ? reason : message('app.errors.create'))
      throw reason
    } finally {
      creating.value = false
    }
  }

  async function deleteProject(id: string) {
    ++fetchGeneration
    error.value = ''
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!response.ok) throw await parseApiError(response)
    const before = projects.value.length
    projects.value = projects.value.filter((project) => project.id !== id)
    const removed = before - projects.value.length
    total.value = Math.max(0, total.value - removed)
    if (!removed) return
    if (!projects.value.length && offset.value > 0) await fetchProjects(Math.max(0, offset.value - limit.value))
    else if (total.value > offset.value + projects.value.length) await fetchProjects(offset.value)
  }

  async function createEmptyProject(name: string, sourceLanguage: string, targetLanguage: string) {
    ++fetchGeneration
    creating.value = true
    error.value = ''
    try {
      const response = await fetch(`${API_BASE_URL}/projects/empty`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), source_language: sourceLanguage, target_language: targetLanguage }),
      })
      if (!response.ok) throw await parseApiError(response)
      const payload = await response.json() as { project: Project; job: null }
      total.value += 1
      await fetchProjects(offset.value)
      return payload.project
    } catch (reason) {
      setError(reason instanceof Error ? reason : message('app.errors.create'))
      throw reason
    } finally {
      creating.value = false
    }
  }

  async function addAudio(projectId: string, file: File, language: string) {
    const form = new FormData()
    form.append('file', file)
    form.append('source_language', language)
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/audios`, { method: 'POST', body: form })
    if (!response.ok) throw await parseApiError(response)
    const payload = await response.json() as { audio: Audio }
    const project = projects.value.find((entry) => entry.id === projectId)
    if (project) project.audios = [...(project.audios || (project.source ? [{ id: 'aud_primary', source: project.source }] : [])), payload.audio]
    return payload.audio
  }

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
  const hasPrevious = computed(() => offset.value > 0)
  const hasNext = computed(() => offset.value + limit.value < total.value)
  async function previousPage() { await fetchProjects(Math.max(0, offset.value - limit.value)) }
  async function nextPage() { if (hasNext.value) await fetchProjects(offset.value + limit.value) }
  return { projects, total, limit, offset, pageCount, hasPrevious, hasNext, loading, creating, error, setError, fetchProjects, previousPage, nextPage, createProject, createEmptyProject, addAudio, deleteProject }
})
