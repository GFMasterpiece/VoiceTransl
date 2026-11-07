import type { CapabilityNode } from './capabilities'

export function inlineConnectionActive(kind: CapabilityNode | null | undefined, config: Record<string, unknown>) {
  const inline = kind?.inlineConnectionInput
  return !!inline && config[inline.modeKey] === inline.modeValue
}
export function inlineConnectionKeys(kind: CapabilityNode | null | undefined) {
  const inline = kind?.inlineConnectionInput
  return inline ? [inline.modeKey, inline.baseUrlKey, inline.timeoutKey, inline.regionKey, inline.apiKeyEnvKey, inline.credentialRefKey] : []
}
