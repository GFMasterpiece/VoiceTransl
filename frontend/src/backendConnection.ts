import { message } from './i18n'

export const BACKEND_ADDRESS_STORAGE_KEY = 'voicetransl.backendAddress'
export const DEFAULT_API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')
export function normalizeBackendAddress(input: string): string {
  const text = input.trim()
  if (!text || /[\\?#\u0000-\u0020\u007f]/.test(text) || /%5c/i.test(text) || text.startsWith('//')) throw message('backend.invalidAddress')
  const relative = text.startsWith('/')
  if (!relative && !/^https?:\/\//i.test(text)) throw message('backend.invalidAddress')
  let url: URL
  try { url = new URL(text, 'https://same-origin.invalid') } catch { throw message('backend.invalidAddress') }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || (!relative && text.split('://')[1]?.split('/')[0]?.includes('@'))) throw message('backend.invalidAddress')
  let path = url.pathname.replace(/\/+$/, '')
  if (!path.endsWith('/api/v1')) path += '/api/v1'
  return relative ? path : `${url.origin}${path}`
}
export function candidateBackendAddress(input: string) { return input.trim().replace(/\/+$/, '') === DEFAULT_API_BASE_URL ? DEFAULT_API_BASE_URL : normalizeBackendAddress(input) }
export function configuredBackendAddress(): string {
  try {
    const stored = typeof window === 'undefined' ? null : window.localStorage.getItem(BACKEND_ADDRESS_STORAGE_KEY)
    if (stored) return candidateBackendAddress(stored)
  } catch { /* Invalid or inaccessible browser preferences retain the deployment default. */ }
  return DEFAULT_API_BASE_URL
}
export function backendIdentity(base: string, pageOrigin = typeof window === 'undefined' ? 'http://localhost' : window.location.origin) {
  const url = new URL(base, pageOrigin)
  return `${url.origin}${url.pathname.replace(/\/+$/, '')}`
}
export function persistBackendAddress(address: string) {
  try { window.localStorage.setItem(BACKEND_ADDRESS_STORAGE_KEY, address) }
  catch { throw message('backend.storageFailed') }
}
export function reloadBackendPage() { window.location.reload() }
export async function probeBackend(base: string, options: { signal?: AbortSignal; timeoutMs?: number } = {}) {
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  const timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 10000)
  let rejectAbort: (() => void) | undefined
  try {
    const cancelled = new Promise<never>((_, reject) => {
      rejectAbort = () => reject(timedOut ? message('backend.timeout') : new DOMException('Aborted', 'AbortError'))
      controller.signal.addEventListener('abort', rejectAbort, { once: true })
      if (controller.signal.aborted) rejectAbort()
    })
    await Promise.race([cancelled, (async () => {
      const response = await fetch(`${base}/health`, { method: 'GET', signal: controller.signal, credentials: 'omit' })
      if (!response.ok) throw message('backend.unavailable')
      const value: unknown = await response.json()
      if (!value || typeof value !== 'object' || (value as { status?: unknown }).status !== 'ok') throw message('backend.unavailable')
    })()])
  } catch (reason) {
    if (controller.signal.aborted) throw timedOut ? message('backend.timeout') : new DOMException('Aborted', 'AbortError')
    throw message('backend.unavailable')
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
    if (rejectAbort) controller.signal.removeEventListener('abort', rejectAbort)
  }
}
