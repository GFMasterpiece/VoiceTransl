import { beforeEach } from 'vitest'
import { setLocale } from './i18n'

beforeEach(() => {
  if (typeof window !== 'undefined') window.localStorage.clear()
  if (typeof window !== 'undefined') window.sessionStorage.clear()
  setLocale('zh-CN')
  if (typeof window !== 'undefined') window.localStorage.clear()
})
