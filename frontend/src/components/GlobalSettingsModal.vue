<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { API_BASE_URL } from '../stores/projectDetail'

type PathKey = 'crispasr_executable' | 'crispasr_model_dir'
type Paths = Record<PathKey, string>
interface Settings extends Paths {
  active: Paths
  managed_by_environment: Record<PathKey, boolean>
  restart_required: boolean
  editable: boolean
  allowed_roots: string[]
  revision: string
}
interface PathCheck { ok: boolean; message: string; version?: string | null }
interface CheckResult { executable: PathCheck; model_dir: PathCheck }

const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const saved = ref<Settings | null>(null)
const draft = reactive<Paths>({ crispasr_executable: '', crispasr_model_dir: '' })
const loading = ref(true)
const saving = ref(false)
const checking = ref(false)
const error = ref('')
const conflict = ref(false)
const notice = ref('')
const checkResult = ref<CheckResult | null>(null)
const fields: { key: PathKey; label: string; hint: string; result: keyof CheckResult }[] = [
  { key: 'crispasr_executable', label: 'CrispASR 可执行文件', hint: '例如 ./crispasr/crispasr 或 @root/1/crispasr。', result: 'executable' },
  { key: 'crispasr_model_dir', label: '模型目录', hint: '例如 ./crispasr/models 或 @root/1/models。', result: 'model_dir' },
]
const dirty = computed(() => !!saved.value && fields.some(({ key }) => draft[key] !== saved.value![key]))
const blank = computed(() => fields.some(({ key }) => !draft[key].trim()))
const busy = computed(() => loading.value || saving.value || checking.value)
const canSave = computed(() => !!saved.value?.editable && !busy.value && dirty.value && !blank.value && !conflict.value)
const saveReason = computed(() => {
  if (loading.value) return '正在读取全局设置。'
  if (!saved.value) return '读取成功后才能保存。'
  if (saving.value) return '正在保存配置，完成后可关闭。'
  if (checking.value) return '正在检查路径，检查完成后可保存。'
  if (!saved.value.editable) return '服务端配置文件不可写，当前设置只读。'
  if (conflict.value) return '服务端设置已更改，请重新加载后再保存。'
  if (blank.value) return '请填写两个路径后再保存。'
  if (!dirty.value) return '没有未保存修改。'
  return '有未保存修改。'
})

let alive = true
let loadGeneration = 0
let checkGeneration = 0
let saveGeneration = 0
let loadController: AbortController | undefined
let checkController: AbortController | undefined
let saveController: AbortController | undefined
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
const endpoint = `${API_BASE_URL}/settings/local-asr`

async function apiError(response: Response) {
  try {
    const payload = await response.json() as { error?: { message?: string } }
    return payload.error?.message || `请求失败（${response.status}），请重试。`
  } catch { return `请求失败（${response.status}），请重试。` }
}

function requestFailure(reason: unknown, fallback: string) {
  if (reason instanceof Error && !(reason instanceof TypeError) && !(reason instanceof SyntaxError) && /[\u3400-\u9fff]/.test(reason.message)) return reason.message
  return fallback
}

function readSettings(value: unknown): Settings {
  const result = value as Settings
  if (!result || !fields.every(({ key }) => typeof result[key] === 'string' && typeof result.active?.[key] === 'string' && typeof result.managed_by_environment?.[key] === 'boolean')
    || typeof result.editable !== 'boolean' || typeof result.restart_required !== 'boolean' || typeof result.revision !== 'string'
    || !Array.isArray(result.allowed_roots) || !result.allowed_roots.every((root) => typeof root === 'string')) {
    throw new Error('设置响应不完整，请检查后端版本后重试。')
  }
  return result
}

async function loadSettings(preserveDraft = false) {
  if (saving.value || checking.value) return
  const generation = ++loadGeneration
  loadController?.abort()
  loadController = new AbortController()
  const edited = fields.filter(({ key }) => saved.value && draft[key] !== saved.value[key]).map(({ key }) => key)
  loading.value = true
  error.value = ''
  notice.value = ''
  invalidateCheck()
  try {
    const response = await fetch(endpoint, { signal: loadController.signal })
    if (!response.ok) throw new Error(await apiError(response))
    const result = readSettings(await response.json())
    if (!alive || generation !== loadGeneration) return
    saved.value = result
    for (const { key } of fields) {
      if (!preserveDraft || !edited.includes(key) || result.managed_by_environment[key]) draft[key] = result[key]
    }
    conflict.value = false
    if (preserveDraft) notice.value = '已读取服务端最新设置，你的未保存修改仍保留，请检查后再保存。'
  } catch (reason) {
    if (!alive || generation !== loadGeneration || loadController.signal.aborted) return
    error.value = requestFailure(reason, '全局设置读取失败，请检查后端服务后重试。')
  } finally {
    if (alive && generation === loadGeneration) {
      loading.value = false
      await nextTick()
      if (document.activeElement === closeButton.value) dialog.value?.querySelector<HTMLInputElement>('input:not([readonly]):not([disabled])')?.focus()
    }
  }
}

