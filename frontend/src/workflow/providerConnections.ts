import type { CapabilityNode } from './capabilities'

export function inlineConnectionKeys(kind: CapabilityNode | null | undefined) {
  const inline = kind?.inlineConnectionInput
  return inline ? [inline.baseUrlKey, inline.timeoutKey, inline.regionKey, inline.credentialRefKey].filter((key): key is string => key !== null) : []
}
export function connectionTarget(value: unknown) {
  try {
    const url = new URL(String(value || ''))
    return ['ws:', 'wss:'].includes(url.protocol) ? `${url.protocol}//${url.host}${url.pathname}` : ['http:', 'https:'].includes(url.protocol) ? url.host : ''
  } catch { return '' }
}
