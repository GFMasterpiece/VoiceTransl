import { afterEach, expect, it, vi } from 'vitest'
import { createApp, nextTick, type App } from 'vue'
import BackendConnectionDialog from './components/BackendConnectionDialog.vue'
import * as backend from './backendConnection'
const response = () => ({ ok: true, json: async () => ({ status: 'ok' }) }) as Response
let app: App | undefined; let root: HTMLDivElement | undefined
async function settle() { for (let count = 0; count < 30; count++) await Promise.resolve(); await nextTick() }
function mount() { root = document.createElement('div'); document.body.append(root); const close = vi.fn(); const switching = vi.fn(); app = createApp(BackendConnectionDialog, { open: true, onClose: close, onSwitching: switching }); app.mount(root); return { close, switching } }
async function edit(value: string) { const input = root!.querySelector<HTMLInputElement>('[data-backend-address]')!; input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick() }
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.removeItem(backend.BACKEND_ADDRESS_STORAGE_KEY) })
it('saves a normalized healthy address and reloads only after storage and switching state', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response())); const reload = vi.spyOn(backend, 'reloadBackendPage').mockImplementation(() => {})
  const { switching } = mount(); await edit('https://api.test/voice')
  root!.querySelector<HTMLButtonElement>('[data-backend-save]')!.click(); await settle()
  expect(localStorage.getItem(backend.BACKEND_ADDRESS_STORAGE_KEY)).toBe('https://api.test/voice/api/v1')
  expect(switching).toHaveBeenCalledOnce(); expect(reload).toHaveBeenCalledOnce(); expect(root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.disabled).toBe(true)
})
it('only fills the deployment default until a healthy save succeeds and reports storage failure', async () => {
  localStorage.setItem(backend.BACKEND_ADDRESS_STORAGE_KEY, '/old/api/v1'); const reload = vi.spyOn(backend, 'reloadBackendPage').mockImplementation(() => {})
  vi.stubGlobal('fetch', vi.fn(async () => response())); const { switching } = mount(); await edit('https://api.test')
  root!.querySelector<HTMLButtonElement>('[data-backend-reset]')!.click(); await nextTick()
  expect(root!.querySelector<HTMLInputElement>('[data-backend-address]')!.value).toBe(backend.DEFAULT_API_BASE_URL)
  expect(localStorage.getItem(backend.BACKEND_ADDRESS_STORAGE_KEY)).toBe('/old/api/v1')
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  root!.querySelector<HTMLButtonElement>('[data-backend-save]')!.click(); await settle()
  expect(root!.querySelector('[role=alert]')).not.toBeNull(); expect(reload).not.toHaveBeenCalled(); expect(switching).not.toHaveBeenCalled()
  expect(localStorage.getItem(backend.BACKEND_ADDRESS_STORAGE_KEY)).toBe('/old/api/v1')
})
it('ignores successful stale save probes after editing, resetting or closing', async () => {
  const requests: { resolve: (response: Response) => void; signal: AbortSignal }[] = []
  vi.stubGlobal('fetch', vi.fn((_path: unknown, options: RequestInit) => new Promise<Response>(resolve => requests.push({ resolve, signal: options.signal! }))))
  const reload = vi.spyOn(backend, 'reloadBackendPage').mockImplementation(() => {}); const { close } = mount()
  await edit('https://old.test'); root!.querySelector<HTMLButtonElement>('[data-backend-save]')!.click(); await settle()
  await edit('https://new.test'); expect(requests[0]!.signal.aborted).toBe(true); requests[0]!.resolve(response()); await settle()
  expect(reload).not.toHaveBeenCalled(); expect(root!.querySelector('[role=status]')).toBeNull()
  root!.querySelector<HTMLButtonElement>('[data-backend-save]')!.click(); await settle()
  root!.querySelector<HTMLButtonElement>('[data-backend-reset]')!.click(); expect(requests[1]!.signal.aborted).toBe(true); requests[1]!.resolve(response()); await settle()
  root!.querySelector<HTMLButtonElement>('[data-backend-save]')!.click(); await settle()
  root!.querySelector<HTMLButtonElement>('.canvas-dialog-close')!.click(); expect(close).toHaveBeenCalledOnce(); expect(requests[2]!.signal.aborted).toBe(true)
  requests[2]!.resolve(response()); await settle(); expect(reload).not.toHaveBeenCalled(); expect(localStorage.getItem(backend.BACKEND_ADDRESS_STORAGE_KEY)).toBeNull()
})
