import { computed } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseApiError, type ApiError } from '../api'
import { displayError, initializeLocale, locale, LOCALE_STORAGE_KEY, message, messageRef, setLocale, t } from './index'
import { appMessages } from './messages/app'
import { commonMessages } from './messages/common'
import { workflowMessages } from './messages/workflow'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

function dictionary(value: object, prefix = ''): Record<string, string> {
  return Object.fromEntries(Object.entries(value).flatMap(([key, child]) => {
    const path = prefix ? `${prefix}.${key}` : key
    return typeof child === 'string' ? [[path, child]] : Object.entries(dictionary(child, path))
  }))
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{([\w]+)\}/g)].map((match) => match[1]!).sort()
}

describe('translation catalogs', () => {
  it.each([
    ['common', commonMessages], ['app', appMessages], ['workflow', workflowMessages],
  ] as const)('%s has matching keys and interpolation arguments', (name, catalog) => {
    const chinese = dictionary(catalog['zh-CN'])
    const english = dictionary(catalog.en)
    expect(Object.keys(english).sort()).toEqual(Object.keys(chinese).sort())
    for (const [key, value] of Object.entries(chinese)) {
      expect(value.trim(), key).not.toBe('')
      expect(english[key]?.trim(), key).not.toBe('')
      expect(placeholders(english[key]!), key).toEqual(placeholders(value))
    }
    for (const current of ['zh-CN', 'en'] as const) {
      setLocale(current)
      for (const [key, value] of Object.entries(dictionary(catalog[current]))) {
        const params = Object.fromEntries(placeholders(value).map((argument) => [argument, `example-${argument}`]))
        expect(t(`${name}.${key}`, params), `${current}.${name}.${key}`)
          .toBe(value.replace(/\{([\w]+)\}/g, (_match, argument: string) => params[argument]!))
      }
    }
  })

  it('interpolates reactively without requiring a component plugin', () => {
    const translated = computed(() => t('common.errors.requestFailed', { status: 503 }))
    expect(translated.value).toBe('请求失败（503）')
    setLocale('en')
    expect(translated.value).toBe('Request failed (503)')
  })
})

describe('locale preferences', () => {
  it('starts tests in Chinese rather than detecting the browser at import', () => {
    expect(locale.value).toBe('zh-CN')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBeNull()
  })

  it('persists a validated selection and updates document metadata', () => {
    setLocale('en')
    expect(locale.value).toBe('en')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(document.title).toBe('VoiceTransl · Audio Translation Workspace')
    setLocale('fr')
    expect(locale.value).toBe('en')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
    setLocale('zh_CN')
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.title).toBe('VoiceTransl · 语音翻译工作台')
  })

  it('prefers a valid saved selection over browser languages', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'EN')
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['zh-CN'])
    initializeLocale()
    expect(locale.value).toBe('en')
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('en')
  })

  it.each([
    [['fr-FR', 'en-GB', 'zh-CN'], 'en'],
    [['zh-TW', 'en-US'], 'zh-CN'],
    [['de-DE'], 'zh-CN'],
  ] as const)('uses the first supported browser language %j', (languages, expected) => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, '{corrupt')
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue([...languages])
    initializeLocale()
    expect(locale.value).toBe(expected)
  })

  it('uses navigator.language when the language list is empty', () => {
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue([])
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-US')
    initializeLocale()
    expect(locale.value).toBe('en')
  })

  it('remains usable when storage access throws', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new DOMException('Blocked', 'SecurityError') })
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['en-US'])
    expect(() => initializeLocale()).not.toThrow()
    expect(locale.value).toBe('en')
    expect(() => setLocale('zh-CN')).not.toThrow()
    expect(document.documentElement.lang).toBe('zh-CN')
  })

  it('keeps a saved selection when only storage writes are blocked', () => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, 'en')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('Quota exceeded', 'QuotaExceededError') })
    expect(() => initializeLocale()).not.toThrow()
    expect(locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('falls back safely without browser globals', () => {
    vi.stubGlobal('window', undefined)
    vi.stubGlobal('document', undefined)
    vi.stubGlobal('navigator', undefined)
    expect(() => initializeLocale()).not.toThrow()
    expect(locale.value).toBe('zh-CN')
  })
})

