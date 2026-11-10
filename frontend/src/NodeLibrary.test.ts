import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, ref, type App } from 'vue'
import NodeLibrary from './components/NodeLibrary.vue'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry } from './workflow/nodeRegistry'
import catalog from './workflow/modularCapabilities.fixture.json'
import { setLocale } from './i18n'
import { settingsCache } from './composables/globalSettings'
let app: App | undefined, host: HTMLDivElement | undefined
async function settle() { await nextTick(); await nextTick(); await nextTick() }
afterEach(() => { app?.unmount(); host?.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); settingsCache.value = null; setLocale('zh-CN') })
async function mount(options: { narrow?: boolean; disabled?: string; privateDefaults?: boolean; extension?: boolean } = {}) {
  const media = { matches: !!options.narrow, addEventListener: vi.fn(), removeEventListener: vi.fn() }
  vi.stubGlobal('matchMedia', vi.fn(() => media))
  const raw = structuredClone(catalog)
  if (options.privateDefaults) raw.workflow.nodes.find(node => node.type === 'llm_service')!.config.fields.find(field => field.key === 'credential_ref')!.default = 'private-catalog-reference' as never
  if (options.extension) raw.workflow.nodes.push({ ...raw.workflow.nodes.find(node => node.type === 'string_value')!, type: 'future_zebra', label: 'Zebra', add_label: 'Zebra', category: null } as never)
  const registry = createNodeRegistry(readCapabilities(raw))
  const kinds = ref([...registry.kinds])
  const add = vi.fn(), close = vi.fn(), fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  host = document.createElement('div'); document.body.append(host)
  app = createApp({ render: () => h(NodeLibrary, { registry, kinds: kinds.value, disabledReason: () => options.disabled || '', onAdd: add, onClose: close }) }); app.mount(host); await settle()
  const search = host.querySelector<HTMLInputElement>('input[type=search]')!
  async function query(value: string) { search.value = value; search.dispatchEvent(new Event('input', { bubbles: true })); await settle() }
  return { registry, kinds, add, close, fetcher, search, query, media }
}
it('previews on hover and details without adding, then emits exactly once for a row click', async () => {
  const { add } = await mount()
  const row = host!.querySelector<HTMLElement>('[data-add-kind=llm_model]')!
  row.dispatchEvent(new MouseEvent('mouseenter')); await settle()
  expect(host!.querySelector('[data-node-library-preview]')!.textContent).toContain('llm_model')
  row.querySelector<HTMLButtonElement>('[data-library-item-details]')!.click(); await settle(); expect(add).not.toHaveBeenCalled()
  row.click(); expect(add).toHaveBeenCalledOnce(); expect(add).toHaveBeenCalledWith('llm_model')
})
it('combines actual category, text, input and output type filters including dynamic parameter ports', async () => {
  const { query } = await mount()
  host!.querySelector<HTMLButtonElement>('[data-library-category=llm]')!.click(); await settle()
  const input = host!.querySelector<HTMLSelectElement>('[data-library-input-type]')!, output = host!.querySelector<HTMLSelectElement>('[data-library-output-type]')!
  input.value = 'llm_connection'; input.dispatchEvent(new Event('change', { bubbles: true }))
  output.value = 'llm_model'; output.dispatchEvent(new Event('change', { bubbles: true })); await query('model_name')
  expect([...host!.querySelectorAll<HTMLElement>('[data-add-kind]')].map(row => row.dataset.addKind)).toEqual(['llm_model'])
  host!.querySelector<HTMLButtonElement>('.node-library-categories button')!.click(); input.value = 'string'; input.dispatchEvent(new Event('change', { bubbles: true })); output.value = 'string'; output.dispatchEvent(new Event('change', { bubbles: true })); await query('text_join')
  expect(host!.querySelector('[data-add-kind=text_join]')).not.toBeNull()
  expect(host!.querySelector('[data-node-library-preview]')!.textContent).toContain('文本 1')
})
it('searches field and port metadata in both languages and preserves filters across a locale change', async () => {
  const { search, query } = await mount()
  await query('附加参数'); expect(host!.querySelector('[data-add-kind=llm_model]')).not.toBeNull()
  await query('Additional parameters'); expect(host!.querySelector('[data-add-kind=llm_model]')).not.toBeNull()
  await query('Text 1'); expect(host!.querySelector('[data-add-kind=text_join]')).not.toBeNull()
  await query('asr_connection'); expect(host!.querySelector('[data-add-kind=asr_model]')).not.toBeNull()
  setLocale('en'); await settle(); expect(search.value).toBe('asr_connection')
})
it('allows unavailable nodes to be selected and inspected while preventing Enter and click adds', async () => {
  const { add, search, query } = await mount({ disabled: 'Node limit reached' })
  await query('llm_model'); const row = host!.querySelector<HTMLElement>('[data-add-kind=llm_model]')!
  expect(row.getAttribute('aria-disabled')).toBe('true'); row.click()
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  row.querySelector<HTMLButtonElement>('[data-library-item-details]')!.click(); await settle()
  expect(host!.querySelector('[data-node-library-preview]')!.textContent).toContain('llm_model'); expect(add).not.toHaveBeenCalled()
})
it('protects IME Enter and Escape, navigates with arrows, and adds only on a confirmed Enter', async () => {
  const { add, close, search, query } = await mount(); await query('llm_model')
  search.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
  for (const key of ['Enter', 'Escape']) search.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
  expect(add).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled()
  search.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }))
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true })); expect(add).not.toHaveBeenCalled()
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); await settle(); expect(document.activeElement).toBe(search)
  const selected = host!.querySelector<HTMLElement>('[role=option][aria-selected=true]')!.dataset.addKind
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); expect(add).toHaveBeenCalledOnce(); expect(add).toHaveBeenCalledWith(selected)
})
it('keeps narrow hidden panes out of the focus trap and restores the detail trigger without losing search or scroll', async () => {
  const { search, query } = await mount({ narrow: true }); await query('model')
  const results = host!.querySelector<HTMLElement>('.node-library-results')!; results.scrollTop = 85
  const trigger = host!.querySelector<HTMLButtonElement>('[data-add-kind=asr_model] [data-library-item-details]')!
  trigger.click(); await settle()
  const pane = host!.querySelector<HTMLElement>('.node-library-results-pane')!, back = host!.querySelector<HTMLButtonElement>('.node-library-details-back')!
  expect(pane.hidden).toBe(true); expect(pane.hasAttribute('inert')).toBe(true); expect(document.activeElement).toBe(back)
  const addButton = host!.querySelector<HTMLButtonElement>('[data-library-add-selected]')!; addButton.focus()
  const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }); addButton.dispatchEvent(tab); await settle()
  expect(document.activeElement).toBe(host!.querySelector('.canvas-dialog-close')); expect(tab.defaultPrevented).toBe(true)
  back.click(); await settle(); expect(pane.hidden).toBe(false); expect(document.activeElement).toBe(trigger)
  expect(search.value).toBe('model'); expect(results.scrollTop).toBe(85)
  expect(host!.querySelector<HTMLElement>('.node-library-details')!.hidden).toBe(true)
  trigger.click(); await settle(); await query('no-match-after-details')
  expect(host!.querySelector<HTMLElement>('.node-library-results-pane')!.hidden).toBe(true)
  expect(host!.querySelector('.node-library-details [role=status]')!.textContent).toBe('没有匹配的节点')

})
it('uses only sanitized catalog defaults, without requesting settings or exposing credential references or resource IDs', async () => {
  settingsCache.value = { schema_version: '4.0', revision: 1, global_fields: [], global: {}, node_defaults: { llm_service: { config: { endpoint: 'private-user-endpoint' } } as never } }
  const { add, fetcher, query } = await mount({ privateDefaults: true }); await query('llm_service')
  expect(host!.textContent).not.toContain('private-catalog-reference'); expect(host!.textContent).not.toContain('private-user-endpoint')
  expect(host!.querySelector('[data-node-library-preview]')!.textContent).not.toContain('credential_ref')
  await query('audio_input'); expect(host!.querySelector('[data-node-library-preview]')!.textContent).not.toContain('audio_id')
  expect(add).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled()
})

