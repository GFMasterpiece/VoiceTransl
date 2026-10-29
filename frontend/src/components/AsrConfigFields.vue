<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { API_BASE_URL, type PipelineConfig } from '../stores/projectDetail'

type AsrConfig = PipelineConfig['asr']
interface ModelItem {
  model: string
  backend: string | null
  size_bytes: number
  architecture: string | null
  selectable: boolean
  reason: string | null
}
interface Catalog {
  provider: 'crispasr'
  executable: { ok: boolean; version: string | null; message: string }
  model_dir: string
  restart_required: boolean
  backends: string[]
  items: ModelItem[]
  truncated: boolean
  warnings: string[]
}
const props = defineProps<{ config: AsrConfig; locked: boolean; audioKey: string }>()
const emit = defineEmits<{ change: [patch: Partial<AsrConfig>] }>()
const catalog = ref<Catalog | null>(null)
const loading = ref(false)
const error = ref('')
const knownBackends = [
  { value: 'qwen3', label: 'Qwen3 ASR（通用）' },
  { value: 'qwen3-1.7b', label: 'Qwen3 ASR 1.7B' },
  { value: 'whisper', label: 'Whisper' },
  { value: 'mega-asr', label: 'Mega ASR' },
  { value: 'voxtral', label: 'Voxtral' },
  { value: 'voxtral4b', label: 'Voxtral 4B' },
  { value: 'granite', label: 'Granite' },
]
const backendOptions = computed(() => {
  const values = [...new Set([...knownBackends.map((item) => item.value), props.config.backend, ...(catalog.value?.backends || [])])]
  return values.map((value) => ({ value, label: knownBackends.find((item) => item.value === value)?.label || safeText(value) }))
})
const availableItems = computed(() => catalog.value?.items.filter(canSelect) || [])
const selectedModel = computed(() => availableItems.value.find((item) => item.model === props.config.model && item.backend === props.config.backend)?.model || '')
const selectionReason = computed(() => {
  if (props.locked) return '任务运行、取消或排队中，只能查看列表；任务结束后可修改。'
  if (loading.value) return '正在读取本地模型，请等待目录检查完成。'
  if (error.value) return '模型列表读取失败，可重试或保留手动文件名。'
  if (!catalog.value?.executable.ok) return 'CrispASR 不可用，请检查全局设置中的可执行文件。'
  if (!availableItems.value.length) return '没有可选模型，可检查全局设置或手动确认文件名。'
  return '选择模型时会同步匹配的识别后端，不会自动保存。'
})
let generation = 0
let controller: AbortController | undefined
let requestTimeout: number | undefined
let alive = true