describe('localized errors', () => {
  it('keeps an existing error and writable message reference reactive', () => {
    const reason = message('common.errors.requestFailed', { status: 409 })
    const error = messageRef(reason)
    expect(reason).toBeInstanceOf(Error)
    expect(reason.message).toBe('请求失败（409）')
    expect(error.text.value).toBe('请求失败（409）')
    setLocale('en')
    expect(reason.message).toBe('Request failed (409)')
    expect(error.text.value).toBe('Request failed (409)')
    error.text.value = ''
    expect(error.text.value).toBe('')
    error.set(message('common.api.PROJECT_NOT_FOUND'))
    expect(error.text.value).toBe('Project not found')
  })

  it('preserves explicit user or extension messages and translates network errors', () => {
    const error = messageRef(new TypeError('Failed to fetch'))
    expect(error.text.value).toBe(t('common.errors.network'))
    setLocale('en')
    expect(error.text.value).toBe('Unable to connect. Check your network and backend service, then try again.')
    expect(displayError('自定义扩展错误')).toBe('自定义扩展错误')
    expect(displayError(new Error('Provider-specific diagnostic'))).toBe('Provider-specific diagnostic')
    expect(displayError({ invalid: true })).toBe(t('common.errors.unexpected'))
    expect(displayError(new Error())).toBe(t('common.errors.unexpected'))
    expect(messageRef().text.value).toBe('')
  })
})

describe('API error handling', () => {
  it('translates known structured error codes and retains metadata', async () => {
    const error = await parseApiError(new Response(JSON.stringify({
      error: { code: 'PROJECT_BUSY', message: '后端原始错误', details: { audio_id: 'aud_1' } },
    }), { status: 409 })) as ApiError
    const current = messageRef(error)
    expect(error.code).toBe('PROJECT_BUSY')
    expect(error.name).toBe('PROJECT_BUSY')
    expect(error.status).toBe(409)
    expect(error.details).toEqual({ audio_id: 'aud_1' })
    expect(error.serverMessage).toBe('后端原始错误')
    expect(current.text.value).toBe('任务正在运行或排队，请先取消任务')
    setLocale('en')
    expect(error.message).toBe('A task is running or queued. Cancel it first.')
    expect(current.text.value).toBe(error.message)
  })

  it('accepts the compatible nested detail error shape', async () => {
    const error = await parseApiError(new Response(JSON.stringify({
      detail: { error: { code: 'WORKFLOW_REVISION_CONFLICT', message: 'old message' } },
    }), { status: 409 }))
    setLocale('en')
    expect(error.name).toBe('WORKFLOW_REVISION_CONFLICT')
    expect(error.message).toBe('The workflow has changed. Refresh and try again.')
  })

  it('preserves unknown extension codes and diagnostic messages', async () => {
    const error = await parseApiError(new Response(JSON.stringify({
      error: { code: 'EXTENSION_RATE_LIMIT', message: 'Provider quota depleted', details: ['retry later'] },
    }), { status: 429 })) as ApiError
    setLocale('en')
    expect(error.code).toBe('EXTENSION_RATE_LIMIT')
    expect(error.message).toBe('Provider quota depleted')
    expect(error.details).toEqual(['retry later'])
    setLocale('zh-CN')
    expect(error.message).toBe('Provider quota depleted')
  })

  it('retains unknown error metadata even when its server message is absent', async () => {
    const error = await parseApiError(new Response(JSON.stringify({
      error: { code: 'EXTENSION_UNAVAILABLE', details: { provider: 'custom' } },
    }), { status: 503 })) as ApiError
    expect(error.code).toBe('EXTENSION_UNAVAILABLE')
    expect(error.details).toEqual({ provider: 'custom' })
    setLocale('en')
    expect(error.message).toBe('Request failed (503)')
  })

  it.each(['not json', '{}', 'null', '[]'])('uses a translated HTTP fallback for %s', async (body) => {
    const error = await parseApiError(new Response(body, { status: 502 })) as ApiError
    expect(error.message).toBe('请求失败（502）')
    expect(error.status).toBe(502)
    setLocale('en')
    expect(error.message).toBe('Request failed (502)')
  })

  it('retains unstructured legacy detail strings', async () => {
    const error = await parseApiError(new Response(JSON.stringify({ detail: 'Legacy diagnostic' }), { status: 400 }))
    expect(error.message).toBe('Legacy diagnostic')
  })
})
