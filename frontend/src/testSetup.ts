import { settingsCache } from './composables/globalSettings'
import { emptySettings } from './workflow/fixtures'
import { beforeEach } from 'vitest'
import { setLocale } from './i18n'

beforeEach(() => {
  settingsCache.value = emptySettings()
  if (typeof window !== 'undefined') window.localStorage.clear()
  if (typeof window !== 'undefined') window.sessionStorage.clear()
  setLocale('zh-CN')
  if (typeof window !== 'undefined') window.localStorage.clear()
})
