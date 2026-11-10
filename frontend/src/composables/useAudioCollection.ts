import { computed, watch } from 'vue'
import type { Audio } from '../stores/projects'
import { useWorkflowStore } from '../stores/workflow'
import { randomRequestId, type BatchManifest, type ManifestItem } from './useBatchManifest'

/** The graph owns uploaded selection/order; this session additionally owns local Files. */
export function useAudioCollection(manifest: BatchManifest, audios: () => readonly Audio[], projectId: string, active: () => boolean = () => true) {
  const store = useWorkflowStore()
  const node = computed(() => active() && store.projectId === projectId && store.document?.project_id === projectId ? store.draft.nodes.find((node) => {
    const resource = store.registry?.kind(node.type)?.resourceInput
    return resource?.resourceType === 'audio' && resource.collectionKey
  }) : undefined)
  const resource = computed(() => node.value && store.registry?.kind(node.value.type)?.resourceInput)
  const mode = computed(() => node.value && resource.value?.collectionKey && Object.prototype.hasOwnProperty.call(node.value.config, resource.value.collectionKey)
    ? 'collection' : node.value && resource.value && node.value.config[resource.value.configKey] ? 'single' : 'context')
  let reconciling = false
  function reconcile() {
    if (manifest.uploading.value || !node.value || !resource.value?.collectionKey) return
    reconciling = true
    try {
      const ids = mode.value === 'collection' && Array.isArray(node.value.config[resource.value.collectionKey]) ? node.value.config[resource.value.collectionKey] as string[] : []
      const cached = manifest.items.value
      if (!manifest.recursive.value && ids.some((id) => (audios().find((audio) => audio.id === id)?.source.origin?.relative_path || cached.find((item) => item.audioId === id)?.origin?.relative_path)?.includes('/'))) manifest.recursive.value = true
      const selected: ManifestItem[] = ids.map((id) => {
        const previous = cached.find((item) => item.audioId === id)
        const audio = audios().find((audio) => audio.id === id)
        if (audio) return { ...previous, id: previous?.id || randomRequestId(), audioId: id, origin: audio.source.origin || null, name: audio.source.original_name,
          sizeBytes: audio.source.size_bytes, language: audio.source.language, selected: true, status: 'uploaded', missing: false }
        return previous ? { ...previous, selected: true } : { id: randomRequestId(), audioId: id, name: id, language: '', origin: null, sizeBytes: 0, selected: true, status: 'uploaded', missing: true }
      })
      manifest.items.value = [...selected, ...cached.filter((item) => !item.audioId || !ids.includes(item.audioId)).map((item) => item.audioId ? { ...item, selected: false } : item)]
    } finally { reconciling = false }
  }
  function synchronize() {
    if (reconciling || mode.value !== 'collection' || manifest.uploading.value || store.resourceUploading || !node.value || !resource.value?.collectionKey) return
    const ids = manifest.selected.value.filter((item) => !!item.audioId).map((item) => item.audioId!)
    if (JSON.stringify(node.value.config[resource.value.collectionKey]) === JSON.stringify(ids)) return
    const config = { ...node.value.config, [resource.value.collectionKey]: ids }
    delete config[resource.value.configKey]
    store.updateNode(node.value.id, { config })
  }
  watch(() => JSON.stringify([node.value?.id, resource.value?.collectionKey, node.value?.config]), reconcile, { immediate: true, flush: 'sync' })
  watch(() => audios().map((audio) => audio.id).join('|'), reconcile)
  watch(() => JSON.stringify([manifest.uploading.value, manifest.selected.value.map((item) => item.audioId || item.id)]), synchronize, { flush: 'post' })
  function setMode(value: string) {
    if (!node.value || !resource.value?.collectionKey) return
    const config = { ...node.value.config }
    delete config[resource.value.configKey]; delete config[resource.value.collectionKey]
    if (value === 'collection') {
      const ids = manifest.selected.value.flatMap((item) => item.audioId ? [item.audioId] : [])
      const previous = node.value.config[resource.value.configKey]
      if (typeof previous === 'string' && previous && !ids.includes(previous)) ids.unshift(previous)
      config[resource.value.collectionKey] = ids
    }
    store.updateNode(node.value.id, { config })
  }
  const incomplete = computed(() => mode.value === 'collection' && manifest.selected.value.some((item) => !item.audioId || !!manifest.validation(item)))
  return { node, resource, mode, incomplete, setMode, synchronize }
}
