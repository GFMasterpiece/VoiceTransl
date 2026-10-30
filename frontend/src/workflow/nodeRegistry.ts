import { t, hasMessage, type MessageParams } from '../i18n'
import type { Capabilities, CapabilityNode, CapabilityPort } from './capabilities'

export type PortName = string
export type NodeType = string

export interface NodeRegistryIssue {
  code: string
  message: string
  messageKey?: string
  messageParams?: MessageParams
}
export interface NodeRegistry {
  capabilities: Capabilities
  kinds: readonly CapabilityNode[]
  kind(type: string): CapabilityNode | null
  has(type: string): boolean
  outputPortForNode(type: string): PortName | null
  inputPortForNode(type: string): PortName | null
  port(id: string): CapabilityPort | null
  portsCompatible(sourceId: string, targetId: string): boolean
  isTerminalNode(type: string): boolean
  label(type: string): string
  createDefaultConfig(type: string): Record<string, unknown>
  validateConfig(type: string, config: Record<string, unknown>): NodeRegistryIssue[]
}

function issueText(messageKey: string, messageParams?: MessageParams) {
  return { message: t(messageKey, messageParams), messageKey, messageParams }
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
  // Presentation is derived without mutating the server's capability contract.
  const present = (key: string, fallback: string) => hasMessage(key) ? t(key) : fallback
  const nodes = capabilities.nodes.map((entry, index) => ({
    ...entry,
    get label() { return present(`workflow.node_${entry.type}_label`, entry.label) },
    get addLabel() { return present(`workflow.node_${entry.type}_add`, entry.addLabel) },
    fields: entry.fields.map((field) => ({
      ...field,
      get label() { return present(`workflow.field_${entry.configKind}_${field.key}`, field.label) },
      options: field.options.map((option) => ({
        ...option,
        get label() { return present(`workflow.option_${entry.configKind}_${field.key}_${option.value}`, option.label) },
      })),
    })),
    positionX: entry.positionX ?? 96 + index * 190,
  }))
  const byType = new Map(nodes.map((entry) => [entry.type, entry]))
  const byPort = new Map(capabilities.ports.map((entry) => [entry.id, entry]))
  const byDataType = new Map(capabilities.dataTypes.map((entry) => [entry.id, entry]))
  const typeCompatible = (sourceTypeId: string, targetTypeId: string): boolean => {
    if (sourceTypeId === targetTypeId) return true
    const visited = new Set<string>()
    let current = byDataType.get(sourceTypeId) || undefined
    while (current && !visited.has(current.id)) {
      visited.add(current.id)
      if (current.parentTypeId === targetTypeId) return true
      current = current.parentTypeId ? byDataType.get(current.parentTypeId) || undefined : undefined
    }
    return false
  }
  const registry: NodeRegistry = {
    capabilities,
    kinds: nodes,
    kind: (type) => byType.get(type) || null,
    has: (type) => byType.has(type),
    outputPortForNode: (type) => byType.get(type)?.outputPorts[0] || null,
    inputPortForNode: (type) => byType.get(type)?.inputPorts[0] || null,
    port: (id) => byPort.get(id) || null,
    portsCompatible: (sourceId, targetId) => {
      const source = byPort.get(sourceId)
      const target = byPort.get(targetId)
      const sourceType = source ? byDataType.get(source.typeId) : null
      return !!source && !!target && !!sourceType
        && typeCompatible(source.typeId, target.typeId)
        && (target.capabilityRequirements || []).every((item) => sourceType.capabilities.includes(item))
    },
    isTerminalNode: (type) => byType.get(type)?.terminal === true,
    label: (type) => byType.get(type)?.label || type,
    createDefaultConfig: (type) => Object.fromEntries((byType.get(type)?.fields || [])
      .filter((field) => field.default !== null || field.required)
      .map((field) => [field.key, cloneDefault(field.default)])),
    validateConfig: (type, config) => {
      const definition = byType.get(type)
      if (!definition) return [{ code: 'UNKNOWN_NODE_TYPE', ...issueText('workflow.unknownType', { p0: type }) }]
      const issues: NodeRegistryIssue[] = []
      const allowed = new Set(definition.fields.map((field) => field.key))
      const unexpected = Object.keys(config || {}).filter((key) => !allowed.has(key))
      if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.unknownFields', { p0: unexpected.join(', ') }) })
      for (const field of definition.fields) {
        const value = config?.[field.key]
        if (value === undefined) {
          if (field.required) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.requiredField', { p0: field.key }) })
          continue
        }
        if (!valueMatchesType(field, value)) {
          issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldType', { p0: field.key }) })
          continue
        }
        if ((field.type === 'integer' || field.type === 'number') && typeof value === 'number') {
          if (field.minimum !== null && value < field.minimum) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldMinimum', { p0: field.key }) })
          if (field.maximum !== null && value > field.maximum) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldMaximum', { p0: field.key }) })
        }
        if (field.type === 'select' && !field.options.some((option) => option.value === value)) {
          issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldOption', { p0: field.key }) })
        }
        if (typeof value === 'string') {
          if (field.minLength !== null && value.length < field.minLength) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldMinLength', { p0: field.key }) })
          if (field.maxLength !== null && value.length > field.maxLength) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldMaxLength', { p0: field.key }) })
          if (field.pattern && !(new RegExp(field.pattern).test(value))) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldPattern', { p0: field.key }) })
        }
      }
      return issues
    },
  }
  return registry
}
