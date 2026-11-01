import type { WorkflowError, WorkflowRun } from '../stores/workflow'

export interface RunRecord {
  id: string
  nodeId?: string
  labelKey: string
  timestamp: string | null
  status?: string
  error?: WorkflowError
}

/** Only fields persisted in the selected run are represented; untimed facts stay untimed. */
export function runRecords(run: WorkflowRun): RunRecord[] {
  const records: RunRecord[] = [
    { id: 'run-created', labelKey: 'panelCreatedAt', timestamp: run.created_at },
    { id: 'run-started', labelKey: 'panelStartedAt', timestamp: run.started_at },
    { id: 'run-finished', labelKey: 'panelFinishedAt', timestamp: run.finished_at },
    { id: 'run-status', labelKey: 'panelStatus', timestamp: null, status: run.status },
  ]
  if (run.cancel_requested) records.push({ id: 'run-cancel', labelKey: 'panelCancelRequested', timestamp: null })
  if (run.error) records.push({ id: 'run-error', labelKey: 'panelError', timestamp: null, error: run.error })
  for (const node of run.nodes) {
    records.push(
      { id: `node:${node.node_id}:started`, nodeId: node.node_id, labelKey: 'panelStartedAt', timestamp: node.started_at },
      { id: `node:${node.node_id}:finished`, nodeId: node.node_id, labelKey: 'panelFinishedAt', timestamp: node.finished_at },
      { id: `node:${node.node_id}:status`, nodeId: node.node_id, labelKey: 'panelStatus', timestamp: null, status: node.status },
    )
    if (node.error) records.push({ id: `node:${node.node_id}:error`, nodeId: node.node_id, labelKey: 'panelError', timestamp: null, error: node.error })
  }
  const recordedTime = (record: RunRecord) => record.timestamp ? Date.parse(record.timestamp) : Number.NaN
  // Stable sorting keeps equal timestamps and missing timestamps in persisted node order.
  return records.sort((a, b) => {
    const left = recordedTime(a), right = recordedTime(b)
    return Number.isNaN(left) ? (Number.isNaN(right) ? 0 : 1) : Number.isNaN(right) ? -1 : left - right
  })
}
