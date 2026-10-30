import { API_BASE_URL, parseApiError } from '../api'
import { message } from '../i18n'

export type CapabilityPort = string
export type CapabilityValue = unknown

export interface CapabilityOption { value: string; label: string }
export interface CapabilityField {
  key: string
  type: 'string' | 'boolean' | 'integer' | 'number' | 'select' | 'json'
  required: boolean
  default: CapabilityValue
  minimum: number | null
  maximum: number | null
  options: readonly CapabilityOption[]
  label: string
}
export interface CapabilityNode {
  type: string
  label: string
  addLabel: string
  idPrefix: string
  inputPorts: readonly CapabilityPort[]
  outputPorts: readonly CapabilityPort[]
  terminal: boolean
  maxInstances: number | null
  requiredInbound: number
  removable: boolean
  configKind: string
  fields: readonly CapabilityField[]
  positionX: number | null
}
export interface CapabilityAsr {
  providers: readonly CapabilityOption[]
}
export interface CapabilityExport {
  formats: readonly CapabilityOption[]
  namingModes: readonly CapabilityOption[]
}
export interface Capabilities {
  schemaVersion: string
  nodeTypes: readonly string[]
  nodes: readonly CapabilityNode[]
  ports: readonly CapabilityPort[]
  asr: CapabilityAsr
  export: CapabilityExport
}

const safeId = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_.-]{0,63}$/.test(value)
const safePort = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_.-]{0,63}$/.test(value)
const text = (value: unknown, fallback = ''): string => typeof value === 'string' ? value : fallback

function object(value: unknown, key: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw message(key)
  return value as Record<string, unknown>
}
function fail(key: string): never { throw message(key) }
function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  return typeof value === 'number' && Number.isFinite(value) ? value : fail('workflow.capNumber')
}
function options(value: unknown, message: string): CapabilityOption[] {
  if (!Array.isArray(value)) fail(message)
  return value.map((entry) => {
    const item = object(entry, message)
    if (typeof item.id !== 'string' && typeof item.value !== 'string') fail(message)
    const id = String(item.id ?? item.value)
    if (!safeId(id)) fail(message)
    return { value: id, label: typeof item.label === 'string' && item.label.trim() ? item.label : id }
  })
}
function field(value: unknown): CapabilityField {
  const item = object(value, 'workflow.capField')
  if (typeof item.key !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(item.key)) fail('workflow.capFieldId')
  const type = item.type
  if (!['string', 'boolean', 'integer', 'number', 'select', 'json'].includes(String(type))) fail('workflow.capFieldType')
  const defaultValue = item.default === undefined ? null : item.default as CapabilityValue
  const minimum = optionalNumber(item.minimum)
  const maximum = optionalNumber(item.maximum)
  if (minimum !== null && maximum !== null && minimum > maximum) fail('workflow.capFieldRange')
  return {
    key: item.key,
    type: type as CapabilityField['type'],
    required: item.required === true,
    default: defaultValue,
    minimum,
    maximum,
    options: item.options === undefined ? [] : options(item.options, 'workflow.capFieldOptions'),
    label: typeof item.label === 'string' && item.label.trim() ? item.label : item.key,
  }
}
function node(value: unknown): CapabilityNode {
  const item = object(value, 'workflow.capNode')
  if (!safeId(item.type) || typeof item.label !== 'string' || !item.label.trim()) fail('workflow.capNodeId')
  const inputPorts = item.input_ports === undefined ? [] : item.input_ports
  const outputPorts = item.output_ports === undefined ? [] : item.output_ports
  if (!Array.isArray(inputPorts) || !inputPorts.every(safePort) || !Array.isArray(outputPorts) || !outputPorts.every(safePort)) fail('workflow.capNodePorts')
  if (inputPorts.length > 1 || outputPorts.length > 1) fail('workflow.capPortLimit')
  const requiredInbound = item.required_inbound === undefined ? (inputPorts.length ? 1 : 0) : item.required_inbound
  if (typeof requiredInbound !== 'number' || !Number.isInteger(requiredInbound) || requiredInbound < 0 || requiredInbound > 1) fail('workflow.capInbound')
  const maxInstances = item.max_instances === null || item.max_instances === undefined ? null : item.max_instances
  if (maxInstances !== null && (typeof maxInstances !== 'number' || !Number.isInteger(maxInstances) || maxInstances < 1)) fail('workflow.capInstances')
  const config = item.config === undefined ? {} : object(item.config, 'workflow.capConfig')
  const fields = config.fields === undefined ? [] : config.fields
  if (!Array.isArray(fields)) fail('workflow.capConfigFields')
  const parsedFields = fields.map(field)
  if (new Set(parsedFields.map((entry) => entry.key)).size !== parsedFields.length) fail('workflow.capDuplicateFields')
  return {
    type: item.type,
    label: item.label,
    addLabel: typeof item.add_label === 'string' && item.add_label.trim() ? item.add_label : item.label,
    idPrefix: typeof item.id_prefix === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(item.id_prefix) ? item.id_prefix : item.type.replace(/_node$/, ''),
    inputPorts,
    outputPorts,
    terminal: item.terminal === true,
    maxInstances,
    requiredInbound,
    removable: item.removable !== false,
    configKind: typeof config.kind === 'string' && config.kind.trim() ? config.kind : item.type,
    fields: parsedFields,
    positionX: optionalNumber(item.position_x),
  }
}
function asr(value: unknown): CapabilityAsr {
  const item = object(value, 'workflow.capAsrMissing')
  return {
    providers: item.providers === undefined ? [] : options(item.providers, 'workflow.capAsrProvider'),
  }
}

