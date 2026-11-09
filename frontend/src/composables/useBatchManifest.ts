import { computed, ref, shallowRef } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { message, messageRef } from '../i18n'
import type { Audio, SourceOrigin } from '../stores/projects'
import { validateSourceLanguage, validateUpload, type UploadPolicy } from './useUploadQueue'

export interface ManifestItem {
  id: string
  file?: File
  origin: SourceOrigin | null
  name: string
  sizeBytes: number
  fileSelection?: boolean
  missing?: boolean
  language: string
  selected: boolean
  status: 'ready' | 'uploading' | 'uploaded' | 'failed'
  audioId?: string
  error?: unknown
}

export function randomRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `id${Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, '0')).join('')}`
}

// Origin metadata is a portable relative path, never a browser/local absolute path.
export function safeOriginPath(path: string) {
  return !!path && path.length <= 4096 && path.split('/').every((part) => !!part && part.length <= 255 && part === part.trim() && part !== '.' && part !== '..'
    && !/[\\:<>"|?*\p{Cc}]/u.test(part) && !/[. ]$/.test(part)
    && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part))
}

export function useBatchManifest(projectId: string, onUploaded: (audio: Audio) => void, blocked: () => boolean = () => false) {
  const items = ref<ManifestItem[]>([])
  const recursive = ref(true)
  const language = ref('')
  const policy = shallowRef<UploadPolicy | null>(null)
  const policyLoading = ref(false)
  const uploading = ref(false)
  const error = messageRef()
  const controllers = new Set<AbortController>()
  let alive = true
  function controller() { const value = new AbortController(); controllers.add(value); return value }
  function included(item: ManifestItem) { return item.selected && (recursive.value || !item.origin?.relative_path.includes('/')) }
  const selected = computed(() => items.value.filter(included))
  const pendingLocal = computed(() => items.value.some((item) => !!item.file && item.status !== 'uploaded'))
  function validation(item: ManifestItem) {
    if (item.missing) return message('workflow.resourceMissing').message
    if (item.audioId) return ''
    if (item.origin && (!safeOriginPath(item.origin.relative_path) || !safeOriginPath(item.origin.root_name)
      || item.origin.root_name.includes('/') || item.origin.root_name.length > 255 || item.origin.relative_path.split('/').pop() !== item.file?.name)) return message('batch.invalidPath').message
    return validateSourceLanguage(item.language) || (item.file ? validateUpload(item.file, policy.value) : '')
  }
  const ready = computed(() => selected.value.length > 0
    && selected.value.every((item) => !!item.audioId && !validation(item)))

  async function loadPolicy() {
    if (policyLoading.value || !alive) return
    policyLoading.value = true
    const request = controller()
    try {
      const response = await fetch(`${API_BASE_URL}/upload-policy`, { signal: request.signal })
      if (!response.ok) throw await parseApiError(response)
      const value = await response.json() as UploadPolicy
      if (!Array.isArray(value.allowed_extensions) || !value.allowed_extensions.every((entry) => typeof entry === 'string')
        || (value.upload_concurrency !== undefined && (!Number.isSafeInteger(value.upload_concurrency) || value.upload_concurrency < 1))
        || !Number.isFinite(value.max_upload_bytes) || value.max_upload_bytes <= 0) throw message('app.errors.policy')
      if (alive) { policy.value = value; error.set('') }
    } catch (reason) { if (alive) error.set(reason) }
    finally { controllers.delete(request); if (alive) policyLoading.value = false }
  }

  function add(files: Iterable<File>, folder = false) {
    if (uploading.value || blocked() || !alive) return
    const rootId = randomRequestId()
    const folderRoots = new Map<string, string>()
    for (const file of files) {
      const path = folder ? file.webkitRelativePath : file.name
      const parts = path.split('/')
      const rootName = folder && parts.length > 1 ? parts.shift()! : 'files'
      const relativePath = folder ? parts.join('/') : file.name
      if (folder && !folderRoots.has(rootName)) folderRoots.set(rootName, randomRequestId())
      items.value.push({ id: randomRequestId(), file, origin: { root_id: folder ? folderRoots.get(rootName)! : rootId, root_name: rootName, relative_path: relativePath },
        name: file.name, sizeBytes: file.size, fileSelection: !folder, language: language.value, selected: true, status: 'ready' })
    }
  }
  function addExisting(audio: Audio) {
    if (uploading.value || blocked() || !alive || items.value.some((item) => item.audioId === audio.id)) return
    items.value.push({ id: randomRequestId(), origin: audio.source.origin || null, name: audio.source.original_name, sizeBytes: audio.source.size_bytes,
      language: audio.source.language, selected: true, status: 'uploaded', audioId: audio.id })
  }
  function remove(id: string) { if (!uploading.value && !blocked()) items.value = items.value.filter((item) => item.id !== id) }
  function discardLocal() { if (!uploading.value) items.value = items.value.filter((item) => !!item.audioId) }
  function applyLanguage() {
    if (uploading.value || blocked()) return
    for (const item of items.value) if (!item.audioId && item.status !== 'uploading') item.language = language.value
  }
  async function upload(failedOnly = false) {
    if (uploading.value || blocked() || !alive) return
    const queue = selected.value.filter((item) => !item.audioId && (!failedOnly || item.status === 'failed') && !validation(item))
    if (!queue.length) return
    uploading.value = true
    error.set('')
    let cursor = 0
    async function worker() {
      while (alive && cursor < queue.length) {
        const item = queue[cursor++]!
        const request = controller()
        item.status = 'uploading'; item.error = undefined
        const form = new FormData()
        // Native folder File objects can retain a multipart path unless the filename is explicit.
        form.append('file', item.file!, item.file!.name)
        form.append('name', item.file!.name)
        form.append('source_language', item.language)
        if (item.origin) {
          form.append('origin_root_id', item.origin.root_id)
          form.append('origin_root_name', item.origin.root_name)
          form.append('origin_relative_path', item.origin.relative_path)
        }
        try {
          const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(projectId)}/audios`, { method: 'POST', body: form, signal: request.signal })
          if (!response.ok) throw await parseApiError(response)
          const value = await response.json() as { audio: Audio }
          if (!value.audio?.id || !value.audio.source) throw message('app.errors.audioIncomplete')
          if (!alive) return
          item.audioId = value.audio.id; item.status = 'uploaded'; item.file = undefined
          onUploaded(value.audio)
        } catch (reason) { if (alive) { item.status = 'failed'; item.error = reason } }
        finally { controllers.delete(request) }
      }
    }
    try { await Promise.all(Array.from({ length: Math.min(policy.value?.upload_concurrency ?? 3, queue.length) }, worker)) }
    finally { if (alive) uploading.value = false }
  }
  function dispose() { alive = false; for (const request of controllers) request.abort(); controllers.clear() }
  return { items, recursive, language, policy, policyLoading, uploading, error, selected, pendingLocal, ready, included, validation,
    loadPolicy, add, addExisting, remove, discardLocal, applyLanguage, upload, dispose }
}
export type BatchManifest = ReturnType<typeof useBatchManifest>
