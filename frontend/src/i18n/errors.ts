import { computed, shallowRef, type WritableComputedRef } from 'vue'
import { t, type MessageParams } from './index'

export class LocalizedError extends Error {
  readonly key: string
  readonly params?: MessageParams

  constructor(key: string, params?: MessageParams) {
    super()
    this.name = 'LocalizedError'
    this.key = key
    this.params = params
    Object.defineProperty(this, 'message', {
      configurable: true,
      get: () => t(this.key, this.params),
    })
  }
}

export function message(key: string, params?: MessageParams): LocalizedError {
  return new LocalizedError(key, params)
}

export function displayError(reason: unknown, fallbackKey = 'common.errors.unexpected'): string {
  if (reason instanceof LocalizedError) return reason.message
  if (typeof reason === 'string') return reason
  if (reason instanceof Error) {
    if (/^(?:failed to fetch|fetch failed|network ?error(?: when attempting to fetch resource)?\.?|network request failed|load failed)$/i.test(reason.message.trim())) {
      return t('common.errors.network')
    }
    if (reason.message.trim()) return reason.message
  }
  return t(fallbackKey)
}

export function messageRef(initial: unknown = ''): {
  text: WritableComputedRef<string>
  set: (reason: unknown) => void
} {
  const raw = shallowRef(initial)
  const set = (reason: unknown) => { raw.value = reason }
  return {
    text: computed({ get: () => displayError(raw.value), set }),
    set,
  }
}
