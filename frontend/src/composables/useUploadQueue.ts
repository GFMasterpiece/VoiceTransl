import { computed, ref } from 'vue'

export interface UploadPolicy {
  allowed_extensions: string[]
  max_upload_bytes: number
}

export type UploadStatus = 'invalid' | 'ready' | 'uploading' | 'created' | 'failed'
export interface UploadOptions { name: string; language: string; targetLanguage: string }
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

export function validateUpload(file: File, policy: UploadPolicy | null): string {
  if (!policy) return '尚未获取上传限制，请重试加载限制。'
  if (!policy.allowed_extensions.includes(fileExtension(file.name))) {
    return `格式不支持，请选择 ${policy.allowed_extensions.map((extension) => extension.toUpperCase()).join(' / ')} 文件。`
  }
  if (file.size > policy.max_upload_bytes) {
    return `超过 ${policy.max_upload_bytes / 1024 / 1024} MB 限制，请压缩或拆分音频后重新选择。`
  }
  // The server stores MIME/name as metadata and generates the source path itself.
  // Empty files and unusual MIME types are accepted by its creation endpoint too.
  return ''
}

export function validateLanguages(sourceLanguage: string, targetLanguage: string): string {
  const valid = (value: string) => !!value && value === value.trim() && value.length <= 32 && !/[\u0000-\u001f\u007f]/.test(value)
  if (!valid(sourceLanguage)) return '源语言必须非空、无首尾空白且最多 32 个字符。'
  if (!valid(targetLanguage)) return '目标语言必须非空、无首尾空白且最多 32 个字符。'
  if (sourceLanguage.toLowerCase() === targetLanguage.toLowerCase()) return '源语言与目标语言不能相同。'
  return ''
}

function uploadError(reason: unknown) {
  if (reason instanceof TypeError) return '无法连接后端，请检查服务和网络后重试此文件。'
  const message = reason instanceof Error ? reason.message : '音频上传失败'
  return `${message}。请检查文件或后端服务后重试此文件；已上传的音频会保留。`
}

export function useUploadQueue(
  createProject: (file: File, name: string, language: string, targetLanguage: string) => Promise<{ id: string; audios?: { id: string }[] }>,
  addAudio: (projectId: string, file: File, language: string) => Promise<{ id: string }>,
) {
  const items = ref<UploadItem[]>([])
  const busy = ref(false)
  const feedback = ref('')
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
      item.error = validateUpload(item.file, value)
      item.status = item.error ? 'invalid' : 'ready'
    }
    return true
  }

  function add(files: Iterable<File>) {
    if (busy.value) return false
    const messages: string[] = []
    for (const file of files) {
      if (items.value.some((item) => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified && item.file.type === file.type)) {
        messages.push(`“${file.name}”已在队列中，未重复添加。`)
        continue
      }
      if (items.value.some((item) => item.file.name === file.name)) {
        messages.push(`“${file.name}”与队列中文件同名，将作为不同音频保存，不会覆盖。`)
      }
      const error = validateUpload(file, policy.value)
      items.value.push({ id: ++nextId, file, status: error ? 'invalid' : 'ready', error })
    }
    feedback.value = messages.join(' ')
    return true
  }

  function remove(id: number) {
    if (busy.value) return false
    const item = items.value.find((entry) => entry.id === id)
    if (!item || item.status === 'created') return false
    items.value = items.value.filter((entry) => entry.id !== id)
    return true
  }

  function clear() {
    if (busy.value) return false
    items.value = []
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
            const project = await createProject(item.file, projectSnapshot!.name, item.snapshot!.language, item.snapshot!.targetLanguage)
            targetProjectId.value = project.id
            targetName.value = projectSnapshot!.name
            item.audioId = project.audios?.[0]?.id || 'aud_primary'
          }
          item.projectId = targetProjectId.value!
          item.status = 'created'
        } catch (reason) {
          item.error = uploadError(reason)
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
    feedback.value = targetProjectId.value
      ? (options.language.trim() ? '' : '源语言不能为空。')
      : validateLanguages(options.language, options.targetLanguage)
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

  return { items, busy, feedback, policy, targetProjectId, targetName, validCount, readyCount, createdCount, failedCount, pendingCount, setTarget, setPolicy, add, remove, clear, submit, retry }
}
