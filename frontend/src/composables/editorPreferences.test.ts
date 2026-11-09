import { afterEach, expect, it, vi } from 'vitest'

const key = 'voicetransl.debugMode'
afterEach(() => { window.localStorage.removeItem(key); vi.restoreAllMocks(); vi.resetModules() })
it('defaults debug off, persists explicit changes and restores the browser preference', async () => {
  window.localStorage.removeItem(key); vi.resetModules()
  const initial = await import('./editorPreferences')
  expect(initial.debugMode.value).toBe(false)
  initial.setDebugMode(true); expect(window.localStorage.getItem(key)).toBe('true')
  vi.resetModules(); const restored = await import('./editorPreferences'); expect(restored.debugMode.value).toBe(true)
  restored.setDebugMode(false); expect(window.localStorage.getItem(key)).toBe('false')
})
it('keeps a usable session preference when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  vi.resetModules(); const preferences = await import('./editorPreferences')
  expect(preferences.debugMode.value).toBe(false)
  expect(() => preferences.setDebugMode(true)).not.toThrow(); expect(preferences.debugMode.value).toBe(true)
})