it('keeps search Home and End native, updates previews from details-button focus, and displays technical badges', async () => {
  const { search, add } = await mount()
  for (const key of ['Home', 'End']) {
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }); search.dispatchEvent(event); expect(event.defaultPrevented).toBe(false)
  }
  const row = host!.querySelector<HTMLElement>('[data-add-kind=asr_model]')!
  expect(row.querySelector('code')!.textContent).toBe('asr_model')
  row.querySelector<HTMLButtonElement>('[data-library-item-details]')!.focus(); await settle()
  expect(host!.querySelector('[data-node-library-preview] header code')!.textContent).toBe('asr_model'); expect(add).not.toHaveBeenCalled()
})
it('moves focus out of panes hidden by breakpoint changes and adds directly from narrow details', async () => {
  const { media, search, add } = await mount()
  const change = media.addEventListener.mock.calls.find(call => call[0] === 'change')![1] as (event: MediaQueryListEvent) => void
  const action = host!.querySelector<HTMLButtonElement>('[data-library-add-selected]')!; action.focus()
  change({ matches: true } as MediaQueryListEvent); await settle(); expect(document.activeElement).toBe(search)
  const row = host!.querySelector<HTMLElement>('[data-add-kind=llm_model]')!
  row.querySelector<HTMLButtonElement>('[data-library-item-details]')!.click(); await settle()
  host!.querySelector<HTMLButtonElement>('[data-library-add-selected]')!.click()
  expect(add).toHaveBeenCalledOnce(); expect(add).toHaveBeenCalledWith('llm_model')
})

it('finds an unknown null-category extension by the translated uncategorized category', async () => {
  const { query } = await mount({ extension: true })
  await query('Uncategorized'); expect(host!.querySelector('[data-add-kind=future_zebra]')).not.toBeNull()
  await query('未分类'); expect(host!.querySelector('[data-add-kind=future_zebra]')).not.toBeNull()
})
it('clears category and type selections when visible declarations no longer offer them', async () => {
  const { kinds, registry } = await mount()
  host!.querySelector<HTMLButtonElement>('[data-library-category=llm]')!.click()
  const input = host!.querySelector<HTMLSelectElement>('[data-library-input-type]')!, output = host!.querySelector<HTMLSelectElement>('[data-library-output-type]')!
  input.value = 'llm_connection'; input.dispatchEvent(new Event('change', { bubbles: true }))
  output.value = 'llm_model'; output.dispatchEvent(new Event('change', { bubbles: true })); await settle()
  kinds.value = registry.kinds.filter(kind => kind.category?.id !== 'llm'); await settle()
  expect(input.value).toBe(''); expect(output.value).toBe('')
  expect(host!.querySelector('.node-library-categories button')!.getAttribute('aria-pressed')).toBe('true')
  expect(host!.querySelectorAll('[data-add-kind]').length).toBeGreaterThan(0)
})