function invalidateCheck() {
  ++checkGeneration
  checkController?.abort()
  checking.value = false
  checkResult.value = null
}

watch(() => [draft.crispasr_executable, draft.crispasr_model_dir], () => {
  invalidateCheck()
  notice.value = ''
}, { flush: 'sync' })

async function checkPaths() {
  if (!saved.value || busy.value || blank.value) return
  const generation = ++checkGeneration
  checkController = new AbortController()
  checking.value = true
  checkResult.value = null
  error.value = ''
  try {
    const response = await fetch(`${endpoint}/check`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...draft }), signal: checkController.signal })
    if (!response.ok) throw new Error(await apiError(response))
    const result = await response.json() as CheckResult
    if (!result || ![result.executable, result.model_dir].every((entry) => entry && typeof entry.ok === 'boolean' && typeof entry.message === 'string')) throw new Error('路径检查响应不完整，请重试。')
    if (alive && generation === checkGeneration) checkResult.value = result
  } catch (reason) {
    if (alive && generation === checkGeneration) error.value = requestFailure(reason, '路径检查失败，请检查后端连接后重试。')
  } finally {
    if (alive && generation === checkGeneration) checking.value = false
  }
}

async function saveSettings() {
  if (!canSave.value || !saved.value) return
  const generation = ++saveGeneration
  saveController = new AbortController()
  saving.value = true
  error.value = ''
  notice.value = ''
  invalidateCheck()
  // Environment-managed fields are always submitted with their saved values.
  const paths = Object.fromEntries(fields.map(({ key }) => [key, saved.value!.managed_by_environment[key] ? saved.value![key] : draft[key]])) as Paths
  try {
    const response = await fetch(endpoint, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...paths, revision: saved.value.revision }), signal: saveController.signal })
    if (!alive || generation !== saveGeneration) return
    if (!response.ok) {
      if (response.status === 409) conflict.value = true
      throw new Error(await apiError(response))
    }
    const result = readSettings(await response.json())
    if (!alive || generation !== saveGeneration) return
    saved.value = result
    for (const { key } of fields) draft[key] = result[key]
    notice.value = '设置已保存到后端配置文件。新路径将在手动重启后端后生效。'
  } catch (reason) {
    if (alive && generation === saveGeneration) error.value = requestFailure(reason, '设置保存失败，请检查后端连接后重试。')
  } finally {
    if (alive && generation === saveGeneration) saving.value = false
  }
}

function requestClose() {
  if (saving.value) return
  if (dirty.value && !window.confirm('全局设置有未保存修改，确定放弃并关闭吗？')) return
  emit('close')
}

function focusableElements() {
  return Array.from(dialog.value?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), [tabindex="0"]') || [])
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); requestClose(); return }
  if (event.key !== 'Tab') return
  const items = focusableElements()
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && (document.activeElement === first || !dialog.value?.contains(document.activeElement))) {
    event.preventDefault(); last?.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.value?.contains(document.activeElement))) {
    event.preventDefault(); first?.focus()
  }
}

function keepFocusInside(event: FocusEvent) {
  if (dialog.value && !dialog.value.contains(event.target as Node)) closeButton.value?.focus()
}

onMounted(() => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  closeButton.value?.focus()
  document.addEventListener('keydown', handleKeydown, true)
  document.addEventListener('focusin', keepFocusInside)
  void loadSettings()
})
onUnmounted(() => {
  alive = false
  ++loadGeneration; ++checkGeneration; ++saveGeneration
  loadController?.abort(); checkController?.abort(); saveController?.abort()
  document.removeEventListener('keydown', handleKeydown, true)
  document.removeEventListener('focusin', keepFocusInside)
  document.body.style.overflow = previousOverflow
  void nextTick(() => { if (previousFocus?.isConnected) previousFocus.focus() })
})
</script>

