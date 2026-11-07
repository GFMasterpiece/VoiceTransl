import { afterEach, describe, expect, it, vi } from 'vitest'
import { measureNodeContent } from './measureNode'

let group: SVGGElement
const fallback = { width: 188, height: 116, fields: 44, resource: 0, preview: 0 }
function fixture() {
  group = document.createElementNS('http://www.w3.org/2000/svg', 'g')
  group.innerHTML = '<text class="node-name">Title</text><text class="node-port-label-in">left</text><text class="node-port-label-out">right</text><foreignObject><div xmlns="http://www.w3.org/1999/xhtml" class="node-body" style="width:900px;height:800px"><div class="node-body-fields"><div class="node-config-fields"><label id="field-id" for="duplicate">Label<input id="duplicate" aria-describedby="field-error" /></label></div></div><div class="node-body-preview"><div class="node-preview"><audio controls src="https://example.invalid/audio"><source src="https://example.invalid/stream" /></audio><pre>Document contents never determine the panel minimum.</pre></div></div></div></foreignObject>'
  document.body.append(group)
  for (const text of group.querySelectorAll('text')) Object.defineProperty(text, 'getComputedTextLength', { value: () => text.classList.contains('node-name') ? 160 : 60 })
}
afterEach(() => { group?.remove(); vi.restoreAllMocks() })

describe('node intrinsic content measurement', () => {
  it('accounts for every port label and places content below all port rows', () => {
    fixture()
    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text')
    label.classList.add('node-port-label-in')
    label.textContent = 'Long second input port'
    Object.defineProperty(label, 'getComputedTextLength', { value: () => 300 })
    group.append(label)
    const minimum = measureNodeContent(group, { ...fallback, bodyY: 134 })
    expect(minimum.width).toBe(628)
    expect(minimum.height).toBe(190)
    expect(minimum.bodyY).toBe(134)
  })

  it('measures an independent intrinsic clone and strips media and accessibility identities before mounting it', () => {
    fixture()
    const password = document.createElement('input'); password.type = 'password'; password.value = 'secret-measure-key'; password.setAttribute('value', 'secret-measure-key')
    group.querySelector('.node-config-fields')!.append(password)
    const append = document.body.append.bind(document.body)
    vi.spyOn(document.body, 'append').mockImplementation((...elements) => {
      const host = elements[0] as HTMLElement
      expect(host.getAttribute('aria-hidden')).toBe('true')
      expect(host.inert).toBe(true)
      expect(host.querySelector('[src], [id], [for], [aria-describedby]')).toBeNull()
      expect(host.querySelector('pre')?.textContent).toBe('')
      expect(host.querySelector<HTMLInputElement>('input[type=password]')!.value).toBe('')
      expect(host.innerHTML).not.toContain('secret-measure-key')
      append(...elements)
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const rect = this.classList.contains('node-body') ? { width: 310, height: 250 }
        : this.classList.contains('node-config-fields') ? { width: 240, height: 80 }
          : this.classList.contains('node-body-preview') ? { width: 306, height: 120 } : { width: 0, height: 0 }
      return { ...rect, x: 0, y: 0, top: 0, left: 0, right: rect.width, bottom: rect.height, toJSON: () => ({}) }
    })
    const measured = measureNodeContent(group, fallback)
    expect(measured).toEqual({ width: 224, height: 274, fields: 80, preview: 120, resource: 0 })
    expect(measured.width).toBeLessThan(900)
    expect(document.querySelector('.node-measure-host')).toBeNull()
    expect(group.querySelector('audio')?.getAttribute('src')).toBe('https://example.invalid/audio')
    expect(password.value).toBe('secret-measure-key')
  })

  it('allows minimum height to grow for errors and shrink again without accumulating custom user sizes', () => {
    fixture()
    let errorHeight = 120
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const width = this.classList.contains('node-body') ? 250 : 0
      const height = this.classList.contains('node-config-fields') ? errorHeight : 0
      return { width, height, x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, toJSON: () => ({}) }
    })
    expect(measureNodeContent(group, fallback).height).toBe(194)
    errorHeight = 44
    expect(measureNodeContent(group, fallback).height).toBe(118)
    expect(group.querySelector<HTMLElement>('.node-body')?.style.height).toBe('800px')
  })

  it('measures wrapping at the current world width while keeping the minimum width independent of user size and zoom', () => {
    fixture()
    const foreignObject = group.querySelector('foreignObject')!
    foreignObject.setAttribute('width', '584')
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const worldWidth = Number.parseFloat(this.closest<HTMLElement>('.node-measure-host')?.style.width || '0')
      const height = this.classList.contains('node-config-fields') ? Math.ceil(1200 / worldWidth) * 24 : 0
      return { width: worldWidth, height, x: 0, y: 0, top: 0, left: 0, right: worldWidth, bottom: height, toJSON: () => ({}) }
    })
    const wide = measureNodeContent(group, fallback)
    foreignObject.setAttribute('width', '224')
    const narrow = measureNodeContent(group, fallback)
    expect(wide.width).toBe(narrow.width)
    expect(wide.width).toBeLessThan(584)
    expect(narrow.height).toBeGreaterThan(wide.height)
    group.setAttribute('transform', 'scale(.1)')
    expect(measureNodeContent(group, fallback)).toEqual(narrow)
    expect(foreignObject.getAttribute('width')).toBe('224')
    expect(group.querySelector<HTMLElement>('.node-body')!.style.width).toBe('900px')
  })
})
