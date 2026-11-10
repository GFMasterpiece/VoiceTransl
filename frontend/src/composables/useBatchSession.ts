import { computed, watch } from 'vue'
import { t } from '../i18n'
import type { Audio } from '../stores/projects'
import { useWorkflowStore } from '../stores/workflow'
import { useAudioCollection } from './useAudioCollection'
import { useBatchManifest } from './useBatchManifest'
import { baseRunReason, activeTargetBusy } from './workflowExecution'
import { useBatches } from './useBatches'
import type { CapabilityNode } from '../workflow/capabilities'
import type { ProjectMedia } from './useProjectMedia'

export function useBatchSession(projectId: string, uploaded: (audio: Audio) => void, _audios: () => readonly Audio[] = () => [], blocked: () => boolean = () => false, media?: ProjectMedia) {
  const store = useWorkflowStore(), batches = useBatches(projectId)
  const locked = computed(() => store.loading || store.saving || store.validating || store.actionLoading || store.resourceUploading || store.activeSingleRun || batches.submitting.value)
  const manifest = useBatchManifest(projectId, uploaded, () => locked.value)
  // Local Files belong to the project library, never to an arbitrary source node.
  const collection = useAudioCollection(manifest, _audios, projectId, () => store.registry?.capabilities.schemaVersion === '1.18')
  const sources = computed(() => store.draft.nodes.filter(node => store.registry?.kind(node.type)?.isSource))
  function neededIds(nodeId?: string) {
    if (!nodeId) return new Set(store.draft.nodes.map(node => node.id))
    const ids = new Set([nodeId]), pending = [nodeId]
    while (pending.length) { const id = pending.pop()!; for (const edge of store.draft.edges) if (edge.source === id && !ids.has(edge.target)) { ids.add(edge.target); pending.push(edge.target) } }
    let changed = true
    while (changed) { changed = false; for (const edge of store.draft.edges) if (ids.has(edge.target) && !ids.has(edge.source)) { ids.add(edge.source); changed = true } }
    return ids
  }
  function groupIds(nodeId?: string) {
    const needed = neededIds(nodeId), ids = new Set<string>()
    for (const node of store.draft.nodes) if (needed.has(node.id)) {
      const resource = store.registry?.kind(node.type)?.resourceInput
      const key = resource?.resourceType === 'audio_group' ? resource.configKey : resource?.groupKey
      const id = key ? node.config[key] : undefined
      if (typeof id === 'string' && id) ids.add(id)
    }
    return [...ids]
  }
  function reason(nodeId?: string) {
    if (store.projectId !== projectId || !store.document || !store.registry) return t('batch.invalidGraph')
    if (blocked() || manifest.uploading.value || batches.active.value || locked.value) return t('batch.busy')
    if (store.registry.capabilities.schemaVersion === '1.18') {
      if (collection.incomplete.value) return t('batch.unready')
      const base = baseRunReason(store)
      if (base) return base
      if (batches.pending.value) return t('batch.pendingRequest')
      const ids = store.boundAudioIds
      if (ids !== null) {
        if (!ids.length) return t('batch.empty')
        if (ids.some(id => !_audios().some(audio => audio.id === id))) return t('workflow.resourceMissing')
      } else if (!(store.boundAudioId || store.audioId)) return t('batch.empty')
      if (activeTargetBusy(store, store.boundAudioId || store.audioId)) return t('batch.busy')
      return ''
    }
    if (media?.dirty.value || media?.uncertain.value || manifest.pendingLocal.value) return t('media.groupUnsaved')
    if (store.dirty) return t('workflow.saveBeforeRun')
    if (batches.pending.value) return t('batch.pendingRequest')
    const needed = neededIds(nodeId)
    if (store.draft.nodes.some(node => needed.has(node.id) && store.registry!.validateConfig(node.type, node.config).length)) return t('batch.invalidGraph')
    if (store.validationIssues.some(issue => !issue.node_id || needed.has(issue.node_id))) return t('batch.invalidGraph')
    const chosen = nodeId ? sources.value.filter(node => node.id === nodeId) : sources.value
    if (!chosen.length) return t('batch.invalidGraph')
    for (const node of chosen) {
      const resource: CapabilityNode['resourceInput'] = store.registry.kind(node.type)?.resourceInput
      const key: string | null | undefined = resource?.groupKey
      if (key && Object.prototype.hasOwnProperty.call(node.config, key)) {
        const id: unknown = node.config[key]
        if (typeof id !== 'string' || !id) return t('media.emptyGroup')
        if (media?.groupErrors.value[id]) return media.groupErrors.value[id]
        if (!media?.groups.value[id]) return t('media.groupLoading')
        if (!media.groups.value[id].audio_ids.length) return t('media.emptyGroup')
      } else if (resource?.collectionKey && Array.isArray(node.config[resource.collectionKey])) {
        if (!(node.config[resource.collectionKey] as string[]).length) return t('batch.empty')
      } else if (!node.config[resource?.configKey || ''] && !store.audioId) return t('batch.empty')
    }
    for (const id of groupIds(nodeId)) {
      if (media?.groupErrors.value[id]) return media.groupErrors.value[id]
      if (!media?.groups.value[id]) return t('media.groupLoading')
    }
    return ''
  }
  const startReason = computed(() => reason())
  watch(batches.submitting, value => { store.batchSubmitting = value }, { flush: 'sync' })
  watch(batches.active, value => { store.batchRunning = value }, { flush: 'sync' })
  async function start(nodeId?: string) {
    if (reason(nodeId) || !store.document) return
    const revisions = Object.fromEntries(groupIds(nodeId).map(id => [id, media!.groups.value[id]!.revision]))
    if (store.registry?.capabilities.schemaVersion === '1.18') {
      const ids = store.boundAudioIds
      if (ids && ids.length > 1) return batches.create(store.document.workflow_revision, [...ids])
      return store.startRun()
    }
    try { return await batches.createTarget(store.document.workflow_revision, nodeId ? { kind:'source', node_id:nodeId } : { kind:'canvas' }, store.audioId, revisions) }
    catch { /* The batch session retains the actionable error and unconfirmed request. */ }
  }
  function dispose() { manifest.dispose(); batches.dispose(); store.batchSubmitting = false; store.batchRunning = false }
  return { manifest, collection, batches, locked, sources, groupIds, neededIds, startReason, reason, start, dispose }
}
