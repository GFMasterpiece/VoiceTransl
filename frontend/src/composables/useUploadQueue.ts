import { computed, ref } from 'vue'

export interface UploadPolicy {
  allowed_extensions: string[]
  max_upload_bytes: number
}

export type UploadStatus = 'invalid' | 'ready' | 'uploading' | 'created' | 'failed'
export interface UploadOptions { name: string; language: string }
export interface UploadItem {
  id: number
  file: File
  status: UploadStatus
  error: string
  projectId?: string
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

function uploadError(reason: unknown) {
  if (reason instanceof TypeError) return '无法连接后端，请检查服务和网络后重试此文件。'
  const message = reason instanceof Error ? reason.message : '项目创建失败'
  return `${message}。请检查文件或后端服务后重试此文件；已创建的项目会保留。`
}

export function useUploadQueue(createProject: (file: File, name: string, language: string) => Promise<{ id: string }>) {
  const items = ref<UploadItem[]>([])
  const busy = ref(false)
  const feedback = ref('')
  const policy = ref<UploadPolicy | null>(null)
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
        messages.push(`“${file.name}”与队列中文件同名，将分别创建独立项目，不会覆盖。`)
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
          const project = await createProject(item.file, item.snapshot!.name, item.snapshot!.language)
          item.projectId = project.id
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
    const single = items.value.length === 1
    for (const item of selected) {
      item.snapshot = { name: single && options.name.trim() ? options.name.trim() : fileStem(item.file.name), language: options.language }
    }
    await process(selected)
  }

  async function retry(id: number) {
    if (busy.value) return
    const item = items.value.find((entry) => entry.id === id && entry.status === 'failed')
    if (item) await process([item])
  }

  return { items, busy, feedback, policy, validCount, readyCount, createdCount, failedCount, pendingCount, setPolicy, add, remove, clear, submit, retry }
}
