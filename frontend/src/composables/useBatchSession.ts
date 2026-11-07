import { computed, watch } from 'vue'
import { t } from '../i18n'
import type { Audio } from '../stores/projects'
import { useWorkflowStore } from '../stores/workflow'
import { useBatchManifest } from './useBatchManifest'
import { useBatches } from './useBatches'
import { useAudioCollection } from './useAudioCollection'
import { activeTargetBusy, baseRunReason } from './workflowExecution'

export function useBatchSession(projectId: string, uploaded: (audio: Audio) => void, audios: () => readonly Audio[] = () => [], blocked: () => boolean = () => false) {
  const store = useWorkflowStore()
  const batches = useBatches(projectId)
  const locked = computed(() => store.loading || store.saving || store.validating || store.actionLoading || store.resourceUploading || store.activeSingleRun || batches.submitting.value)
  const manifest = useBatchManifest(projectId, uploaded, () => locked.value)
  const collection = useAudioCollection(manifest, audios, projectId)
  const startReason = computed(() => {
    if (store.projectId !== projectId) return t('batch.invalidGraph')
    if (blocked() || manifest.uploading.value) return t('batch.busy')
    if (collection.incomplete.value) return t('batch.unready')
    const base = baseRunReason(store)
    if (base) return base
    if (batches.pending.value) return t('batch.pendingRequest')
    const ids = store.boundAudioIds
    if (ids !== null) {
      if (!ids.length || new Set(ids).size !== ids.length) return t('batch.empty')
      if (ids.some((id) => !audios().some((audio) => audio.id === id))) return t('workflow.resourceMissing')
      if (ids.length > 1 && batches.active.value) return t('batch.busy')
      if (ids.length === 1 && activeTargetBusy(store, ids[0])) return t('batch.busy')
    } else if (!(store.boundAudioId || store.audioId)) return t('batch.empty')
    else if (activeTargetBusy(store, store.boundAudioId || store.audioId)) return t('batch.busy')
    return ''
  })
  watch(batches.submitting, (value) => { store.batchSubmitting = value }, { flush: 'sync' })
  watch(batches.active, (value) => { store.batchRunning = value }, { flush: 'sync' })
  async function start() {
    if (startReason.value || !store.document) return
    try {
      const ids = store.boundAudioIds
      if (ids && ids.length > 1) return await batches.create(store.document.workflow_revision, [...ids])
      return await store.startRun()
    } catch { /* The API layer retains the original actionable diagnostic. */ }
  }
  function dispose() { manifest.dispose(); batches.dispose(); store.batchSubmitting = false; store.batchRunning = false }
  return { manifest, collection, batches, locked, startReason, start, dispose }
}
