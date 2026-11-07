import { computed, ref } from 'vue'

type CredentialChange = { operation: 'set'; api_key: string } | { operation: 'clear' }
export type CredentialUpdate = CredentialChange & { node_id: string }
function createSession() {
  // Values deliberately stay outside Pinia state, editor snapshots and browser storage.
  const changes = new Map<string, CredentialChange>()
  const version = ref(0)
  let request: { signature: string; credentialVersion: number; id: string } | null = null
  const pending = computed(() => { void version.value; return changes.size > 0 })
  function set(nodeId: string, value: string) {
    const current = changes.get(nodeId)
    if ((current?.operation === 'set' && current.api_key === value) || (!current && !value) || (current?.operation === 'clear' && !value)) return
    if (value) changes.set(nodeId, { operation: 'set', api_key: value })
    else changes.delete(nodeId)
    version.value++
  }
  function clear(nodeId: string) { if (changes.get(nodeId)?.operation !== 'clear') { changes.set(nodeId, { operation: 'clear' }); version.value++ } }
  function forget(nodeId?: string) {
    if (nodeId === undefined) { changes.clear(); request = null; version.value++ }
    else if (changes.delete(nodeId)) version.value++
  }
  function value(nodeId: string) { void version.value; const entry = changes.get(nodeId); return entry?.operation === 'set' ? entry.api_key : '' }
  function operation(nodeId: string) { void version.value; return changes.get(nodeId)?.operation }
  function updates(): CredentialUpdate[] { return [...changes].map(([node_id, update]) => ({ node_id, ...update })) }
  function requestId(signature: string) {
    if (!request || request.signature !== signature || request.credentialVersion !== version.value) request = { signature, credentialVersion: version.value, id: crypto.randomUUID() }
    return request.id
  }
  function redact(value: string) {
    for (const entry of changes.values()) if (entry.operation === 'set') value = value.split(entry.api_key).join('[redacted]')
    return value
  }
  function reconcile(ids: ReadonlySet<string>) { for (const id of changes.keys()) if (!ids.has(id)) forget(id) }
  return { pending, version, set, clear, forget, value, operation, updates, requestId, redact, reconcile }
}
const sessions = new WeakMap<object, ReturnType<typeof createSession>>()
export function workflowCredentials(context: object) {
  let session = sessions.get(context)
  if (!session) { session = createSession(); sessions.set(context, session) }
  return session
}
