import { computed, watch } from 'vue'
import { t } from '../i18n'
import type { Audio } from '../stores/projects'
import { useWorkflowStore } from '../stores/workflow'
import { useBatchManifest } from './useBatchManifest'
import { useBatches } from './useBatches'

export function useBatchSession(projectId: string, uploaded: (audio: Audio) => void) {
  const store = useWorkflowStore()
  const batches = useBatches(projectId)
  const locked = computed(() => store.loading || store.saving || store.validating || store.actionLoading || store.resourceUploading || store.activeSingleRun || batches.submitting.value)
  const manifest = useBatchManifest(projectId, uploaded, () => locked.value)
  const startReason = computed(() => {
    if (store.dirty) return t('batch.dirty')
    if (!store.document || !store.registry || store.validationIssues.length || store.projectId !== projectId) return t('batch.invalidGraph')
    if (store.boundAudioId) return t('batch.binding')
    if (locked.value || store.resourceUploading || manifest.uploading.value || batches.active.value) return t('batch.busy')
    if (!manifest.selected.value.length || manifest.selected.value.length > 10000) return t('batch.empty')
    if (!manifest.ready.value) return t('batch.unready')
    const ids = manifest.selected.value.map((item) => item.audioId)
    return new Set(ids).size === ids.length ? '' : t('batch.empty')
  })
  watch(batches.submitting, (value) => { store.batchSubmitting = value }, { flush: 'sync' })
  watch(batches.active, (value) => { store.batchRunning = value }, { flush: 'sync' })
  async function start() {
    if (startReason.value || !store.document) return
    await batches.create(store.document.workflow_revision, manifest.selected.value.map((item) => item.audioId!))
  }
  function dispose() { manifest.dispose(); batches.dispose(); store.batchSubmitting = false; store.batchRunning = false }
  return { manifest, batches, locked, startReason, start, dispose }
}
