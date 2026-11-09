import { rerunMessages } from './messages/rerun'
import { settingsMessages } from './messages/settings'
import { createI18n } from 'vue-i18n'
import { appMessages } from './messages/app'
import { commonMessages } from './messages/common'
import { workflowMessages } from './messages/workflow'
import { workflowPanelMessages } from './messages/workflowPanels'
import { editorOperationMessages } from './messages/editorOperations'
import { batchMessages } from './messages/batch'
import { languageMessages } from './messages/languages'
import { connectionMessages } from './messages/connections'

export type Locale = 'zh-CN' | 'en'
export type MessageParams = Record<string, string | number>
export const LOCALE_STORAGE_KEY = 'voicetransl.locale'
export const supportedLocales: readonly Locale[] = ['zh-CN', 'en']

export const i18n = createI18n({
  legacy: false,
  locale: 'zh-CN' as Locale,
  fallbackLocale: 'zh-CN',
  missingWarn: false,
  fallbackWarn: false,
  messages: {
    'zh-CN': { settings: settingsMessages['zh-CN'], languages: languageMessages['zh-CN'], connections: connectionMessages['zh-CN'], batch: batchMessages['zh-CN'], common: commonMessages['zh-CN'], app: appMessages['zh-CN'], workflow: { ...workflowMessages['zh-CN'], ...rerunMessages['zh-CN'], ...workflowPanelMessages['zh-CN'], ...editorOperationMessages['zh-CN'], node_source_info_label: batchMessages['zh-CN'].node_source_info_label, node_source_info_add: batchMessages['zh-CN'].node_source_info_add } },
    en: { settings: settingsMessages.en, languages: languageMessages.en, connections: connectionMessages.en, batch: batchMessages.en, common: commonMessages.en, app: appMessages.en, workflow: { ...workflowMessages.en, ...rerunMessages.en, ...workflowPanelMessages.en, ...editorOperationMessages.en, node_source_info_label: batchMessages.en.node_source_info_label, node_source_info_add: batchMessages.en.node_source_info_add } },
  },
})

export const locale = i18n.global.locale

export function t(key: string, params?: MessageParams): string {
  return params ? i18n.global.t(key, params) : i18n.global.t(key)
}

export function hasMessage(key: string): boolean {
  return i18n.global.te(key, locale.value)
}

function normalizeLocale(value: string): Locale | undefined {
  const normalized = value.trim().replace(/_/g, '-').toLowerCase()
  if (normalized === 'zh-cn') return 'zh-CN'
  if (normalized === 'en') return 'en'
  return undefined
}

function updateDocument(): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale.value
  document.title = t('common.appTitle')
}

export function setLocale(value: string): void {
  const next = normalizeLocale(value)
  if (!next) return
  locale.value = next
  updateDocument()
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(LOCALE_STORAGE_KEY, next)
  } catch { /* The current tab remains usable when browser storage is blocked. */ }
}

export function initializeLocale(): void {
  let saved: Locale | undefined
  try {
    const value = typeof window !== 'undefined' ? window.localStorage.getItem(LOCALE_STORAGE_KEY) : null
    if (value) saved = normalizeLocale(value)
  } catch { /* Browser detection still works when reading storage is blocked. */ }

  let detected: Locale | undefined
  if (!saved && typeof navigator !== 'undefined') {
    for (const language of navigator.languages?.length ? navigator.languages : [navigator.language]) {
      if (/^zh(?:-|$)/i.test(language)) { detected = 'zh-CN'; break }
      if (/^en(?:-|$)/i.test(language)) { detected = 'en'; break }
    }
  }
  setLocale(saved || detected || 'zh-CN')
}

export { LocalizedError, message, messageRef, displayError } from './errors'