function safeText(value: string) {
  return value.replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\s，。；：、"'<>]+|(?<![.\w@])\/[^\s，。；：、"'<>]+/g, '［路径已隐藏］')
}
function validBackend(value: unknown): value is string { return typeof value === 'string' && /^[a-z][a-z0-9_.-]*$/.test(value) }
function validFilename(value: unknown): value is string { return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value) }
function readCatalog(value: unknown): Catalog {
  const data = value as Catalog
  if (!data || data.provider !== 'crispasr' || typeof data.executable?.ok !== 'boolean' || typeof data.executable?.message !== 'string'
    || !(data.executable.version === null || typeof data.executable.version === 'string') || typeof data.model_dir !== 'string'
    || !/^(?:\.\/(?!.*(?:^|\/)\.\.(?:\/|$))[^\u0000-\u001f\\]*|@root\/[1-9]\d*(?:\/[^\u0000-\u001f\\]*)?|@unavailable)$/.test(data.model_dir)
    || typeof data.restart_required !== 'boolean' || typeof data.truncated !== 'boolean'
    || !Array.isArray(data.backends) || !data.backends.every(validBackend) || !Array.isArray(data.warnings) || !data.warnings.every((warning) => typeof warning === 'string')
    || !Array.isArray(data.items) || !data.items.every((item) => item && validFilename(item.model) && (item.backend === null || validBackend(item.backend))
      && Number.isFinite(item.size_bytes) && item.size_bytes >= 0 && typeof item.selectable === 'boolean'
      && (item.architecture === null || typeof item.architecture === 'string') && (item.reason === null || typeof item.reason === 'string'))) {
    throw new Error('模型列表响应不完整或路径未脱敏，请检查后端版本并重试。')
  }
  return { ...data, executable: { ...data.executable, message: safeText(data.executable.message), version: data.executable.version === null ? null : safeText(data.executable.version) },
    items: data.items.map((item) => ({ ...item, reason: item.reason === null ? null : safeText(item.reason), architecture: item.architecture === null ? null : safeText(item.architecture) })), warnings: data.warnings.map(safeText) }
}
function canSelect(item: ModelItem) { return !!catalog.value?.executable.ok && item.selectable && !!item.backend && catalog.value.backends.includes(item.backend) }
function unavailableReason(item: ModelItem) { return item.reason || (!catalog.value?.executable.ok ? 'CrispASR 不可用' : '当前 CLI 不支持或无法确定识别后端') }
function sizeLabel(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB` }
function update(patch: Partial<AsrConfig>) { if (!props.locked) emit('change', patch) }
function textValue(event: Event) { return (event.target as HTMLInputElement).value }
function chooseModel(event: Event) {
  if (props.locked || loading.value || error.value || props.config.provider !== 'crispasr') return
  const item = catalog.value?.items.find((entry) => entry.model === textValue(event))
  if (item && canSelect(item) && item.backend) update({ model: item.model, backend: item.backend })
}
function invalidate() { ++generation; controller?.abort(); if (requestTimeout !== undefined) window.clearTimeout(requestTimeout); requestTimeout = undefined; catalog.value = null; error.value = ''; loading.value = false }
async function fetchModels() {
  if (props.config.provider !== 'crispasr') return
  const request = ++generation
  controller?.abort()
  if (requestTimeout !== undefined) window.clearTimeout(requestTimeout)
  const requestController = new AbortController()
  controller = requestController
  const audioKey = props.audioKey
  catalog.value = null; error.value = ''; loading.value = true
  let timedOut = false
  const timeout = window.setTimeout(() => { timedOut = true; requestController.abort() }, 25000)
  requestTimeout = timeout
  try {
    const response = await fetch(`${API_BASE_URL}/settings/local-asr/models`, { signal: requestController.signal })
    if (!response.ok) {
      let message = '模型列表读取失败，请检查全局设置后重试。'
      try {
        const payload = await response.json() as { error?: { message?: unknown } }
        if (typeof payload.error?.message === 'string' && /[\u3400-\u9fff]/.test(payload.error.message)) message = safeText(payload.error.message)
      } catch { /* Keep the actionable fallback for malformed errors. */ }
      throw new Error(message)
    }
    const result = readCatalog(await response.json())
    if (alive && request === generation && props.audioKey === audioKey && props.config.provider === 'crispasr') catalog.value = result
  } catch (reason) {
    if (!alive || request !== generation || props.audioKey !== audioKey || props.config.provider !== 'crispasr') return
    if (timedOut) error.value = '读取本地模型超时，请确认后端服务和 CrispASR 状态后重试。'
    else if (!requestController.signal.aborted) error.value = reason instanceof Error && !(reason instanceof TypeError) && !(reason instanceof SyntaxError) && /[\u3400-\u9fff]/.test(reason.message)
      ? safeText(reason.message) : '模型列表读取失败，请检查后端连接后重试。'
  } finally {
    window.clearTimeout(timeout)
    if (requestTimeout === timeout) requestTimeout = undefined
    if (alive && request === generation) loading.value = false
  }
}
watch([() => props.audioKey, () => props.config.provider], () => { invalidate(); if (props.config.provider === 'crispasr') void fetchModels() }, { immediate: true })
onUnmounted(() => { alive = false; invalidate() })
</script>

<template>
  <div class="asr-config-fields">
    <div class="modal-field-grid">
      <label class="config-field"><span>识别 Provider</span><select :value="config.provider" :disabled="locked" @change="update({ provider: textValue($event) })"><option value="mock">Mock（演示）</option><option value="crispasr">CrispASR（本机）</option></select></label>
      <label class="config-field"><span>识别后端</span><select :value="config.backend" :disabled="locked" @change="update({ backend: textValue($event) })"><option v-for="backend in backendOptions" :key="backend.value" :value="backend.value">{{ backend.label }}</option></select></label>
    </div>
    <section v-if="config.provider === 'crispasr'" class="asr-model-catalog" aria-labelledby="asr-model-catalog-title">
      <div class="asr-catalog-heading"><h4 id="asr-model-catalog-title">本地模型</h4><button class="secondary-button compact-button" type="button" :disabled="loading" @click="fetchModels">{{ loading ? '正在读取…' : '刷新模型列表' }}</button></div>
      <div v-if="loading" class="asr-catalog-loading" role="status"><p>正在检查当前生效目录与 CrispASR 支持的模型…</p><div class="settings-skeleton"></div></div>
      <div v-else-if="error" class="settings-feedback settings-error" role="alert"><p>{{ error }}</p><p>关闭此节点弹窗后，可从顶部「设置」检查可执行文件与模型目录。</p></div>
      <template v-else-if="catalog">
        <p class="settings-help">当前生效目录：<code>{{ catalog.model_dir }}</code></p>
        <p v-if="catalog.restart_required" class="settings-restart-pending">全局路径有待生效修改，此列表仍来自当前目录。请等待运行任务结束后手动重启后端，再刷新列表。</p>
        <p class="settings-help">{{ catalog.executable.message }}<template v-if="catalog.executable.version"> · {{ catalog.executable.version }}</template></p>
        <p v-if="!catalog.executable.ok" class="settings-feedback settings-error" role="status">CrispASR 不可用。关闭此节点弹窗后，在顶部「设置」检查可执行文件；当前文件名会保留。</p>
        <p v-if="!catalog.items.length" class="asr-catalog-empty" role="status">当前目录没有发现模型。可在全局设置检查目录，或手动填写已确认存在的可信模型文件名。</p>
        <label v-else class="config-field asr-catalog-picker" for="asr-discovered-model"><span>选择本地模型</span><select id="asr-discovered-model" :value="selectedModel" :disabled="locked || !availableItems.length" aria-describedby="asr-model-selection-note" @change="chooseModel"><option value="">选择模型（保留当前文件名）</option><option v-for="item in catalog.items" :key="item.model" :value="item.model" :disabled="!canSelect(item)">{{ item.model }} · {{ canSelect(item) ? `${item.backend} · ${sizeLabel(item.size_bytes)}` : unavailableReason(item) }}</option></select></label>
        <p v-if="config.model && !selectedModel" class="settings-help">当前文件名「{{ safeText(config.model) }}」未匹配可用模型，已保留；可手动确认或选择其他模型。</p>
        <p v-if="catalog.truncated" class="settings-help">目录条目较多，列表已截断；未列出的文件可手动填写。</p>
        <ul v-if="catalog.warnings.length" class="asr-catalog-warnings"><li v-for="(warning, index) in catalog.warnings" :key="index">{{ warning }}</li></ul>
      </template>
      <p id="asr-model-selection-note" class="settings-help">{{ selectionReason }}</p>
      <p class="settings-help">发现文件不代表已验证推理可用；任务启动后仍会检查模型格式与运行能力。</p>
    </section>
    <div class="modal-field-grid asr-parameter-grid">
      <label class="config-field"><span>{{ config.provider === 'crispasr' ? '识别模型（可手动填写文件名）' : '识别模型' }}</span><input :value="config.model" :disabled="locked" @input="update({ model: textValue($event) })" /></label>
      <label class="config-field"><span>识别语言</span><input :value="config.language" :disabled="locked" @input="update({ language: textValue($event) })" /></label>
      <label class="config-field"><span>分段时长（秒）</span><input :value="config.chunk_seconds" type="number" min="1" max="600" :disabled="locked" @input="update({ chunk_seconds: Number(textValue($event)) })" /></label>
    </div>
    <p v-if="config.provider === 'crispasr'" class="settings-help">手动填写仅接受模型目录中的可信文件名，不填写路径。请确认该模型与所选识别后端匹配。</p>
    <label class="check-option modal-check"><input :checked="config.vad_enabled" type="checkbox" :disabled="locked" @change="update({ vad_enabled: ($event.target as HTMLInputElement).checked })" /><span>启用语音活动检测（VAD）</span></label>
  </div>
</template>
