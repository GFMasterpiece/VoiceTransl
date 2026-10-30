import { hasMessage, message } from './i18n'

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')

export interface ApiError extends Error {
  code?: string
  status: number
  details?: unknown
  serverMessage?: string
}

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

export async function parseApiError(response: Response): Promise<Error> {
  let payload: Record<string, unknown> | undefined
  try { payload = record(await response.json()) } catch { /* Fall back to the HTTP status. */ }
  const detail = record(payload?.detail)
  const error = record(payload?.error) || record(detail?.error) || detail
  const code = typeof error?.code === 'string' ? error.code : undefined
  const serverMessage = typeof error?.message === 'string' && error.message.trim()
    ? error.message
    : typeof payload?.detail === 'string' && payload.detail.trim() ? payload.detail : undefined
  const key = code ? `common.api.${code}` : undefined
  const result = key && hasMessage(key)
    ? message(key, { status: response.status })
    : serverMessage ? new Error(serverMessage) : message('common.errors.requestFailed', { status: response.status })
  const value = result as ApiError
  value.name = code || `HTTP_${response.status}`
  value.code = code
  value.status = response.status
  value.details = error?.details
  value.serverMessage = serverMessage
  return value
}
