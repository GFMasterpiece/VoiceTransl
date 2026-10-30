import { computed, ref } from 'vue'
import { displayError, locale, message, messageRef, t } from '../i18n'

export interface UploadPolicy {
  allowed_extensions: string[]
  max_upload_bytes: number
}

export type UploadStatus = 'invalid' | 'ready' | 'uploading' | 'created' | 'failed'
export interface UploadOptions { name: string; language: string; targetLanguage?: string }
export interface UploadItem {
  id: number
  file: File
  status: UploadStatus
  error: string
  projectId?: string
  audioId?: string
  snapshot?: UploadOptions
}

function fileBasename(name: string) {
  return name.split('/').filter((part) => part && part !== '.').pop() || ''
}

// Match POSIX pathlib's final suffix; the server never uses this name as a path.
export function fileExtension(name: string) {
  const basename = fileBasename(name)
  const index = basename.lastIndexOf('.')
  return index > 0 && index < basename.length - 1 ? basename.slice(index + 1).toLowerCase() : ''
}

export function fileStem(name: string) {
  const basename = fileBasename(name) || 'audio'
  return fileExtension(name) ? basename.slice(0, basename.lastIndexOf('.')) : basename
}

// Keep the error source rather than a translated snapshot so existing rows update
// with the interface locale without replacing files, status, or retry snapshots.
function deferredMessage(render: () => string): Error {
  return Object.defineProperty(new Error(), 'message', { get: render })
}

function uploadValidation(file: File, policy: UploadPolicy | null): Error | null {
  if (!policy) return message('app.upload.policyMissing')
  if (!policy.allowed_extensions.includes(fileExtension(file.name))) {
    return message('app.upload.unsupported', { extensions: policy.allowed_extensions.map((extension) => extension.toUpperCase()).join(' / ') })
  }
  if (file.size > policy.max_upload_bytes) {
    return deferredMessage(() => t('app.upload.tooLarge', { size: new Intl.NumberFormat(locale.value, { maximumFractionDigits: 20 }).format(policy.max_upload_bytes / 1024 / 1024) }))
  }
  // Empty files and unusual MIME types are accepted by the creation endpoint.
  return null
}

export function validateUpload(file: File, policy: UploadPolicy | null): string {
  const reason = uploadValidation(file, policy)
  return reason ? displayError(reason) : ''
}

function sourceLanguageValidation(sourceLanguage: string): Error | null {
  return !!sourceLanguage && sourceLanguage === sourceLanguage.trim() && sourceLanguage.length <= 32 && !/[\u0000-\u001f\u007f]/.test(sourceLanguage)
    ? null
    : message('app.upload.invalidSource')
}

export function validateSourceLanguage(sourceLanguage: string): string {
  const reason = sourceLanguageValidation(sourceLanguage)
  return reason ? displayError(reason) : ''
}

function languagesValidation(sourceLanguage: string, targetLanguage: string): Error | null {
  const sourceError = sourceLanguageValidation(sourceLanguage)
  if (sourceError) return sourceError
  if (!targetLanguage || targetLanguage !== targetLanguage.trim() || targetLanguage.length > 32 || /[\u0000-\u001f\u007f]/.test(targetLanguage)) return message('app.upload.invalidTarget')
  if (sourceLanguage.toLowerCase() === targetLanguage.toLowerCase()) return message('app.upload.sameLanguage')
  return null
}

export function validateLanguages(sourceLanguage: string, targetLanguage: string): string {
  const reason = languagesValidation(sourceLanguage, targetLanguage)
  return reason ? displayError(reason) : ''
}

function uploadError(reason: unknown): Error {
  if (reason instanceof TypeError) return message('app.upload.network')
  return deferredMessage(() => t('app.upload.recovery', { error: displayError(reason, 'app.upload.failed') }))
}

