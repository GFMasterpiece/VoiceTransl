import { fieldValueErrors } from './fieldValidation'
import { t, hasMessage, i18n, type MessageParams } from '../i18n'
import type { Capabilities, CapabilityNode, CapabilityPort } from './capabilities'

export type PortName = string
export type NodeType = string

export interface NodeRegistryIssue {
  field?: string
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
  outputPortsForNode(type: string): readonly PortName[]
  inputPortsForNode(type: string, config?: Record<string, unknown>): readonly PortName[]
  fieldsForNode(type: string, config?: Record<string, unknown>): readonly CapabilityNode['fields'][number][]
  outputPortForNode(type: string): PortName | null
  inputPortForNode(type: string, config?: Record<string, unknown>): PortName | null
  port(id: string, node?: { type: string; config: Record<string, unknown> }): CapabilityPort | null
  portsCompatible(sourceId: string, targetId: string, target?: { type: string; config: Record<string, unknown> }): boolean
  isTerminalNode(type: string): boolean
  label(type: string): string
  createDefaultConfig(type: string, creating?: boolean): Record<string, unknown>
  validateConfig(type: string, config: Record<string, unknown>): NodeRegistryIssue[]
}

function issueText(messageKey: string, messageParams?: MessageParams) {
  return { message: t(messageKey, messageParams), messageKey, messageParams }
}

