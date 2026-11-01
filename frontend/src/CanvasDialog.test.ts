import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import CanvasDialog from './components/CanvasDialog.vue'

const inertDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'inert')
// jsdom lacks native inert behavior; reflect the browser property to its attribute.
beforeAll(() => {
  if (!inertDescriptor) Object.defineProperty(HTMLElement.prototype, 'inert', {
    configurable: true,
    get(this: HTMLElement) { return this.hasAttribute('inert') },
    set(this: HTMLElement, value: boolean) { this.toggleAttribute('inert', !!value) },
  })
})
afterAll(() => {
  if (!inertDescriptor) Reflect.deleteProperty(HTMLElement.prototype, 'inert')
})

let app: App | undefined
let fixture: HTMLDivElement | undefined
async function settle() { await nextTick(); await nextTick(); await nextTick() }
function mount(options: { open?: boolean; autofocus?: boolean } = {}) {
  fixture = document.createElement('div')
  const trigger = document.createElement('button'); trigger.textContent = 'Open panel'
  const existingInert = document.createElement('aside'); existingInert.inert = true
  const host = document.createElement('div')
  fixture.append(trigger, existingInert, host); document.body.append(fixture)
  trigger.focus()
  const props = reactive<{ open?: boolean }>({ open: options.open })
  const close = vi.fn(() => { props.open = false })
  app = createApp({
    render: () => h('main', [
      h('button', { id: 'background-action' }, 'Background action'),
      h(CanvasDialog, { ...props, title: 'Run details', onClose: close }, {
        default: () => [
          h('input', { id: 'dialog-input', autofocus: options.autofocus || undefined }),
          h('button', { id: 'disabled-action', disabled: true }, 'Disabled'),
          h('button', { id: 'last-action' }, 'Last action'),
        ],
      }),
    ]),
  })
  app.mount(host)
  return {
    props, close, trigger, existingInert,
    background: host.querySelector<HTMLButtonElement>('#background-action')!,
    dialog: () => host.querySelector<HTMLElement>('[role="dialog"]')!,
    input: () => host.querySelector<HTMLInputElement>('#dialog-input')!,
    last: () => host.querySelector<HTMLButtonElement>('#last-action')!,
    first: () => host.querySelector<HTMLButtonElement>('.canvas-dialog-close')!,
    backdrop: () => host.querySelector<HTMLElement>('.canvas-dialog-backdrop')!,
  }
}
function key(key: string, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true })
  document.dispatchEvent(event)
  return event
}
afterEach(() => {
  app?.unmount(); app = undefined
  fixture?.remove(); fixture = undefined
  vi.restoreAllMocks()
})

describe('canvas dialog interaction lifecycle', () => {
  it('opens by default, names the dialog and focuses its first enabled control', async () => {
    const ui = mount(); await settle()
    expect(ui.dialog().getAttribute('aria-modal')).toBe('true')
    const titleId = ui.dialog().getAttribute('aria-labelledby')!
    expect(document.getElementById(titleId)?.textContent).toBe('Run details')
    expect(document.activeElement).toBe(ui.first())
    expect(ui.background.inert).toBe(true)
    expect(ui.trigger.inert).toBe(true)
    expect(ui.existingInert.inert).toBe(true)
    expect(ui.dialog().closest('[inert]')).toBeNull()
  })

  it('honors autofocus and traps Tab and Shift+Tab at the dialog boundaries', async () => {
    const ui = mount({ autofocus: true }); await settle()
    expect(document.activeElement).toBe(ui.input())
    ui.last().focus()
    expect(key('Tab').defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(ui.first())
    expect(key('Tab', true).defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(ui.last())
    ui.input().focus()
    expect(key('Tab').defaultPrevented).toBe(false)
    // Programmatic focus also stays inside the open modal.
    ui.background.focus()
    expect(document.activeElement).toBe(ui.input())
  })

  it('emits one close for Escape and restores prior inert states and trigger focus', async () => {
    const ui = mount(); await settle()
    expect(key('Escape').defaultPrevented).toBe(true)
    await settle()
    expect(ui.close).toHaveBeenCalledTimes(1)
    expect(ui.dialog()).toBeNull()
    expect(ui.background.inert).toBe(false)
    expect(ui.trigger.inert).toBe(false)
    expect(ui.existingInert.inert).toBe(true)
    expect(document.activeElement).toBe(ui.trigger)
    expect(key('Escape').defaultPrevented).toBe(false)
    expect(ui.close).toHaveBeenCalledTimes(1)
  })

  it('closes only when pointerdown lands on the backdrop itself', async () => {
    const ui = mount(); await settle()
    ui.input().dispatchEvent(new Event('pointerdown', { bubbles: true }))
    ui.dialog().dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await settle()
    expect(ui.close).not.toHaveBeenCalled()
    ui.backdrop().dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await settle()
    expect(ui.close).toHaveBeenCalledTimes(1)
    expect(ui.dialog()).toBeNull()
    expect(document.activeElement).toBe(ui.trigger)
  })

  it('removes document handlers and restores background state when unmounted while open', async () => {
    const added = vi.spyOn(document, 'addEventListener')
    const removed = vi.spyOn(document, 'removeEventListener')
    const ui = mount(); await settle()
    const handlers = added.mock.calls.filter(([type]) => type === 'keydown' || type === 'focusin')
    expect(handlers.map(([type]) => type).sort()).toEqual(['focusin', 'keydown'])
    app!.unmount(); app = undefined
    await settle()
    expect(ui.background.inert).toBe(false)
    expect(ui.trigger.inert).toBe(false)
    expect(ui.existingInert.inert).toBe(true)
    expect(document.activeElement).toBe(ui.trigger)
    for (const [type, handler, options] of handlers) expect(removed).toHaveBeenCalledWith(type, handler, options)
    expect(key('Escape').defaultPrevented).toBe(false)
    expect(ui.close).not.toHaveBeenCalled()
  })

  it('can repeatedly open and close without retaining modal listeners or inert backgrounds', async () => {
    const ui = mount({ open: false }); await settle()
    expect(ui.dialog()).toBeNull()
    expect(document.activeElement).toBe(ui.trigger)
    for (let cycle = 0; cycle < 3; cycle++) {
      ui.props.open = true; await settle()
      expect(ui.background.inert).toBe(true)
      expect(document.activeElement).toBe(ui.first())
      key('Escape'); await settle()
      expect(ui.close).toHaveBeenCalledTimes(cycle + 1)
      expect(ui.background.inert).toBe(false)
      expect(ui.trigger.inert).toBe(false)
      expect(ui.existingInert.inert).toBe(true)
      expect(document.activeElement).toBe(ui.trigger)
    }
    ui.background.focus()
    expect(document.activeElement).toBe(ui.background)
    expect(key('Tab').defaultPrevented).toBe(false)
    expect(key('Escape').defaultPrevented).toBe(false)
    expect(ui.close).toHaveBeenCalledTimes(3)
  })
})
