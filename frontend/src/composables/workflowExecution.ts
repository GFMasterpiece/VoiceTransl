import { computed, inject, type ComputedRef, type InjectionKey } from 'vue'
import { t } from '../i18n'
import { useWorkflowStore } from '../stores/workflow'
export interface WorkflowExecution { reason: ComputedRef<string>; start: () => Promise<unknown> }
export const workflowExecutionKey: InjectionKey<WorkflowExecution> = Symbol('workflowExecution')
export function baseRunReason(store: ReturnType<typeof useWorkflowStore>) {
  if (store.legacyConnectionUnsupported) return t('connections.error_LEGACY_CONNECTION_UNSUPPORTED')
  if (store.dirty) return t('workflow.saveBeforeRun')
  if (store.loading || store.saving || store.validating || store.resourceUploading || store.actionLoading || store.activeSingleRun) return t('batch.busy')
  if (!store.document || !store.registry || store.validationIssues.length) return t('batch.invalidGraph')
  return ''
}
export function activeTargetBusy(store: ReturnType<typeof useWorkflowStore>, target: string | null | undefined) {
  return !!store.activeRun && !!target && store.run?.audio_id === target
}
export function useWorkflowExecution(): WorkflowExecution {
  const provided = inject(workflowExecutionKey, null)
  if (provided) return provided
  const store = useWorkflowStore()
  return { reason: computed(() => baseRunReason(store) || (!store.boundAudioId && !store.audioId ? t('batch.empty') : activeTargetBusy(store, store.boundAudioId || store.audioId) ? t('batch.busy') : '')), start: () => store.startRun() }
}
