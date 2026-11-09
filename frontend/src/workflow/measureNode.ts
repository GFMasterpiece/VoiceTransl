import { WORKFLOW_NODE_BODY_BOTTOM, WORKFLOW_NODE_FIELDS_Y, type WorkflowNodeSize } from '../stores/workflow'

export interface NodeContentMinimum extends WorkflowNodeSize {
  fields: number
  resource: number
  preview: number
  bodyY?: number
}
function textWidth(element: SVGTextElement | null): number {
  if (!element) return 0
  // SVG text metrics are already expressed in local/world units, even at .01 zoom.
  if (typeof element.getComputedTextLength === 'function') return element.getComputedTextLength()
  return 0
}

export function measureNodeContent(group: SVGGElement, fallback: NodeContentMinimum): NodeContentMinimum {
  const body = group.querySelector<HTMLElement>('.node-body')
  const widthOf = (selector: string) => Math.max(0, ...Array.from(group.querySelectorAll<SVGTextElement>(selector), textWidth))
  const inputWidth = widthOf('.node-port-label-in')
  const outputWidth = widthOf('.node-port-label-out')
  const portWidth = inputWidth && outputWidth ? Math.max(inputWidth, outputWidth) * 2 + 28 : Math.max(inputWidth, outputWidth) + 20
  const width = Math.ceil(Math.max(fallback.width, textWidth(group.querySelector('.node-name')) + 64, portWidth))
  // SVG attributes are world units. Screen rectangles would change with zoom.
  const foreignObject = group.querySelector('foreignObject')
  const declaredWidth = Number(foreignObject?.getAttribute('width'))
  const currentBodyWidth = Number.isFinite(declaredWidth) && declaredWidth > 0 ? declaredWidth : width - 16
  const host = document.createElement('div')
  host.className = 'node-measure-host'
  host.style.width = `${Math.max(width - 16, currentBodyWidth)}px`
  host.setAttribute('aria-hidden', 'true')
  host.inert = true
  let fields = fallback.fields
  let resource = fallback.resource
  let preview = fallback.preview
  try {
    if (body) {
      const clone = body.cloneNode(true) as HTMLElement
      // Strip media URLs and accessibility identities BEFORE connecting the clone.
      for (const element of [clone, ...clone.querySelectorAll<HTMLElement>('*')]) {
        for (const attr of ['src', 'srcset', 'poster', 'id', 'for', 'aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-activedescendant']) element.removeAttribute(attr)
      }
      // Document/JSON bodies scroll inside bounded controls and do not define UI minima.
      clone.querySelectorAll('pre').forEach((element) => { element.textContent = '' })
      clone.querySelectorAll('textarea').forEach((element) => { element.value = '' })
      clone.querySelectorAll<HTMLInputElement>('input[type="password"], input[data-credential-input]').forEach((element) => { element.value = ''; element.removeAttribute('value') })
      host.append(clone)
      document.body.append(host)
      const height = (selector: string, minimum: number) => Math.max(minimum, Math.ceil(clone.querySelector<HTMLElement>(selector)?.getBoundingClientRect().height || 0))
      fields = height('.node-config-fields', fields)
      resource = height('.node-body-resource', resource)
      preview = height('.node-body-preview', preview)
    }
    return {
      width,
      height: Math.ceil(Math.max(fallback.height, (fallback.bodyY ?? WORKFLOW_NODE_FIELDS_Y) + fields + resource + preview + WORKFLOW_NODE_BODY_BOTTOM)),
      fields, resource, preview, ...(fallback.bodyY === undefined ? {} : { bodyY: fallback.bodyY }),
    }
  } finally { host.remove() }
}
