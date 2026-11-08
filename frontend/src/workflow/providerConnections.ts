import type { CapabilityNode } from './capabilities'

export function inlineConnectionActive(kind: CapabilityNode | null | undefined, config: Record<string, unknown>) {
  const inline = kind?.inlineConnectionInput
  return !!inline && (inline.modeKey === null || config[inline.modeKey] === inline.modeValue)
}
export function inlineConnectionKeys(kind: CapabilityNode | null | undefined) {
  const inline = kind?.inlineConnectionInput
  return inline ? [inline.modeKey, inline.baseUrlKey, inline.timeoutKey, inline.regionKey, inline.apiKeyEnvKey, inline.credentialRefKey].filter((key): key is string => key !== null) : []
}
export function connectionDataHandling(kind: CapabilityNode | null | undefined, config: Record<string, unknown>) {
  if (inlineConnectionActive(kind, config) && kind?.inlineConnectionInput?.providerType !== undefined) return kind.inlineConnectionInput.dataHandling
  return kind?.providerProfileInput?.dataHandling
}
export function connectionTarget(value: unknown) {
  try {
    const url = new URL(String(value || ''))
    return ['ws:', 'wss:'].includes(url.protocol) ? `${url.protocol}//${url.host}${url.pathname}` : ['http:', 'https:'].includes(url.protocol) ? url.host : ''
  } catch { return '' }
}
