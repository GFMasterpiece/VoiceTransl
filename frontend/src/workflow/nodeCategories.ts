import { hasMessage, i18n, supportedLocales, t } from '../i18n'
import type { CapabilityNode } from './capabilities'

function translatedNames(key: string) {
  return supportedLocales.flatMap((locale) => i18n.global.te(key, locale) ? [i18n.global.t(key, {}, { locale })] : [])
}
export function isNodeVisible(node: Pick<CapabilityNode, 'debugOnly'> | undefined, debug: boolean) { return debug || !node?.debugOnly }
export function nodeCategoryLabel(category: CapabilityNode['category']) {
  const key = category ? `workflow.category_${category.id}` : 'workflow.categoryUncategorized'
  return hasMessage(key) ? t(key) : category?.label || t('workflow.categoryUncategorized')
}
export function groupNodeKinds(kinds: readonly CapabilityNode[], search: string) {
  const query = search.trim().toLocaleLowerCase()
  const groups = new Map<string | null, { id: string | null; label: string; nodes: CapabilityNode[] }>()
  for (const entry of kinds) {
    const category = entry.category
    const key = category ? `workflow.category_${category.id}` : 'workflow.categoryUncategorized'
    const label = nodeCategoryLabel(category)
    const matches = [entry.label, entry.addLabel, entry.type, category?.id || '', category?.label || '', label,
      ...translatedNames(key), ...translatedNames(`workflow.node_${entry.type}_label`), ...translatedNames(`workflow.node_${entry.type}_add`)]
    if (query && !matches.some((value) => value.toLocaleLowerCase().includes(query))) continue
    const id = category?.id ?? null
    if (!groups.has(id)) groups.set(id, { id, label, nodes: [] })
    groups.get(id)!.nodes.push(entry)
  }
  return [...groups.values()]
}
