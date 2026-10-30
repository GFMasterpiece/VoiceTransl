import type { Capabilities, CapabilityNode } from './capabilities'

export type PortName = string
export type NodeType = string

export interface NodeRegistryIssue {
  code: string
  message: string
}
export interface NodeRegistry {
  capabilities: Capabilities
  kinds: readonly CapabilityNode[]
  kind(type: string): CapabilityNode | null
  has(type: string): boolean
  outputPortForNode(type: string): PortName | null
  inputPortForNode(type: string): PortName | null
  isTerminalNode(type: string): boolean
  label(type: string): string
  createDefaultConfig(type: string): Record<string, unknown>
  validateConfig(type: string, config: Record<string, unknown>): NodeRegistryIssue[]
}

function cloneDefault(value: unknown): unknown {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value))
}

function valueMatchesType(item: CapabilityNode['fields'][number], value: unknown): boolean {
  if (value === null) return false
  if (value === undefined) return !item.required
  if (item.type === 'boolean') return typeof value === 'boolean'
  if (item.type === 'integer') return typeof value === 'number' && Number.isInteger(value)
  if (item.type === 'number') return typeof value === 'number' && Number.isFinite(value)
  if (item.type === 'string' || item.type === 'select') return typeof value === 'string'
  if (item.type === 'json') return typeof value === 'object'
  return false
}

export function createNodeRegistry(capabilities: Capabilities): NodeRegistry {
  const nodes = capabilities.nodes.map((entry, index) => ({ ...entry, positionX: entry.positionX ?? 96 + index * 190 }))
  const byType = new Map(nodes.map((entry) => [entry.type, entry]))
  const registry: NodeRegistry = {
    capabilities,
    kinds: nodes,
    kind: (type) => byType.get(type) || null,
    has: (type) => byType.has(type),
    outputPortForNode: (type) => byType.get(type)?.outputPorts[0] || null,
    inputPortForNode: (type) => byType.get(type)?.inputPorts[0] || null,
    isTerminalNode: (type) => byType.get(type)?.terminal === true,
    label: (type) => byType.get(type)?.label || type,
    createDefaultConfig: (type) => Object.fromEntries((byType.get(type)?.fields || [])
      .filter((field) => field.default !== null || field.required)
      .map((field) => [field.key, cloneDefault(field.default)])),
    validateConfig: (type, config) => {
      const definition = byType.get(type)
      if (!definition) return [{ code: 'UNKNOWN_NODE_TYPE', message: `不支持的节点类型：${type}` }]
      const issues: NodeRegistryIssue[] = []
      const allowed = new Set(definition.fields.map((field) => field.key))
      const unexpected = Object.keys(config || {}).filter((key) => !allowed.has(key))
      if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', message: `节点配置包含未知字段：${unexpected.join(', ')}` })
      for (const field of definition.fields) {
        const value = config?.[field.key]
        if (value === undefined) {
          if (field.required) issues.push({ code: 'INVALID_NODE_CONFIG', message: `缺少必填字段：${field.key}` })
          continue
        }
        if (!valueMatchesType(field, value)) {
          issues.push({ code: 'INVALID_NODE_CONFIG', message: `字段 ${field.key} 类型无效` })
          continue
        }
        if ((field.type === 'integer' || field.type === 'number') && typeof value === 'number') {
          if (field.minimum !== null && value < field.minimum) issues.push({ code: 'INVALID_NODE_CONFIG', message: `字段 ${field.key} 小于最小值` })
          if (field.maximum !== null && value > field.maximum) issues.push({ code: 'INVALID_NODE_CONFIG', message: `字段 ${field.key} 大于最大值` })
        }
        if (field.type === 'select' && !field.options.some((option) => option.value === value)) {
          issues.push({ code: 'INVALID_NODE_CONFIG', message: `字段 ${field.key} 不是可用选项` })
        }
      }
      return issues
    },
  }
  return registry
}
