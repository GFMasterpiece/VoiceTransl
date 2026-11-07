import { defineStore } from 'pinia'
import { ref } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { hasMessage, message, messageRef, t } from '../i18n'

export interface ProviderProfile {
  schema_version: string; id: string; provider_type: string; display_name: string; base_url: string | null
  api_key_env: string | null; region: string | null; default_timeout: number; enabled: boolean
  created_at?: string; updated_at?: string; [key: string]: unknown
}
export function readProviderProfile(value: unknown): ProviderProfile {
  const profile = value as ProviderProfile
  if (!profile || profile.schema_version !== '1.0' || typeof profile.id !== 'string' || !profile.id || typeof profile.provider_type !== 'string'
    || typeof profile.display_name !== 'string' || (profile.base_url !== null && typeof profile.base_url !== 'string')
    || (profile.api_key_env !== null && typeof profile.api_key_env !== 'string') || (profile.region !== null && typeof profile.region !== 'string')
    || !Number.isInteger(profile.default_timeout) || typeof profile.enabled !== 'boolean') throw message('profiles.invalidResponse')
  return profile
}
export function profileHost(profile?: ProviderProfile) {
  try { return profile?.base_url ? new URL(profile.base_url).host : '' } catch { return '' }
}
export function providerErrorLabel(error: { type?: string; code?: string; message: string }): string | null {
  const code = error.code || error.type
  if (!code || !hasMessage(`profiles.error_${code}`)) return null
  const status = code.startsWith('QWEN_') ? error.message.match(/\bHTTP\s*([1-5]\d{2})\b/i)?.[1] : undefined
  return `${t(`profiles.error_${code}`)}${status ? ` (${t('profiles.httpStatus', { status })})` : ''}`
}
export const useProviderProfilesStore = defineStore('providerProfiles', () => {
  const items = ref<ProviderProfile[]>([])
  const loading = ref(false)
  const busy = ref(false)
  const loaded = ref(false)
  const errorState = messageRef()
  let generation = 0
  let alive = true
  const requests = new Set<AbortController>()
  async function request(path = '', options?: RequestInit) {
    const controller = new AbortController(); requests.add(controller)
    try {
      const response = await fetch(`${API_BASE_URL}/provider-profiles${path}`, { ...options, signal: controller.signal })
      if (!response.ok) throw await parseApiError(response)
      return response.status === 204 ? null : await response.json()
    } finally { requests.delete(controller) }
  }
  async function load() {
    if (busy.value || !alive) return
    const token = ++generation
    loading.value = true; errorState.set('')
    try {
      const payload = await request() as { items?: unknown }
      if (!Array.isArray(payload?.items)) throw message('profiles.invalidResponse')
      const profiles = payload.items.map(readProviderProfile)
      if (new Set(profiles.map((profile) => profile.id)).size !== profiles.length) throw message('profiles.invalidResponse')
      if (alive && token === generation) { items.value = profiles; loaded.value = true }
    } catch (reason) { if (alive && token === generation) errorState.set(reason); throw reason }
    finally { if (alive && token === generation) loading.value = false }
  }
  async function save(profile: ProviderProfile, creating: boolean) {
    if (busy.value || loading.value || !alive) return null
    const token = ++generation
    busy.value = true; loading.value = false; errorState.set('')
    try {
      const value = readProviderProfile(await request(creating ? '' : `/${encodeURIComponent(profile.id)}`, { method: creating ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(profile) }))
      if (!alive || token !== generation) return null
      items.value = [...items.value.filter((item) => item.id !== value.id), value]
      return value
    } catch (reason) { if (alive && token === generation) errorState.set(reason); throw reason }
    finally { if (alive && token === generation) busy.value = false }
  }
  async function remove(id: string) {
    if (busy.value || loading.value || !alive) return false
    const token = ++generation
    busy.value = true; loading.value = false; errorState.set('')
    try {
      await request(`/${encodeURIComponent(id)}`, { method: 'DELETE' })
      if (!alive || token !== generation) return false
      items.value = items.value.filter((item) => item.id !== id)
      return true
    } catch (reason) { if (alive && token === generation) errorState.set(reason); throw reason }
    finally { if (alive && token === generation) busy.value = false }
  }
  function stop() { alive = false; generation++; for (const controller of requests) controller.abort(); requests.clear() }
  return { items, loading, busy, loaded, error: errorState.text, load, save, remove, stop }
})