export function useUploadQueue(
  createProject: (file: File, name: string, language: string, targetLanguage: string) => Promise<{ id: string; audios?: { id: string }[] }>,
  addAudio: (projectId: string, file: File, language: string) => Promise<{ id: string }>,
) {
  const items = ref<UploadItem[]>([])
  const busy = ref(false)
  const { text: feedback, set: setFeedback } = messageRef()
  const itemErrors = new Map<number, ReturnType<typeof messageRef>>()
  function setItemError(item: UploadItem, reason: unknown) { itemErrors.get(item.id)!.set(reason) }
  const policy = ref<UploadPolicy | null>(null)
  const targetProjectId = ref<string | null>(null)
  const targetName = ref('')
  let projectSnapshot: UploadOptions | null = null
  let nextId = 0
  const validCount = computed(() => items.value.filter((item) => item.status !== 'invalid').length)
  const readyCount = computed(() => items.value.filter((item) => item.status === 'ready').length)
  const createdCount = computed(() => items.value.filter((item) => item.status === 'created').length)
  const failedCount = computed(() => items.value.filter((item) => item.status === 'failed').length)
  const pendingCount = computed(() => items.value.filter((item) => item.status === 'ready' || item.status === 'uploading').length)

  function setPolicy(value: UploadPolicy) {
    if (busy.value) return false
    policy.value = value
    for (const item of items.value) {
      if (item.status !== 'invalid' && item.status !== 'ready') continue
      const reason = uploadValidation(item.file, value)
      setItemError(item, reason || '')
      item.status = reason ? 'invalid' : 'ready'
    }
    return true
  }

  function add(files: Iterable<File>) {
    if (busy.value) return false
    const messages: Error[] = []
    for (const file of files) {
      if (items.value.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified && item.file.type === file.type)) {
        messages.push(message('app.upload.duplicate', { name: file.name }))
        continue
      }
      if (items.value.some((item) => item.file.name === file.name)) {
        messages.push(message('app.upload.sameName', { name: file.name }))
      }
      const reason = uploadValidation(file, policy.value)
      const error = messageRef(reason || '')
      const id = ++nextId
      itemErrors.set(id, error)
      items.value.push({ id, file, status: reason ? 'invalid' : 'ready', get error() { return error.text.value }, set error(value: string) { error.text.value = value } })
    }
    setFeedback(messages.length ? deferredMessage(() => messages.map((reason) => displayError(reason)).join(' ')) : '')
    return true
  }

  function remove(id: number) {
    if (busy.value) return false
    const item = items.value.find((entry) => entry.id === id)
    if (!item || item.status === 'created') return false
    items.value = items.value.filter((entry) => entry.id !== id)
    itemErrors.delete(id)
    return true
  }

  function clear() {
    if (busy.value) return false
    items.value = []
    itemErrors.clear()
    feedback.value = ''
    targetProjectId.value = null
    targetName.value = ''
    projectSnapshot = null
    return true
  }

  function setTarget(projectId: string, name: string) {
    if (busy.value || items.value.length) return false
    targetProjectId.value = projectId
    targetName.value = name
    return true
  }

  async function process(selected: UploadItem[]) {
    if (busy.value || !selected.length) return
    busy.value = true
    feedback.value = ''
    try {
      for (const item of selected) {
        item.status = 'uploading'
        item.error = ''
        try {
          if (targetProjectId.value) {
            const audio = await addAudio(targetProjectId.value, item.file, item.snapshot!.language)
            item.audioId = audio.id
          } else {
            const project = await createProject(item.file, projectSnapshot!.name, item.snapshot!.language, item.snapshot!.targetLanguage || '')
            targetProjectId.value = project.id
            targetName.value = projectSnapshot!.name
            item.audioId = project.audios?.[0]?.id || 'aud_primary'
          }
          item.projectId = targetProjectId.value!
          item.status = 'created'
        } catch (reason) {
          setItemError(item, uploadError(reason))
          item.status = 'failed'
        }
      }
    } finally {
      busy.value = false
    }
  }

  async function submit(options: UploadOptions) {
    if (busy.value) return
    const selected = items.value.filter((item) => item.status === 'ready')
    // A new project requires both languages; appending validates only the
    // new audio's source because the parent project owns the target.
    setFeedback((targetProjectId.value
      ? sourceLanguageValidation(options.language)
      : languagesValidation(options.language, options.targetLanguage || '')) || '')
    if (feedback.value) return
    if (!projectSnapshot && selected.length) projectSnapshot = { name: options.name.trim() || fileStem(selected[0].file.name), language: options.language, targetLanguage: options.targetLanguage }
    for (const item of selected) {
      // Retry reuses the immutable snapshot, never the current form values.
      if (!item.snapshot) item.snapshot = { name: projectSnapshot!.name, language: options.language, targetLanguage: options.targetLanguage }
    }
    await process(selected)
  }

  async function retry(id: number) {
    if (busy.value) return
    const item = items.value.find((entry) => entry.id === id && entry.status === 'failed')
    if (item) await process([item])
  }

  return { items, busy, feedback, setFeedback, policy, targetProjectId, targetName, validCount, readyCount, createdCount, failedCount, pendingCount, setTarget, setPolicy, add, remove, clear, submit, retry }
}
