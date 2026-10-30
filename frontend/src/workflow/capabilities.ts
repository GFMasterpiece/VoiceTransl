import { API_BASE_URL, parseApiError } from '../api'
import { message } from '../i18n'

export interface CapabilityPort {
  id: string
  typeId: string
  label: string
  capabilityRequirements: readonly string[]
}
export interface CapabilityDataType {
  id: string
  label: string
  parentTypeId: string | null
  capabilities: readonly string[]
}
export type CapabilityValue = unknown

export interface CapabilityOption { value: string; label: string }
export interface CapabilityField {
  key: string
  type: 'string' | 'boolean' | 'integer' | 'number' | 'select' | 'json'
  required: boolean
  default: CapabilityValue
  minimum: number | null
  maximum: number | null
  minLength: number | null
  maxLength: number | null
  pattern: string | null
  validator: string | null
  options: readonly CapabilityOption[]
  label: string
  description: string
}
export interface CapabilityNode {
  type: string
  label: string
  addLabel: string
  idPrefix: string
  inputPorts: readonly string[]
  outputPorts: readonly string[]
  terminal: boolean
  isSource: boolean
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
  dataTypes: readonly CapabilityDataType[]
  constraints: {
    maxNodes: number
    maxEdges: number
    minSources: number
    maxSources: number
    minTerminals: number
  }
  defaultGraph: { nodes: Array<{ id: string; type: string; config: Record<string, unknown>; position: { x: number; y: number } }>; edges: Array<{ id: string; source: string; source_port: string; target: string; target_port: string }> }
  asr: CapabilityAsr
  export: CapabilityExport
}

const safeNodeType = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(value)
const safeIdentifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(value)
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
    if (!id.trim() || id.length > 256) fail(message)
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
  const minLength = optionalNumber(item.min_length)
  const maxLength = optionalNumber(item.max_length)
  if (minimum !== null && maximum !== null && minimum > maximum) fail('workflow.capFieldRange')
  if (minLength !== null && maxLength !== null && minLength > maxLength) fail('workflow.capFieldRange')
  const pattern = item.pattern === undefined || item.pattern === null ? null : text(item.pattern)
  if (pattern) {
    try { new RegExp(pattern) } catch { fail('workflow.capFieldPattern') }
  }
  return {
    key: item.key,
    type: type as CapabilityField['type'],
    required: item.required === true,
    default: defaultValue,
    minimum,
    maximum,
    minLength,
    maxLength,
    pattern,
    validator: typeof item.validator === 'string' ? item.validator : null,
    options: item.options === undefined ? [] : options(item.options, 'workflow.capFieldOptions'),
    label: typeof item.label === 'string' && item.label.trim() ? item.label : item.key,
    description: text(item.description),
  }
}
function port(value: unknown): CapabilityPort {
  if (typeof value === 'string') {
    if (!safePort(value)) fail('workflow.capPorts')
    return { id: value, typeId: value, label: value, capabilityRequirements: [] }
  }
  const item = object(value, 'workflow.port')
  const id = text(item.id)
  const typeId = text(item.type_id)
  const label = text(item.label)
  if (!id || !typeId || !label) fail('workflow.capPorts')
  if (!safePort(id) || !safePort(typeId)) fail('workflow.capPorts')
  const capabilityRequirements = item.capability_requirements === undefined ? [] : item.capability_requirements
  if (!Array.isArray(capabilityRequirements) || !capabilityRequirements.every((value) => typeof value === 'string' && value.trim())) fail('workflow.capPorts')
  return { id, typeId, label, capabilityRequirements }
}
function dataType(value: unknown): CapabilityDataType {
  const item = object(value, 'workflow.dataType')
  const id = text(item.id)
  const label = text(item.label)
  const parentTypeId = item.parent_type_id === null || item.parent_type_id === undefined ? null : text(item.parent_type_id)
  const capabilities = item.capabilities === undefined ? [] : item.capabilities
  if (!safePort(id) || !label || (parentTypeId !== null && !safePort(parentTypeId)) || !Array.isArray(capabilities) || !capabilities.every(safePort)) fail('workflow.capTypes')
  return { id, label, parentTypeId, capabilities }
}

