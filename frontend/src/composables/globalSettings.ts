import { shallowRef } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { message } from '../i18n'
import { parseCapabilityField, type CapabilityField } from '../workflow/capabilities'
export interface NodeDefaults { label: string; fields: CapabilityField[]; config: Record<string, unknown>; credential_configured: boolean; credential_version: string | null }
export interface GlobalSettings { schema_version: '2.0'; revision: number; global_fields: CapabilityField[]; global: Record<string, unknown>; node_defaults: Record<string, NodeDefaults> }
export const settingsCache = shallowRef<GlobalSettings | null>(null)
let cacheGeneration = 0
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
export function readSettings(value: unknown): GlobalSettings {
  const raw = value as GlobalSettings
  if (!raw || raw.schema_version !== '2.0' || (!Number.isInteger(raw.revision) || raw.revision < 0) || !Array.isArray(raw.global_fields) || !isRecord(raw.global) || !isRecord(raw.node_defaults)) throw message('settings.invalid')
  const fields = (items: unknown[]) => items.map((item) => parseCapabilityField(item))
  const node_defaults: Record<string, NodeDefaults> = {}
  for (const [type, node] of Object.entries(raw.node_defaults)) {
    if (!node || typeof node.label !== 'string' || !Array.isArray(node.fields) || !isRecord(node.config) || typeof node.credential_configured !== 'boolean' || (node.credential_version !== null && (typeof node.credential_version !== 'string' || !node.credential_version))) throw message('settings.invalid')
    const parsed = fields(node.fields)
    if (new Set(parsed.map(field => field.key)).size !== parsed.length || JSON.stringify(parsed.map(field => field.key).sort()) !== JSON.stringify(Object.keys(node.config).sort()) || node.credential_configured !== !!node.credential_version) throw message('settings.invalid')
    node_defaults[type] = { ...node, fields: parsed }
  }
  const globalFields = fields(raw.global_fields)
  if (new Set(globalFields.map(field => field.key)).size !== globalFields.length || JSON.stringify(globalFields.map(field => field.key).sort()) !== JSON.stringify(Object.keys(raw.global).sort())) throw message('settings.invalid')
  return { ...raw, global_fields: globalFields, node_defaults }
}
export async function loadSettings() {
  const generation = ++cacheGeneration
  settingsCache.value = null
  const response = await fetch(`${API_BASE_URL}/settings`)
  if (!response.ok) throw await parseApiError(response)
  const value = readSettings(await response.json()); if (generation === cacheGeneration) settingsCache.value = value; return value
}
export async function saveSettings(body: unknown) {
  const generation = ++cacheGeneration
  const response = await fetch(`${API_BASE_URL}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw await parseApiError(response)
  const value = readSettings(await response.json()); if (generation === cacheGeneration) settingsCache.value = value; return value
}
