<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { API_BASE_URL, parseApiError } from '../api'
import { t, message, messageRef } from '../i18n'
import type { Audio } from '../stores/projects'
import { fileExtension, type UploadPolicy } from '../composables/useUploadQueue'

const props = defineProps<{ projectId: string; audios: readonly Audio[]; value: unknown; sourceLanguage?: string | null; resourceType: string; locked: boolean }>()
const emit = defineEmits<{ bind: [id: string | null]; uploaded: [audio: Audio]; busy: [value: boolean] }>()
const language = ref(props.sourceLanguage || '')
const fileInput = ref<HTMLInputElement | null>(null)
const pending = ref<File | null>(null)
const uploading = ref(false)
const policy = ref<UploadPolicy | null>(null)
const policyLoading = ref(false)
const error = messageRef()
let alive = true
const controller = new AbortController()
async function loadPolicy() {
  if (props.locked || policyLoading.value) return
  policyLoading.value = true
  try {
    const response = await fetch(`${API_BASE_URL}/upload-policy`, { signal: controller.signal })
    if (!response.ok) throw await parseApiError(response)
    const data = await response.json() as UploadPolicy
    if (!Array.isArray(data.allowed_extensions) || !data.allowed_extensions.every((value) => typeof value === 'string') || !Number.isFinite(data.max_upload_bytes) || data.max_upload_bytes <= 0) throw message('app.errors.policy')
    if (alive) { policy.value = data; error.set('') }
  } catch (reason) { if (alive) error.set(reason instanceof Error ? reason : message('app.errors.policy')) }
  finally { if (alive) policyLoading.value = false }
}
async function upload(file: File) {
  if (props.locked || uploading.value) return
  pending.value = file
  if (!language.value.trim()) { error.set(message('app.upload.chooseSource')); return }
  if (!policy.value) { error.set(message('app.upload.policyMissing')); return }
  if (!policy.value.allowed_extensions.includes(fileExtension(file.name))) { error.set(message('app.upload.unsupported', { extensions: policy.value.allowed_extensions.join(' / ') })); return }
  if (file.size > policy.value.max_upload_bytes) { error.set(message('workflow.resourceTooLarge')); return }
  uploading.value = true
  emit('busy', true)
  error.set('')
  const form = new FormData()
  form.append('file', file)
  form.append('source_language', language.value.trim())
  try {
    const response = await fetch(`${API_BASE_URL}/projects/${encodeURIComponent(props.projectId)}/audios`, { method: 'POST', body: form, signal: controller.signal })
    if (!response.ok) throw await parseApiError(response)
    const payload = await response.json() as { audio: Audio }
    if (!payload.audio?.id || !payload.audio.source) throw message('app.errors.audioIncomplete')
    if (!alive) return
    uploading.value = false
    emit('busy', false)
    pending.value = null
    emit('uploaded', payload.audio)
  } catch (reason) {
    if (alive) error.set(reason instanceof Error ? reason : message('workflow.resourceUploadFailed'))
  } finally {
    if (alive) { uploading.value = false; emit('busy', false) }
  }
}
function choose(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void upload(file)
}
function drop(event: DragEvent) {
  if (props.locked) return
  const file = event.dataTransfer?.files[0]
  if (file) void upload(file)
}
onMounted(() => { void loadPolicy() })
onUnmounted(() => { alive = false; controller.abort(); emit('busy', false) })
</script>

<template>
  <div class="node-resource-input" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop @dragover.prevent @drop.prevent.stop="drop">
    <p v-if="resourceType !== 'audio'" role="alert">{{ t('workflow.resourceUnsupported', { type: resourceType }) }}</p>
    <template v-else>
      <label><span>{{ t('workflow.resourceChoose') }}</span><select data-resource="audio" :value="value || ''" :disabled="locked" @change="!locked && emit('bind', ($event.target as HTMLSelectElement).value || null)"><option value="">{{ t('workflow.resourceContext') }}</option><option v-for="audio in audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }}</option><option v-if="value && !audios.some((audio) => audio.id === value)" :value="String(value)">{{ t('workflow.resourceMissing') }}</option></select></label>
      <label><span>{{ t('app.create.source') }}</span><input :value="language" @input="!locked && (language = ($event.target as HTMLInputElement).value)" data-resource-language type="text" :disabled="locked" :placeholder="t('app.create.sourcePlaceholder')" autocomplete="off" /></label>
      <button class="node-resource-upload" type="button" :disabled="locked || !policy" @click="fileInput?.click()">{{ t('workflow.resourceUpload') }}</button>
      <input ref="fileInput" class="visually-hidden" type="file" :accept="policy?.allowed_extensions.map((extension) => `.${extension}`).join(',')" :disabled="locked || !policy" :aria-label="t('workflow.resourceFile')" @change="choose" />
      <small>{{ t('workflow.resourceDrop') }}</small>
      <progress v-if="uploading" :aria-label="t('workflow.resourceUploading')" />
      <small v-if="uploading" role="status">{{ t('workflow.resourceUploading') }}</small>
      <p v-if="error.text.value" class="node-resource-error" role="alert" :title="error.text.value">{{ error.text.value }}</p>
      <button v-if="pending && !uploading" type="button" :disabled="locked" @click="upload(pending!)">{{ t('app.upload.retry') }}</button>
      <button v-if="!policy" type="button" :disabled="locked || policyLoading" @click="loadPolicy">{{ t('app.create.retryPolicy') }}</button>
    </template>
  </div>
</template>