<template>
  <div class="modal-backdrop global-settings-backdrop" role="presentation" @click.self="requestClose">
    <section ref="dialog" class="config-modal global-settings-modal" role="dialog" aria-modal="true" aria-labelledby="global-settings-title" aria-describedby="global-settings-description">
      <div class="config-modal-header">
        <div><span class="section-kicker">WORKSPACE SETTINGS</span><h3 id="global-settings-title">全局设置</h3><p id="global-settings-description">配置后端使用的本地路径，适用于整个工作台。</p></div>
        <button ref="closeButton" class="modal-close" type="button" aria-label="关闭全局设置" :disabled="saving" @click="requestClose">×</button>
      </div>
      <div v-if="loading" class="settings-loading" role="status" aria-live="polite"><p>正在读取全局设置…</p><div class="settings-skeleton"></div><div class="settings-skeleton"></div></div>
      <div v-if="error" class="settings-feedback settings-error" role="alert"><p>{{ error }}</p><button v-if="!saved || conflict" class="secondary-button" type="button" :disabled="busy" @click="loadSettings(!!saved)">{{ conflict ? '重新加载，保留修改' : '重试读取' }}</button></div>
      <form v-if="saved && !loading" class="global-settings-form" @submit.prevent="saveSettings">
        <h4>本地语音识别</h4>
        <p class="settings-help">默认以相对路径或授权目录别名隐藏服务器个人目录前缀，不改变真实运行路径。相对路径以配置文件所在目录的父目录为起点；@root/N 对应后端授权根目录。</p>
        <p class="settings-help">后端运行在 WSL 时请使用 WSL/Linux 路径。也可自行输入后端绝对路径，保存成功后将显示脱敏表示。</p>
        <div class="settings-roots"><span>允许的根目录</span><ul v-if="saved.allowed_roots.length"><li v-for="path in saved.allowed_roots" :key="path"><code>{{ path }}</code></li></ul><p v-else>服务端未开放可用目录，请联系服务端配置管理员。</p></div>
        <p v-if="!saved.editable" class="settings-feedback">服务端配置文件不可写，当前设置只读。</p>
        <div v-for="field in fields" :key="field.key" class="settings-path-field">
          <label class="config-field" :for="`settings-${field.key}`"><span>{{ field.label }}<small v-if="saved.managed_by_environment[field.key]" class="settings-source">环境变量管理 · 只读</small></span>
            <input :id="`settings-${field.key}`" v-model="draft[field.key]" type="text" :readonly="!saved.editable || saved.managed_by_environment[field.key]" :disabled="saving" :aria-describedby="`settings-${field.key}-hint`" autocomplete="off" spellcheck="false" />
          </label>
          <p :id="`settings-${field.key}-hint`" class="settings-help">{{ field.hint }}<template v-if="saved.managed_by_environment[field.key]"> 此值由后端环境变量覆盖，请在服务端修改环境变量并重启。</template></p>
          <p v-if="saved.active[field.key] !== saved[field.key]" class="settings-active"><span>当前生效</span><code>{{ saved.active[field.key] || '未配置' }}</code><small>输入框显示已保存值，重启后端后生效。</small></p>
          <p v-if="checkResult" class="settings-check-result" :class="{ 'settings-check-failed': !checkResult[field.result].ok }" role="status"><span>{{ checkResult[field.result].ok ? '✓' : '!' }}</span> {{ checkResult[field.result].message }}<template v-if="checkResult[field.result].version"> · {{ checkResult[field.result].version }}</template></p>
        </div>
        <div class="settings-check-action"><button class="secondary-button" type="button" :disabled="busy || blank" @click="checkPaths">{{ checking ? '正在检查路径…' : '检查路径' }}</button><p class="settings-help">检查可执行文件和目录是否可用，不会保存设置或下载模型。</p></div>
        <p class="settings-restart-note">保存不会影响当前任务；新路径需重启后端生效。请等待运行任务结束后再手动重启。</p>
        <p v-if="saved.restart_required" class="settings-restart-pending" role="status">已保存配置与当前运行值不同，等待重启后端。</p>
        <p v-if="notice" class="settings-feedback settings-success" role="status">{{ notice }}</p>
        <div class="config-modal-footer"><span id="settings-save-reason">{{ saveReason }}</span><div><button class="secondary-button" type="button" :disabled="saving" @click="requestClose">取消</button><button class="primary-button" type="submit" :disabled="!canSave" aria-describedby="settings-save-reason">{{ saving ? '保存中…' : '保存设置' }}</button></div></div>
      </form>
      <div v-else-if="!loading" class="config-modal-footer"><span>读取成功后才能检查和保存路径。</span><button class="secondary-button" type="button" @click="requestClose">关闭</button></div>
    </section>
  </div>
</template>