function node(value: unknown): CapabilityNode {
  const item = object(value, 'workflow.capNode')
  if (!safeNodeType(item.type) || typeof item.label !== 'string' || !item.label.trim()) fail('workflow.capNodeId')
  const inputPorts = item.input_ports === undefined ? [] : item.input_ports
  const outputPorts = item.output_ports === undefined ? [] : item.output_ports
  if (!Array.isArray(inputPorts) || !inputPorts.every(safePort) || !Array.isArray(outputPorts) || !outputPorts.every(safePort)) fail('workflow.capNodePorts')
  // The current canvas renders one input and one output handle per node. The
  // backend registry enforces the same contract; relax this only with a
  // corresponding multi-handle canvas implementation.
  if (inputPorts.length > 1 || outputPorts.length > 1) fail('workflow.capPortLimit')
  const requiredInbound = item.required_inbound === undefined ? (inputPorts.length ? 1 : 0) : item.required_inbound
  // required_inbound is currently limited by the same single-input canvas
  // contract, not a second domain rule owned by the frontend.
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
    isSource: item.is_source === true,
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
  if (workflow.schema_version !== '1.2') fail('workflow.capWorkflowSchema')
  if (!Array.isArray(workflow.nodes) || !workflow.nodes.length) fail('workflow.capNoNodes')
  const nodes = workflow.nodes.map(node)
  if (new Set(nodes.map((entry) => entry.type)).size !== nodes.length) fail('workflow.capDuplicateNodes')
  const ports = workflow.ports === undefined
    ? [...new Set(nodes.flatMap((entry) => [...entry.inputPorts, ...entry.outputPorts]))].map((id) => ({ id, typeId: id, label: id, capabilityRequirements: [] as string[] }))
    : Array.isArray(workflow.ports) ? workflow.ports.map(port) : fail('workflow.capPorts')
  if (!Array.isArray(ports) || new Set(ports.map((item) => item.id)).size !== ports.length) fail('workflow.capPorts')
  const portIds = new Set(ports.map((item) => item.id))
  const dataTypes = workflow.data_types === undefined
    ? ports.map((item) => ({ id: item.typeId, label: item.label, parentTypeId: null, capabilities: [] as string[] }))
    : Array.isArray(workflow.data_types) ? workflow.data_types.map(dataType) : fail('workflow.capTypes')
  if (new Set(dataTypes.map((item) => item.id)).size !== dataTypes.length) fail('workflow.capTypes')
  const typeIds = new Set(dataTypes.map((item) => item.id))
  if (ports.some((item) => !typeIds.has(item.typeId))) fail('workflow.capTypes')
  for (const entry of nodes) {
    for (const port of [...entry.inputPorts, ...entry.outputPorts]) if (!portIds.has(port)) fail('workflow.capUndeclaredPort')
  }
  const rawConstraints = object(workflow.constraints, 'workflow.capConstraints')
  const constraints = {
    maxNodes: optionalNumber(rawConstraints.max_nodes),
    maxEdges: optionalNumber(rawConstraints.max_edges),
    minSources: optionalNumber(rawConstraints.min_sources),
    maxSources: optionalNumber(rawConstraints.max_sources),
    minTerminals: optionalNumber(rawConstraints.min_terminals),
  }
  if (Object.values(constraints).some((value) => value === null || !Number.isInteger(value) || value < 0)) fail('workflow.capConstraints')
  if (constraints.minSources !== null && constraints.maxSources !== null
    && constraints.minSources > constraints.maxSources) fail('workflow.capConstraints')
  const rawDefaultGraph = object(workflow.default_graph, 'workflow.capDefaultGraph')
  if (!Array.isArray(rawDefaultGraph.nodes) || !Array.isArray(rawDefaultGraph.edges)) fail('workflow.capDefaultGraph')
  const defaultGraph = {
    nodes: rawDefaultGraph.nodes.map((value) => {
      const item = object(value, 'workflow.capDefaultGraph')
      const position = object(item.position, 'workflow.capDefaultGraph')
      const config = object(item.config, 'workflow.capDefaultGraph')
      if (!safeIdentifier(item.id) || !safeNodeType(item.type) || typeof position.x !== 'number' || !Number.isFinite(position.x) || typeof position.y !== 'number' || !Number.isFinite(position.y)) fail('workflow.capDefaultGraph')
      return { id: item.id, type: item.type, config, position: { x: position.x, y: position.y } }
    }),
    edges: rawDefaultGraph.edges.map((value) => {
      const item = object(value, 'workflow.capDefaultGraph')
      if (!safeIdentifier(item.id) || !safeIdentifier(item.source) || !safePort(item.source_port) || !safeIdentifier(item.target) || !safePort(item.target_port)) fail('workflow.capDefaultGraph')
      return { id: item.id, source: item.source, source_port: item.source_port, target: item.target, target_port: item.target_port }
    }),
  }
  const exported = root.export === undefined ? {} : object(root.export, 'workflow.capExport')
  return {
    schemaVersion: '1.2',
    nodeTypes: nodes.map((entry) => entry.type),
    nodes,
    ports,
    dataTypes,
    constraints: constraints as Capabilities['constraints'],
    defaultGraph,
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