export function readCapabilities(value: unknown): Capabilities {
  const root = object(value, 'workflow.capIncomplete')
  if (root.schema_version !== '1.0') fail('workflow.capSchema')
  const workflow = object(root.workflow, 'workflow.capWorkflowMissing')
  if (workflow.schema_version !== undefined && workflow.schema_version !== '1.0') fail('workflow.capWorkflowSchema')
  if (!Array.isArray(workflow.nodes) || !workflow.nodes.length) fail('workflow.capNoNodes')
  const nodes = workflow.nodes.map(node)
  if (new Set(nodes.map((entry) => entry.type)).size !== nodes.length) fail('workflow.capDuplicateNodes')
  const ports = workflow.ports === undefined
    ? [...new Set(nodes.flatMap((entry) => [...entry.inputPorts, ...entry.outputPorts]))]
    : workflow.ports
  if (!Array.isArray(ports) || !ports.every(safePort) || new Set(ports).size !== ports.length) fail('workflow.capPorts')
  for (const entry of nodes) {
    for (const port of [...entry.inputPorts, ...entry.outputPorts]) if (!ports.includes(port)) fail('workflow.capUndeclaredPort')
  }
  const exported = root.export === undefined ? {} : object(root.export, 'workflow.capExport')
  return {
    schemaVersion: '1.0',
    nodeTypes: nodes.map((entry) => entry.type),
    nodes,
    ports,
    asr: asr(root.asr),
    export: {
      formats: exported.formats === undefined ? [] : options(exported.formats, 'workflow.capFormats'),
      namingModes: exported.naming_modes === undefined ? [] : options(exported.naming_modes, 'workflow.capNamingModes'),
    },
  }
}

export async function fetchCapabilities(signal?: AbortSignal): Promise<Capabilities> {
  let response: Response
  try { response = await fetch(`${API_BASE_URL}/capabilities`, { signal }) } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
    throw message('workflow.capNetwork')
  }
  if (!response.ok) throw await parseApiError(response)
  try { return readCapabilities(await response.json()) } catch (reason) {
    throw reason instanceof Error && !(reason instanceof SyntaxError) ? reason : message('workflow.capInvalid')
  }
}
