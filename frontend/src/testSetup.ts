import { beforeEach } from 'vitest'
import { setLocale } from './i18n'

beforeEach(() => {
  if (typeof window !== 'undefined') window.localStorage.clear()
  setLocale('zh-CN')
  if (typeof window !== 'undefined') window.localStorage.clear()
})
