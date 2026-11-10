import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, reactive, nextTick, type App } from 'vue'
import NodeConfigField from './components/NodeConfigField.vue'
import catalog from './workflow/modularCapabilities.fixture.json'
import { readCapabilities } from './workflow/capabilities'
import { createNodeRegistry } from './workflow/nodeRegistry'

let app: App | undefined, root: HTMLDivElement | undefined
const field = () => createNodeRegistry(readCapabilities(structuredClone(catalog))).kind('llm_translate')!.fields[0]!
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks() })
function mount(overrides: Record<string, unknown> = {}) {
  const start = vi.fn(), end = vi.fn(), cancel = vi.fn(), edit = vi.fn()
  const props = reactive({ field: field(), text: 'retained fallback', disabled: false, hidden: false, connectable: true, socketDisabled: false, ...overrides })
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(NodeConfigField, { ...props, onSocketStart: start, onSocketEnd: end, onSocketCancel: cancel, onEdit: edit }) }); app.mount(root)
  return { props, start, end, cancel, edit }
}
it.each(['string', 'select', 'boolean', 'integer', 'number', 'json'] as const)('renders a declared %s parameter socket in its field row', type => {
  mount({ field: { ...field(), key: 'custom', inputPort: 'extension_parameter', type, widget: null } })
  const socket = root!.querySelector<HTMLButtonElement>('.node-field-socket')!
  expect(socket.dataset.portId).toBe('extension_parameter'); expect(socket.closest('label')).toBeNull()
  expect(socket.closest('.node-config-row')!.querySelector('.node-config-label')).not.toBeNull()
  expect(root!.querySelector('.node-port-label-in')).toBeNull()
})
it('requires an explicit node scene and declaration, keeping global settings free of sockets', () => {
  mount({ connectable: false }); expect(root!.querySelector('.node-field-socket')).toBeNull()
  app!.unmount(); mount({ field: { ...field(), inputPort: null } }); expect(root!.querySelector('.node-field-socket')).toBeNull()
})
it('keeps a connected row and active socket while retaining its hidden control and fallback', async () => {
  const { props, start, end } = mount({ disabled: true, hidden: true })
  const row = root!.querySelector<HTMLElement>('.node-config-row')!, socket = root!.querySelector<HTMLButtonElement>('.node-field-socket')!, control = root!.querySelector<HTMLTextAreaElement>('textarea')!
  expect(row.style.display).not.toBe('none'); expect(row.classList.contains('is-connected')).toBe(true)
  expect(root!.querySelector<HTMLElement>('.node-field-control')!.style.display).toBe('none')
  expect(control.value).toBe('retained fallback'); expect(socket.disabled).toBe(false)
  socket.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true })); socket.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }))
  expect(start).toHaveBeenCalledOnce(); expect(end).toHaveBeenCalledOnce()
  props.hidden = false; props.disabled = false; await nextTick()
  expect(root!.querySelector('textarea')).toBe(control); expect(control.value).toBe('retained fallback')
  expect(root!.querySelector<HTMLLabelElement>('label')!.htmlFor).toBe(control.id)
})
it('cancels from Escape, keeps the node context menu, and locks only the socket when editing is locked', async () => {
  const { props, cancel } = mount(); const context = vi.fn(); root!.addEventListener('contextmenu', context)
  const socket = root!.querySelector<HTMLButtonElement>('.node-field-socket')!
  socket.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); expect(cancel).toHaveBeenCalledOnce()
  socket.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true })); expect(context).toHaveBeenCalledOnce()
  props.socketDisabled = true; await nextTick(); expect(socket.disabled).toBe(true)
})
