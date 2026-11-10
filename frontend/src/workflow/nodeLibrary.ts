import { hasMessage, i18n, supportedLocales, t } from '../i18n'
import type { CapabilityNode } from './capabilities'
import type { NodeRegistry } from './nodeRegistry'
import { nodeCategoryLabel } from './nodeCategories'
function names(key: string) { return supportedLocales.flatMap(locale => i18n.global.te(key, locale) ? [i18n.global.t(key, {}, { locale })] : []) }
export function nodeLibraryDefaults(kind: CapabilityNode, registry: NodeRegistry) {
  const config = registry.createDefaultConfig(kind.type, true)
  if (kind.inlineConnectionInput) delete config[kind.inlineConnectionInput.credentialRefKey]
  if (kind.resourceInput) { delete config[kind.resourceInput.configKey]; if (kind.resourceInput.collectionKey) delete config[kind.resourceInput.collectionKey] }
  return config
}
export function libraryTypeLabel(id: string, registry: NodeRegistry) {
  const key = `workflow.dataType_${id}`
  return hasMessage(key) ? t(key) : registry.capabilities.dataTypes.find(type => type.id === id)?.label || id
}
export function nodeLibrarySearch(kind: CapabilityNode, registry: NodeRegistry, query: string) {
  const config = nodeLibraryDefaults(kind, registry), node = { type: kind.type, config }
  const original = registry.capabilities.nodes.find(entry => entry.type === kind.type)
  const fields = registry.fieldsForNode(kind.type, config)
  const words = [kind.type, kind.label, kind.addLabel, original?.label || '', original?.addLabel || '', kind.category?.id || '', kind.category?.label || '', nodeCategoryLabel(kind.category), ...names(kind.category ? `workflow.category_${kind.category.id}` : 'workflow.categoryUncategorized'), ...names(`workflow.node_${kind.type}_label`), ...names(`workflow.node_${kind.type}_add`)]
  let dynamicIndex = 0
  for (const field of fields) {
    words.push(field.key, field.label, original?.fields.find(entry => entry.key === field.key)?.label || '', ...names(`workflow.field_${kind.type}_${field.key}`), ...names(`workflow.field_${kind.configKind}_${field.key}`))
    if (kind.dynamicInputs && field.key.startsWith(`${kind.dynamicInputs.configKey}.`)) {
      dynamicIndex++
      words.push(...supportedLocales.map(locale => i18n.global.t('workflow.dynamicInputLabel', { number: dynamicIndex }, { locale })))
    }
  }
  for (const id of [...registry.inputPortsForNode(kind.type, config), ...kind.outputPorts]) {
    const port = registry.port(id, node)
    words.push(id, port?.label || '', port?.typeId || '', ...names(`workflow.port_${id}`), ...names(`workflow.dataType_${port?.typeId}`))
  }
  return query.trim().toLocaleLowerCase().split(/\s+/).every(token => words.some(word => word.toLocaleLowerCase().includes(token)))
}
