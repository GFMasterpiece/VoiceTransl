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
  it('measures an independent intrinsic clone and strips media and accessibility identities before mounting it', () => {
    fixture()
    const append = document.body.append.bind(document.body)
    vi.spyOn(document.body, 'append').mockImplementation((...elements) => {
      const host = elements[0] as HTMLElement
      expect(host.getAttribute('aria-hidden')).toBe('true')
      expect(host.inert).toBe(true)
      expect(host.querySelector('[src], [id], [for], [aria-describedby]')).toBeNull()
      expect(host.querySelector('pre')?.textContent).toBe('')
      append(...elements)
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const rect = this.classList.contains('node-body') ? { width: 310, height: 250 }
        : this.classList.contains('node-config-fields') ? { width: 240, height: 80 }
          : this.classList.contains('node-body-preview') ? { width: 306, height: 120 } : { width: 0, height: 0 }
      return { ...rect, x: 0, y: 0, top: 0, left: 0, right: rect.width, bottom: rect.height, toJSON: () => ({}) }
    })
    const measured = measureNodeContent(group, fallback)
    expect(measured).toEqual({ width: 326, height: 272, fields: 80, preview: 120, resource: 0 })
    expect(measured.width).toBeLessThan(900)
    expect(document.querySelector('.node-measure-host')).toBeNull()
    expect(group.querySelector('audio')?.getAttribute('src')).toBe('https://example.invalid/audio')
  })

  it('allows minimum height to grow for errors and shrink again without accumulating custom user sizes', () => {
    fixture()
    let errorHeight = 120
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
      const width = this.classList.contains('node-body') ? 250 : 0
      const height = this.classList.contains('node-config-fields') ? errorHeight : 0
      return { width, height, x: 0, y: 0, top: 0, left: 0, right: width, bottom: height, toJSON: () => ({}) }
    })
    expect(measureNodeContent(group, fallback).height).toBe(192)
    errorHeight = 44
    expect(measureNodeContent(group, fallback).height).toBe(116)
    expect(group.querySelector<HTMLElement>('.node-body')?.style.height).toBe('800px')
  })
})
