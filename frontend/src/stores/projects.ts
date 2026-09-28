import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Project {
  id: string
  name: string
  created_at: string
  updated_at: string
  preset: string
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
  const loading = ref(false)
  const creating = ref(false)
  const error = ref('')

  async function fetchProjects() {
    loading.value = true
    error.value = ''
    try {
      const response = await fetch(`${API_BASE_URL}/projects`)
      if (!response.ok) throw await parseError(response)
      const payload = (await response.json()) as { items: Project[] }
      projects.value = payload.items
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '项目列表加载失败'
    } finally {
      loading.value = false
    }
  }

  async function createProject(file: File, name: string, language: string, preset = 'mock') {
    creating.value = true
    error.value = ''
    try {
      const form = new FormData()
      form.append('file', file)
      if (name.trim()) form.append('name', name.trim())
      form.append('source_language', language)
      form.append('preset', preset)

      const response = await fetch(`${API_BASE_URL}/projects`, { method: 'POST', body: form })
      if (!response.ok) throw await parseError(response)
      const payload = (await response.json()) as { project: Project }
      projects.value = [payload.project, ...projects.value]
      return payload.project
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : '项目创建失败'
      throw reason
    } finally {
      creating.value = false
    }
  }

  return { projects, loading, creating, error, fetchProjects, createProject }
})