function cloneDefault(value: unknown): unknown {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value))
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
      get label() { return present(`workflow.field_${entry.type}_${field.key}`, present(`workflow.field_${entry.configKind}_${field.key}`, field.label)) },
      get description() { return present(`workflow.description_${entry.configKind}_${field.key}`, field.description) },
      options: field.options.map((option) => ({
        ...option,
        get label() { return present(`workflow.option_${entry.configKind}_${field.key}_${option.value}`, option.label) },
      })),
    })),
    positionX: entry.positionX ?? 96 + index * 190,
  }))
  const byType = new Map(nodes.map((entry) => [entry.type, entry]))
  const byPort = new Map(capabilities.ports.map((entry) => [entry.id, { ...entry, get label() { const key = `workflow.port_${entry.id}`; return hasMessage(key) && [entry.id, entry.typeId, i18n.global.t(key, {}, { locale: 'zh-CN' }), i18n.global.t(key, {}, { locale: 'en' })].includes(entry.label) ? t(key) : entry.label } }]))
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
  const slots = (type: string, config: Record<string, unknown> = {}) => {
    const d = byType.get(type)?.dynamicInputs
    const value = d ? config[d.configKey] : null
    return Array.isArray(value) ? value.filter((item): item is { id: string; value: string } => !!item && typeof item === 'object' && typeof item.id === 'string' && /^[a-z][a-z0-9_]{0,31}$/.test(item.id)) : []
  }
  const resolvePort = (id: string, node?: { type: string; config: Record<string, unknown> }): CapabilityPort | null => {
    const d = node && byType.get(node.type)?.dynamicInputs
    return d && slots(node!.type, node!.config).some((slot) => d.portPrefix + slot.id === id)
      ? { id, typeId: d.typeId, label: id, capabilityRequirements: [] } : byPort.get(id) || null
  }
  const registry: NodeRegistry = {
    capabilities,
    kinds: nodes,
    kind: (type) => byType.get(type) || null,
    has: (type) => byType.has(type),
    outputPortsForNode: (type) => byType.get(type)?.outputPorts || [],
    inputPortsForNode: (type, config) => [...(byType.get(type)?.inputPorts || []), ...slots(type, config).map((slot) => byType.get(type)!.dynamicInputs!.portPrefix + slot.id)],
    fieldsForNode: (type, config) => [...(byType.get(type)?.fields || []), ...slots(type, config).map((slot, index) => {
      const d = byType.get(type)!.dynamicInputs!
      return { ...d.field, key: `${d.configKey}.${slot.id}.value`, inputPort: d.portPrefix + slot.id, label: t('workflow.dynamicInputLabel', { number: index + 1 }) }
    })],
    outputPortForNode: (type) => { const ports = byType.get(type)?.outputPorts; return ports?.length === 1 ? ports[0]! : null },
    inputPortForNode: (type, config) => { const ports = registry.inputPortsForNode(type, config); return ports.length === 1 ? ports[0]! : null },
    port: resolvePort,
    portsCompatible: (sourceId, targetId, node) => {
      const source = byPort.get(sourceId)
      const target = resolvePort(targetId, node)
      const sourceType = source ? byDataType.get(source.typeId) : null
      return !!source && !!target && !!sourceType
        && typeCompatible(source.typeId, target.typeId)
        && (target.capabilityRequirements || []).every((item) => sourceType.capabilities.includes(item))
    },
    isTerminalNode: (type) => byType.get(type)?.terminal === true,
    label: (type) => byType.get(type)?.label || type,
    createDefaultConfig: (type, creating = false) => Object.assign(byType.get(type)?.dynamicInputs ? { [byType.get(type)!.dynamicInputs!.configKey]: cloneDefault(byType.get(type)!.dynamicInputs!.defaultItems) } : {}, Object.fromEntries((byType.get(type)?.fields || [])
      .map((field) => ({ field, value: creating ? field.creationDefault ?? field.default : field.default }))
      .filter(({ field, value }) => value !== null || field.required)
      .map(({ field, value }) => [field.key, cloneDefault(value)]))),
    validateConfig: (type, config) => {
      const definition = byType.get(type)
      if (!definition) return [{ code: 'UNKNOWN_NODE_TYPE', ...issueText('workflow.unknownType', { p0: type }) }]
      const issues: NodeRegistryIssue[] = []
      const allowed = new Set([...definition.fields.map((field) => field.key), ...(definition.dynamicInputs ? [definition.dynamicInputs.configKey] : [])])
      const unexpected = Object.keys(config || {}).filter((key) => !allowed.has(key))
      if (unexpected.length) issues.push({ code: 'INVALID_NODE_CONFIG', ...issueText('workflow.unknownFields', { p0: unexpected.join(', ') }) })
      const d = definition.dynamicInputs
      if (d) {
        const items = config[d.configKey]
        if (!Array.isArray(items) || items.length < d.minItems || items.length > d.maxItems || new Set(slots(type, config).map((slot) => slot.id)).size !== items.length || items.some((slot) => !slot || typeof slot !== 'object' || Object.keys(slot).some((key) => !['id', 'value'].includes(key)) || !Object.prototype.hasOwnProperty.call(slot, 'value'))) issues.push({ field: d.configKey, code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldType', { p0: d.configKey }) })
      }
      for (const field of registry.fieldsForNode(type, config)) {
        const value = d && field.key.startsWith(`${d.configKey}.`) ? slots(type, config).find((slot) => slot.id === field.key.split('.')[1])?.value : config?.[field.key]
        if (value === null && field.nullable) continue
        if (value === undefined) {
          if (field.required) issues.push({ field: field.key, code: 'INVALID_NODE_CONFIG', ...issueText('workflow.requiredField', { p0: field.key }) })
          continue
        }
        const resource = definition.resourceInput
        const collection = resource?.collectionKey === field.key
        if (collection && value !== undefined) {
          if (!Array.isArray(value) || value.some((id) => typeof id !== 'string' || !id) || new Set(value).size !== value.length) issues.push({ field: field.key, code: 'INVALID_NODE_CONFIG', ...issueText('workflow.fieldType', { p0: field.key }) })
          if (config[resource!.configKey] !== undefined) issues.push({ field: field.key, code: 'INVALID_NODE_CONFIG', ...issueText('workflow.unknownFields', { p0: resource!.configKey }) })
          continue
        }
        issues.push(...fieldValueErrors(field, value).map((key) => ({ field: field.key, code: 'INVALID_NODE_CONFIG', ...issueText(`workflow.${key}`, { p0: field.key }) })))
      }
      return issues
    },
  }
  return registry
}
