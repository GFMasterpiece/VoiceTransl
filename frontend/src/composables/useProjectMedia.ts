import { computed, ref, shallowRef } from 'vue'
import { API_BASE_URL, parseApiError, type ApiError } from '../api'
import { message, messageRef } from '../i18n'
import type { Audio } from '../stores/projects'

export type GroupMode = 'independent' | 'shared' | 'continuous'
export interface AudioGroupSummary { id: string; revision: number; name: string; mode: GroupMode; audio_count: number; created_at: string; updated_at: string }
export interface AudioGroup extends Omit<AudioGroupSummary, 'audio_count'> { schema_version: '1.0'; audio_ids: string[]; background: string }
export interface MediaPage<T> { items: T[]; total: number; limit: number; offset: number }
export interface GroupDraft { id: string; revision: number; name: string; audio_ids: string[]; background: string; mode: GroupMode }
const emptyDraft = (): GroupDraft => ({ id: '', revision: 0, name: '', audio_ids: [], background: '', mode: 'independent' })
const emptyPage = <T>(): MediaPage<T> => ({ items: [], total: 0, limit: 20, offset: 0 })
function readGroup(raw: unknown): AudioGroup {
  const value = raw as AudioGroup
  if (!value || value.schema_version !== '1.0' || !/^grp_[0-9a-f]{32}$/.test(value.id) || !Number.isInteger(value.revision) || value.revision < 1 || typeof value.name !== 'string' || !Array.isArray(value.audio_ids) || !value.audio_ids.every(id => typeof id === 'string') || new Set(value.audio_ids).size !== value.audio_ids.length || typeof value.background !== 'string' || !['independent','shared','continuous'].includes(value.mode)) throw message('media.invalidResponse')
  return value
}
function readPage<T>(raw: unknown): MediaPage<T> {
  const page = raw as MediaPage<T>
  if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 || !Number.isInteger(page.limit) || page.limit < 1 || !Number.isInteger(page.offset) || page.offset < 0) throw message('media.invalidResponse')
  return page
}
export function useProjectMedia(projectId: string) {
  const audioPage = shallowRef(emptyPage<Audio>())
  const groupPage = shallowRef(emptyPage<AudioGroupSummary>())
  const groups = ref<Record<string, AudioGroup>>({})
  const summaries = ref<Record<string, AudioGroupSummary>>({})
  const groupErrors = ref<Record<string, string>>({})
  const draft = ref<GroupDraft>(emptyDraft())
  const baseline = ref(JSON.stringify(draft.value))
  const editing = ref(false)
  const dirty = computed(() => editing.value && JSON.stringify(draft.value) !== baseline.value)
  const pendingReads = ref(0)
  const loading = computed(() => pendingReads.value > 0)
  const saving = ref(false), uncertain = ref(false)
  const error = messageRef()
  const selected = ref<string[]>([])
  const controllers = new Set<AbortController>()
  let alive = true, audioGeneration = 0, groupGeneration = 0, editGeneration = 0
  const detailGenerations = new Map<string, number>()
  const deleted = new Set<string>()
  const base = `${API_BASE_URL}/projects/${encodeURIComponent(projectId)}`
  async function request(path: string, options?: RequestInit) {
    const controller = new AbortController(); controllers.add(controller)
    try {
      const response = await fetch(base + path, { ...options, signal: controller.signal })
      if (!response.ok) throw await parseApiError(response)
      return response.status === 204 ? null : await response.json()
    } finally { controllers.delete(controller) }
  }
  function remember(group: AudioGroup) {
    if (!alive || deleted.has(group.id)) return
    if ((groups.value[group.id]?.revision || 0) > group.revision) return
    groups.value[group.id] = group
    const { audio_ids, background: _background, schema_version: _schema, ...summary } = group
    summaries.value[group.id] = { ...summary, audio_count: audio_ids.length }
    delete groupErrors.value[group.id]
  }
  async function loadAudios(offset = audioPage.value.offset) {
    const generation = ++audioGeneration
    pendingReads.value++
    try { const page = readPage<Audio>(await request(`/audios?limit=20&offset=${offset}`)); if (alive && generation === audioGeneration) audioPage.value = page }
    catch (reason) { if (alive) error.set(reason) }
    finally { if (alive) pendingReads.value = Math.max(0, pendingReads.value - 1) }
  }
  async function loadGroups(offset = groupPage.value.offset) {
    const generation = ++groupGeneration
    pendingReads.value++
    try {
      const page = readPage<AudioGroupSummary>(await request(`/audio-groups?limit=20&offset=${offset}`))
      if (alive && generation === groupGeneration) { groupPage.value = { ...page, items: page.items.filter(group => !deleted.has(group.id)).map(group => (summaries.value[group.id]?.revision || 0) > group.revision ? summaries.value[group.id]! : group) }; for (const group of groupPage.value.items) summaries.value[group.id] = group }
    } catch (reason) { if (alive) error.set(reason) }
    finally { if (alive) pendingReads.value = Math.max(0, pendingReads.value - 1) }
  }
  async function loadGroup(id: string) {
    if (!alive || deleted.has(id)) return null
    const generation = (detailGenerations.get(id) || 0) + 1
    detailGenerations.set(id, generation)
    pendingReads.value++
    try {
      const group = readGroup(await request(`/audio-groups/${encodeURIComponent(id)}`))
      if (!alive || deleted.has(id)) return null
      if (detailGenerations.get(id) === generation) remember(group)
      return groups.value[id] || null
    } catch (reason) {
      if (alive && !deleted.has(id) && detailGenerations.get(id) === generation) groupErrors.value[id] = reason instanceof Error ? reason.message : String(reason)
      return null
    } finally { if (alive) pendingReads.value = Math.max(0, pendingReads.value - 1) }
  }
  function replaceDraft(group?: AudioGroup) {
    editGeneration++
    draft.value = group ? { id: group.id, revision: group.revision, name: group.name, audio_ids: [...group.audio_ids], background: group.background, mode: group.mode } : emptyDraft()
    baseline.value = JSON.stringify(draft.value); editing.value = true
  }
  async function edit(id: string) {
    const generation = ++editGeneration, before = JSON.stringify(draft.value)
    const group = await loadGroup(id)
    if (alive && group && generation === editGeneration && before === JSON.stringify(draft.value)) replaceDraft(groups.value[id] || group)
  }
  function createDraft() { replaceDraft(); draft.value.audio_ids = [...selected.value] }
  function discard() { editGeneration++; draft.value = JSON.parse(baseline.value) as GroupDraft; uncertain.value = false }
  function addMembers(ids = selected.value) { draft.value.audio_ids = [...new Set([...draft.value.audio_ids, ...ids])]; editing.value = true }
  function move(id: string, delta: number) { const ids = draft.value.audio_ids; const index = ids.indexOf(id), target = index + delta; if (index >= 0 && target >= 0 && target < ids.length) [ids[index], ids[target]] = [ids[target]!, ids[index]!] }
  async function save() {
    if (saving.value || uncertain.value || !draft.value.name.trim()) return null
    saving.value = true; error.set('')
    const editToken = ++editGeneration, before = JSON.stringify(draft.value)
    const current = JSON.parse(JSON.stringify(draft.value)) as GroupDraft
    const body = { name: current.name.trim(), audio_ids: current.audio_ids, background: current.background, mode: current.mode, ...(current.id ? { expected_revision: current.revision } : {}) }
    try {
      const group = readGroup(await request(`/audio-groups${current.id ? '/' + encodeURIComponent(current.id) : ''}`, { method: current.id ? 'PUT' : 'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify(body) }))
      if (alive) { remember(group); if (editToken === editGeneration && before === JSON.stringify(draft.value)) replaceDraft(groups.value[group.id] || group); await loadGroups() }
      return alive ? group : null
    } catch (reason) { if (alive) { error.set(reason); if (!(reason as ApiError)?.status || (reason as ApiError).status >= 500) uncertain.value = true } return null }
    finally { if (alive) saving.value = false }
  }
  async function remove(id: string) {
    const group = groups.value[id] || await loadGroup(id)
    if (!group || saving.value || uncertain.value) return false
    saving.value = true; error.set(''); editGeneration++
    detailGenerations.set(id, (detailGenerations.get(id) || 0) + 1)
    try {
      await request(`/audio-groups/${encodeURIComponent(id)}?expected_revision=${group.revision}`, { method:'DELETE' })
      if (alive) { deleted.add(id); detailGenerations.set(id, (detailGenerations.get(id) || 0) + 1); delete groups.value[id]; delete summaries.value[id]; if (draft.value.id === id) { replaceDraft(); editing.value = false }; await loadGroups(0) }
      return true
    } catch (reason) { if (alive) { error.set(reason); if (!(reason as ApiError)?.status || (reason as ApiError).status >= 500) uncertain.value = true }; return false }
    finally { if (alive) saving.value = false }
  }
  async function refresh() { error.set(''); await Promise.all([loadAudios(), loadGroups(), ...Object.keys(groups.value).map(id => loadGroup(id))]) }
  function dispose() { alive = false; editGeneration++; audioGeneration++; groupGeneration++; pendingReads.value = 0; for (const controller of controllers) controller.abort(); controllers.clear() }
  return { audioPage, groupPage, groups, summaries, groupErrors, draft, dirty, editing, loading, saving, uncertain, error, selected, loadAudios, loadGroups, loadGroup, edit, createDraft, discard, addMembers, move, save, remove, refresh, dispose }
}
export type ProjectMedia = ReturnType<typeof useProjectMedia>
