import { API_BASE_URL } from '../api'

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

function object(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(message)
  return value as Record<string, unknown>
}
function fail(message: string): never { throw new Error(message) }
function optionalNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  return typeof value === 'number' && Number.isFinite(value) ? value : fail('能力目录中的数值无效')
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
  const item = object(value, '能力目录中的节点字段无效')
  if (typeof item.key !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(item.key)) fail('能力目录中的节点字段标识无效')
  const type = item.type
  if (!['string', 'boolean', 'integer', 'number', 'select', 'json'].includes(String(type))) fail('能力目录中的节点字段类型无效')
  const defaultValue = item.default === undefined ? null : item.default as CapabilityValue
  const minimum = optionalNumber(item.minimum)
  const maximum = optionalNumber(item.maximum)
  if (minimum !== null && maximum !== null && minimum > maximum) fail('能力目录中的节点字段范围无效')
  return {
    key: item.key,
    type: type as CapabilityField['type'],
    required: item.required === true,
    default: defaultValue,
    minimum,
    maximum,
    options: item.options === undefined ? [] : options(item.options, '能力目录中的节点字段选项无效'),
    label: typeof item.label === 'string' && item.label.trim() ? item.label : item.key,
  }
}
function node(value: unknown): CapabilityNode {
  const item = object(value, '能力目录中的节点定义无效')
  if (!safeId(item.type) || typeof item.label !== 'string' || !item.label.trim()) fail('能力目录中的节点标识或名称无效')
  const inputPorts = item.input_ports === undefined ? [] : item.input_ports
  const outputPorts = item.output_ports === undefined ? [] : item.output_ports
  if (!Array.isArray(inputPorts) || !inputPorts.every(safePort) || !Array.isArray(outputPorts) || !outputPorts.every(safePort)) fail('能力目录中的节点端口无效')
  if (inputPorts.length > 1 || outputPorts.length > 1) fail('当前节点画布仅支持每侧一个端口')
  const requiredInbound = item.required_inbound === undefined ? (inputPorts.length ? 1 : 0) : item.required_inbound
  if (typeof requiredInbound !== 'number' || !Number.isInteger(requiredInbound) || requiredInbound < 0 || requiredInbound > 1) fail('能力目录中的节点入边约束无效')
  const maxInstances = item.max_instances === null || item.max_instances === undefined ? null : item.max_instances
  if (maxInstances !== null && (typeof maxInstances !== 'number' || !Number.isInteger(maxInstances) || maxInstances < 1)) fail('能力目录中的节点实例上限无效')
  const config = item.config === undefined ? {} : object(item.config, '能力目录中的节点配置定义无效')
  const fields = config.fields === undefined ? [] : config.fields
  if (!Array.isArray(fields)) fail('能力目录中的节点配置字段无效')
  const parsedFields = fields.map(field)
  if (new Set(parsedFields.map((entry) => entry.key)).size !== parsedFields.length) fail('能力目录中的节点配置字段重复')
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
  const item = object(value, '能力目录缺少 ASR 能力')
  return {
    providers: item.providers === undefined ? [] : options(item.providers, '能力目录中的 ASR Provider 无效'),
  }
}

export function readCapabilities(value: unknown): Capabilities {
  const root = object(value, '能力目录响应不完整，请检查后端版本后重试。')
  if (root.schema_version !== '1.0') fail('能力目录 schema_version 不受支持，请升级前端或后端后重试。')
  const workflow = object(root.workflow, '能力目录缺少工作流定义')
  if (workflow.schema_version !== undefined && workflow.schema_version !== '1.0') fail('能力目录 workflow.schema_version 不受支持，请升级前端或后端后重试。')
  if (!Array.isArray(workflow.nodes) || !workflow.nodes.length) fail('能力目录没有节点定义')
  const nodes = workflow.nodes.map(node)
  if (new Set(nodes.map((entry) => entry.type)).size !== nodes.length) fail('能力目录中的节点类型重复')
  const ports = workflow.ports === undefined
    ? [...new Set(nodes.flatMap((entry) => [...entry.inputPorts, ...entry.outputPorts]))]
    : workflow.ports
  if (!Array.isArray(ports) || !ports.every(safePort) || new Set(ports).size !== ports.length) fail('能力目录中的端口列表无效')
  for (const entry of nodes) {
    for (const port of [...entry.inputPorts, ...entry.outputPorts]) if (!ports.includes(port)) fail('能力目录中的节点端口未声明')
  }
  const exported = root.export === undefined ? {} : object(root.export, '能力目录中的导出能力无效')
  return {
    schemaVersion: '1.0',
    nodeTypes: nodes.map((entry) => entry.type),
    nodes,
    ports,
    asr: asr(root.asr),
    export: {
      formats: exported.formats === undefined ? [] : options(exported.formats, '能力目录中的导出格式无效'),
      namingModes: exported.naming_modes === undefined ? [] : options(exported.naming_modes, '能力目录中的导出命名模式无效'),
    },
  }
}

export async function fetchCapabilities(signal?: AbortSignal): Promise<Capabilities> {
  let response: Response
  try { response = await fetch(`${API_BASE_URL}/capabilities`, { signal }) } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') throw reason
    throw new Error('无法连接能力目录服务，请检查后端服务后重试。')
  }
  if (!response.ok) {
    let message = `能力目录读取失败（${response.status}），请检查后端版本后重试。`
    try {
      const payload = await response.json() as { error?: { message?: unknown }; detail?: unknown }
      const detail = payload.detail as { error?: { message?: unknown } } | undefined
      const candidate = payload.error?.message || detail?.error?.message
      if (typeof candidate === 'string' && candidate.trim()) message = candidate
    } catch { /* keep status fallback */ }
    throw new Error(message)
  }
  try { return readCapabilities(await response.json()) } catch (reason) {
    throw reason instanceof Error ? reason : new Error('能力目录响应无效')
  }
}
