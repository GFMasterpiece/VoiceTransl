import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE_URL } from './api'
import { BACKEND_ADDRESS_STORAGE_KEY, DEFAULT_API_BASE_URL, backendIdentity, configuredBackendAddress, normalizeBackendAddress, persistBackendAddress, probeBackend } from './backendConnection'
const response = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); localStorage.removeItem(BACKEND_ADDRESS_STORAGE_KEY) })
describe('backend address boundary', () => {
  it.each([
    ['http://localhost:8000', 'http://localhost:8000/api/v1'], ['https://API.example:443/voice/', 'https://api.example/voice/api/v1'],
    ['http://[::1]:8000/voice/api/v1/', 'http://[::1]:8000/voice/api/v1'], ['/voice', '/voice/api/v1'], ['/api/v1/', '/api/v1'], ['/', '/api/v1'],
  ])('normalizes %s to %s', (input, expected) => expect(normalizeBackendAddress(input)).toBe(expected))
  it.each(['', '//api.test', 'ftp://api.test', 'javascript:alert(1)', 'api.test', 'https://u:p@api.test', 'https://@api.test', 'https://api.test?x=1', '/voice#fragment', '/api/v1?', 'https://api.test\\voice', '/voice%5cpath', 'http://api .test'])('rejects unsafe or ambiguous address %s', input => expect(() => normalizeBackendAddress(input)).toThrow())
  it('persists preferences without hot-switching existing API imports', () => {
    expect(configuredBackendAddress()).toBe(DEFAULT_API_BASE_URL)
    const before = API_BASE_URL
    persistBackendAddress('https://api.test/voice/api/v1')
    expect(configuredBackendAddress()).toBe('https://api.test/voice/api/v1'); expect(API_BASE_URL).toBe(before)
    localStorage.setItem(BACKEND_ADDRESS_STORAGE_KEY, '//unsafe.test'); expect(configuredBackendAddress()).toBe(DEFAULT_API_BASE_URL)
    expect(backendIdentity('/api/v1', 'https://ui.test')).toBe(backendIdentity('https://UI.test:443/api/v1/'))
  })
  it('keeps the old preference when storage cannot be written', () => {
    localStorage.setItem(BACKEND_ADDRESS_STORAGE_KEY, '/old/api/v1')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => persistBackendAddress('/new/api/v1')).toThrow(); expect(localStorage.getItem(BACKEND_ADDRESS_STORAGE_KEY)).toBe('/old/api/v1')
  })
})
describe('backend health probes', () => {
  it('checks the health protocol without sending credentials', async () => {
    const fetcher = vi.fn(async () => response({ status: 'ok' })); vi.stubGlobal('fetch', fetcher)
    await probeBackend('https://api.test/voice/api/v1')
    expect(fetcher).toHaveBeenCalledWith('https://api.test/voice/api/v1/health', expect.objectContaining({ credentials: 'omit', method: 'GET' }))
  })
  it.each([response({ status: 'wrong' }), response({ status: 'ok' }, false), response('<html>')])('rejects an unhealthy or unrelated service', async value => {
    vi.stubGlobal('fetch', vi.fn(async () => value)); await expect(probeBackend('/api/v1')).rejects.toThrow()
  })
  it('times out after ten seconds even if a transport ignores cancellation', async () => {
    vi.useFakeTimers(); const fetcher = vi.fn((_path: string, _options: RequestInit) => new Promise<Response>(() => {})); vi.stubGlobal('fetch', fetcher)
    const result = probeBackend('/api/v1').catch(error => error)
    await vi.advanceTimersByTimeAsync(10000)
    expect(await result).toBeInstanceOf(Error); expect(fetcher.mock.calls[0]![1]!.signal!.aborted).toBe(true)
  })
  it('aborts a pending request without waiting for the network', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))); const controller = new AbortController()
    const result = probeBackend('/api/v1', { signal: controller.signal }).catch(error => error); controller.abort()
    expect((await result).name).toBe('AbortError')
  })
})


it('preserves a deployment-defined nonstandard API base when restoring and reloading the saved default', async () => {
  vi.stubEnv('VITE_API_BASE_URL', '/bridge'); vi.resetModules()
  const deployment = await import('./backendConnection')
  expect(deployment.DEFAULT_API_BASE_URL).toBe('/bridge')
  expect(deployment.candidateBackendAddress('/bridge')).toBe('/bridge')
  expect(deployment.normalizeBackendAddress('/other-service')).toBe('/other-service/api/v1')
  deployment.persistBackendAddress(deployment.DEFAULT_API_BASE_URL)
  expect(deployment.configuredBackendAddress()).toBe('/bridge')
  const fetcher = vi.fn(async (_path: string) => response({ status: 'ok' })); vi.stubGlobal('fetch', fetcher)
  await deployment.probeBackend(deployment.candidateBackendAddress('/bridge'))
  expect(fetcher.mock.calls[0]![0]).toBe('/bridge/health')
})
