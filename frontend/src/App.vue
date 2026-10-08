<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore, type Stage, type StageName } from './stores/projectDetail'
import { useUploadQueue, type UploadPolicy, type UploadStatus } from './composables/useUploadQueue'

type View = 'list' | 'create' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const view = ref<View>('list')
const projectName = ref('')
const sourceLanguage = ref('ja')
const upload = useUploadQueue((file, name, language) => store.createProject(file, name, language), (id, file, language) => store.addAudio(id, file, language))
const { items: uploadItems, busy: uploadBusy, feedback: uploadFeedback, policy: uploadPolicy, validCount, readyCount, createdCount, failedCount, pendingCount } = upload
const policyLoading = ref(false)
const policyError = ref('')
const uploadAccept = computed(() => uploadPolicy.value?.allowed_extensions.map((extension) => `.${extension}`).join(',') || '')
const uploadSubmitLabel = computed(() => {
  if (uploadBusy.value) return '正在上传音频…'
  if (readyCount.value) return `上传 ${readyCount.value} 条音频到${upload.targetProjectId.value ? '此项目' : '新项目'}`
  if (failedCount.value) return '请重试失败文件'
  if (createdCount.value) return '上传完成'
  return '等待有效文件'
})
const fileInput = ref<HTMLInputElement | null>(null)
const selectedStage = ref<StageName>('preprocess')

const stageList: { key: StageName; label: string; shortLabel: string; description: string }[] = [
  { key: 'preprocess', label: '预处理', shortLabel: '源音频', description: '清理音频、统一采样率并准备识别输入。' },
  { key: 'asr', label: '语音识别', shortLabel: '识别', description: '将语音转换为带时间轴的原始字幕。' },
  { key: 'alignment', label: '时间轴对齐', shortLabel: '对齐', description: '校准每个片段的起止时间，让字幕更贴合声音。' },
  { key: 'translation', label: '翻译', shortLabel: '翻译', description: '根据源语言上下文生成目标语言字幕。' },
  { key: 'export', label: '导出', shortLabel: '交付', description: '整理字幕和媒体文件，准备下载。' },
]

const waveformBars = [34, 54, 42, 76, 62, 89, 47, 68, 36, 58, 82, 50, 72, 45, 63, 92, 52, 77, 39, 61, 84, 48, 70, 55, 88, 43, 66, 31, 57, 74, 46, 81, 52, 69, 40, 63]

const pageTitle = computed(() => {
  if (view.value === 'create') return upload.targetProjectId.value ? `添加音频 · ${upload.targetName.value}` : '新建项目'
  if (view.value === 'detail') return detail.project?.name || '项目详情'
  return '我的项目'
})

const pageSubtitle = computed(() => {
  if (view.value === 'create') return '多个音频归入同一个项目，上传完成后配置并选择处理范围。'
  if (view.value === 'detail') return '选择音频查看详情，勾选一条、多条或全部音频应用工作流。'
  return '把音频变成可交付的字幕文件。'
})

const currentError = computed(() => (view.value === 'detail' ? detail.error : view.value === 'create' ? '' : store.error))
const selectedStageMeta = computed(() => stageList.find((item) => item.key === selectedStage.value) || stageList[0])
const selectedStageValue = computed(() => stageState(selectedStage.value))
const selectedStagePercent = computed(() => Math.round(selectedStageValue.value.progress * 100))
const selectedStageIndex = computed(() => stageList.findIndex((item) => item.key === selectedStage.value))
const selectedStageLog = computed(() => {
  const stage = selectedStageValue.value
  if (stage.error) return stage.error
  if (stage.status === 'completed') return `${selectedStageMeta.value.label}已完成，结果已保存。`
  if (stage.status === 'running') return `正在执行${selectedStageMeta.value.label}，进度会实时更新。`
  if (stageVisualStatus(selectedStage.value) === 'cancelled') return '任务已取消，可从上次进度继续。'
  return `等待${selectedStageMeta.value.label}开始。`
})

const configModalOpen = ref(false)
const configModalStage = ref<StageName>('preprocess')
const configModalSnapshot = ref<ReturnType<typeof cloneConfig> | null>(null)
const configModalInitialSerialized = ref('')
const configModalWasDirty = ref(false)
const configModalStageMeta = computed(() => stageList.find((item) => item.key === configModalStage.value) || stageList[0])
const configModalStatus = computed(() => stageVisualStatus(configModalStage.value))

function cloneConfig() {
  return detail.config ? JSON.parse(JSON.stringify(detail.config)) as NonNullable<typeof detail.config> : null
}

function serializeConfig() {
  return JSON.stringify(detail.config)
}

function stageConfigSummary(key: StageName) {
  const config = detail.config
  if (!config) return '配置加载中'
  if (key === 'preprocess') return `${config.preprocess.provider.toUpperCase()} · ${config.preprocess.sample_rate / 1000} kHz`
  if (key === 'asr') return `${config.asr.provider.toUpperCase()} · ${config.asr.model || config.asr.backend}`
  if (key === 'alignment') return config.alignment.enabled ? `${config.alignment.provider.toUpperCase()} · ${config.alignment.model}` : '已关闭，沿用识别时间轴'
  if (key === 'translation') return `${config.translation.provider.toUpperCase()} · ${config.translation.model}`
  return `${config.export.formats.map((format) => format.toUpperCase()).join(' / ')} · ${config.export.bilingual ? '双语' : '单语'}`
}

function openStageConfig(key: StageName) {
  if (!detail.config || detail.loading || detail.configLoading || detail.actionLoading || detail.configSaving) return
  selectedStage.value = key
  configModalStage.value = key
  configModalSnapshot.value = cloneConfig()
  configModalInitialSerialized.value = serializeConfig()
  configModalWasDirty.value = detail.configDirty
  configModalOpen.value = true
}

function hasModalChanges() {
  return configModalOpen.value && configModalInitialSerialized.value !== serializeConfig()
}

function restoreModalSnapshot() {
  if (configModalSnapshot.value) detail.config = configModalSnapshot.value
  detail.configDirty = configModalWasDirty.value
  detail.validateCurrentConfig()
}

function closeStageConfig(force = false) {
  if (!configModalOpen.value) return true
  if (!force && hasModalChanges() && !window.confirm('当前节点有未保存修改，确定关闭吗？')) return false
  if (!force) restoreModalSnapshot()
  configModalOpen.value = false
  configModalSnapshot.value = null
  return true
}

async function saveStageConfig() {
  try {
    await detail.saveConfig()
    closeStageConfig(true)
  } catch {
    // The store exposes the concrete validation or server error in the alert.
  }
}

function syncTranslationTarget() {
  if (!detail.config) return
  detail.config.translation.target_language = detail.config.target_language
  detail.markConfigDirty()
}

function chooseFile() {
  if (uploadBusy.value) return
  fileInput.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  upload.add(Array.from(input.files || []))
  input.value = ''
}

function handleDrop(event: DragEvent) {
  event.preventDefault()
  upload.add(Array.from(event.dataTransfer?.files || []))
}

async function loadUploadPolicy() {
  if (uploadBusy.value || policyLoading.value) return
  policyLoading.value = true
  policyError.value = ''
  try {
    const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/+$/, '')
    const response = await fetch(`${base}/upload-policy`)
    if (!response.ok) throw new Error()
    const policy = await response.json() as UploadPolicy
    if (!Array.isArray(policy.allowed_extensions) || !policy.allowed_extensions.every((value) => typeof value === 'string') || !Number.isFinite(policy.max_upload_bytes) || policy.max_upload_bytes <= 0) throw new Error()
    upload.setPolicy(policy)
  } catch {
    policyError.value = '无法获取上传限制，请检查后端服务后重试。'
  } finally {
    policyLoading.value = false
  }
}

function uploadStatusLabel(status: UploadStatus) {
  return { invalid: '校验失败', ready: '可上传', uploading: '上传中', created: '已上传', failed: '上传失败' }[status]
}

function clearUploadQueue() {
  if (uploadBusy.value) return
  if (uploadItems.value.length && !window.confirm('清空此上传队列并开始新项目吗？已上传的音频会保留在原项目中。')) return
  upload.clear()
  projectName.value = ''
}

function guardUploadNavigation() {
  return !uploadBusy.value && !detail.actionLoading && !detail.configSaving
}

function guardUnload(event: BeforeUnloadEvent) {
  if (!uploadBusy.value) return
  event.preventDefault()
  event.returnValue = ''
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
}

function projectAudioCount(project: Project) { return project.audios?.length || 1 }
function projectAudioSize(project: Project) { return project.audios?.reduce((sum, audio) => sum + audio.source.size_bytes, 0) ?? project.source.size_bytes }

function formatSize(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatOutputSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatTime(value?: number | null) {
  if (value === null || value === undefined) return '—'
  const minutes = Math.floor(value / 60)
  const seconds = value - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(1).padStart(4, '0')}`
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = { pending: '等待开始', running: '处理中', cancelling: '正在取消', cancelled: '已取消', completed: '已完成', failed: '处理失败' }
  return labels[status || 'pending'] || status || '等待开始'
}

function stageStatusLabel(status?: string) {
  const labels: Record<string, string> = { pending: '待处理', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消' }
  return labels[status || 'pending'] || status || '待处理'
}

function stageState(key: StageName): Stage {
  return detail.job?.stages[key] || { status: 'pending', progress: 0 }
}

function stageVisualStatus(key: StageName) {
  const state = stageState(key)
  if (detail.job?.status === 'cancelled' && state.status === 'pending') return 'cancelled'
  return state.status
}

function stageIcon(key: StageName) {
  const status = stageVisualStatus(key)
  if (status === 'completed') return '✓'
  if (status === 'failed') return '!'
  if (status === 'cancelled') return '×'
  return String(stageList.findIndex((item) => item.key === key) + 1).padStart(2, '0')
}

function stageMetric(key: StageName) {
  const segments = detail.transcript?.segments.length || 0
  if (key === 'preprocess') return detail.activeAudio ? formatSize(detail.activeAudio.source.size_bytes) : '—'
  if (key === 'asr') return segments ? `${segments} 条片段` : '等待字幕'
  if (key === 'alignment') return detail.transcript?.duration ? formatTime(detail.transcript.duration) : '时间轴待生成'
  if (key === 'translation') return segments ? `${detail.transcript?.segments.filter((segment) => segment.translated_text).length || 0} 条译文` : '等待识别'
  return detail.outputs.length ? `${detail.outputs.length} 个文件` : '等待导出'
}

async function submitProject() {
  await upload.submit({ name: projectName.value, language: sourceLanguage.value })
  if (createdCount.value && !failedCount.value && !uploadItems.value.some((item) => item.status === 'invalid') && upload.targetProjectId.value) await openProject(upload.targetProjectId.value)
}

async function retryUpload(id: number) {
  await upload.retry(id)
  if (createdCount.value && !failedCount.value && !uploadItems.value.some((item) => item.status === 'invalid') && upload.targetProjectId.value) await openProject(upload.targetProjectId.value)
}

function cancelCreate() {
  if (!guardUploadNavigation()) return
  view.value = 'list'
  store.error = ''
}

function showCreate() {
  if (!guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  if (uploadItems.value.some((item) => item.status !== 'created') && !window.confirm('新建项目会清空当前未完成的上传队列。已上传音频会保留在原项目中，继续新建吗？')) return
  upload.clear()
  projectName.value = ''
  sourceLanguage.value = 'ja'
  detail.stop()
  view.value = 'create'
}

function returnUploadQueue() {
  if (!guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  detail.stop()
  view.value = 'create'
}

async function openProject(id: string, audioId?: string) {
  if (!guardUploadNavigation()) return
  closeStageConfig(true)
  view.value = 'detail'
  selectedStage.value = 'preprocess'
  try {
    await detail.load(id, audioId)
    if (detail.job?.current_stage) selectedStage.value = detail.job.current_stage
    else if (detail.job?.status === 'completed') selectedStage.value = 'export'
    else if (detail.job?.error?.stage) selectedStage.value = detail.job.error.stage
  } catch {
    // The store exposes a user-facing error while keeping the detail view open.
  }
}

function addProjectAudio() {
  if (!detail.project || !guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  if (uploadItems.value.length && !window.confirm('替换当前上传队列，向此项目添加音频吗？已上传的音频会保留。')) return
  upload.clear()
  upload.setTarget(detail.project.id, detail.project.name)
  projectName.value = detail.project.name
  sourceLanguage.value = detail.activeAudio?.source.language || detail.project.source.language
  detail.stop()
  view.value = 'create'
}

async function focusAudio(id: string) {
  if (detail.actionLoading || detail.configSaving || id === detail.activeAudioId) return
  if (configModalOpen.value && !closeStageConfig()) return
  if (detail.configDirty && !window.confirm('当前音频有未保存配置，切换后会放弃修改。继续切换吗？')) return
  await detail.selectAudio(id)
  selectedStage.value = detail.job?.current_stage || 'preprocess'
}

function showList() {
  if (!guardUploadNavigation()) return
  if (configModalOpen.value && !closeStageConfig()) return
  detail.stop()
  view.value = 'list'
  detail.error = ''
  void store.fetchProjects()
}

function selectStage(key: StageName) {
  selectedStage.value = key
}

function toggleExportFormat(format: string) {
  if (!detail.config || detail.configLocked) return
  const formats = detail.config.export.formats
  detail.config.export.formats = formats.includes(format)
    ? formats.filter((item) => item !== format)
    : [...formats, format]
  detail.markConfigDirty()
}

async function runJob() {
  try {
    const resume = ['failed', 'cancelled', 'interrupted'].includes(detail.job?.status || '')
    if (!resume) await detail.saveConfig()
    await detail.run(resume)
  } catch { /* The store keeps the translated API error. */ }
}

async function cancelJob() {
  try { await detail.cancel() } catch { /* The store keeps the translated API error. */ }
}

async function deleteProject(id: string, name: string) {
  if (!window.confirm(`确定删除项目“${name}”吗？这会移除项目内全部音频、字幕和输出文件。`)) return
  try {
    await store.deleteProject(id)
    if (detail.project?.id === id) showList()
  } catch (reason) {
    store.error = reason instanceof Error ? reason.message : '项目删除失败'
  }
}

onMounted(() => {
  void store.fetchProjects()
  void loadUploadPolicy()
  window.addEventListener('beforeunload', guardUnload)
})
onUnmounted(() => {
  detail.stop()
  window.removeEventListener('beforeunload', guardUnload)
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="showList"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>VoiceTransl</span></a>
      <span class="topbar-divider" aria-hidden="true"></span><span class="topbar-caption">语音翻译工作台</span><div class="topbar-status"><span class="status-dot"></span>服务已连接</div>
    </header>

    <main class="main-content">
      <section class="page-heading"><div><p class="eyebrow">VOICE / TRANSLATE</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><div v-if="view !== 'create'" class="heading-actions"><button class="primary-button" type="button" aria-label="新建项目" @click="showCreate"><span aria-hidden="true">＋</span> 新建项目</button><button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button" type="button" aria-label="返回上传队列" @click="returnUploadQueue">返回上传队列</button></div></section>

      <div v-if="currentError" class="alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" aria-label="关闭提示" @click="view === 'detail' ? (detail.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">PROJECTS</span><h2>项目列表</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span> 刷新</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>还没有项目</h3><p>上传音频后，项目会显示在这里。</p><button class="secondary-button" type="button" @click="showCreate">创建第一个项目</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" aria-label="删除项目" @click.stop="deleteProject(project.id, project.name)">删除</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="project.source.original_name">{{ project.source.original_name }}</p><div class="project-meta"><span>{{ project.source.language.toUpperCase() }}</span><span>{{ projectAudioCount(project) }} 条音频 · {{ formatSize(projectAudioSize(project)) }}</span><span class="project-preset">{{ project.preset }}</span></div></article></div>
      </section>

      <section v-else-if="view === 'create'" class="create-layout">
        <div class="create-copy"><span class="section-kicker">NEW PROJECT</span><h2>让音频开始流动。</h2><p>音频保存在同一个项目中。上传后逐条配置，或把当前工作流应用到勾选的音频，再批量开始处理。</p><div class="mini-pipeline"><span v-for="stage in stageList" :key="stage.key">{{ stage.shortLabel }}</span></div></div>
        <form class="create-form" :aria-busy="uploadBusy" @submit.prevent="submitProject">
          <div class="form-heading"><span class="section-kicker">SOURCE</span><h3>项目资料</h3></div>
          <label class="field-label" for="project-name">项目名称 <span>{{ upload.targetProjectId.value ? '上传到现有项目' : '所有音频共用，可选' }}</span></label>
          <input id="project-name" v-model="projectName" class="text-input" type="text" :disabled="uploadBusy || !!upload.targetProjectId.value" placeholder="留空时使用第一个有效文件名" />
          <label class="field-label" for="source-language">源语言</label>
          <select id="source-language" v-model="sourceLanguage" class="text-input" :disabled="uploadBusy"><option value="ja">日语</option><option value="en">英语</option><option value="zh">中文</option><option value="ko">韩语</option></select>
          <label class="field-label" for="audio-picker">音频文件</label>
          <button id="audio-picker" class="upload-zone" type="button" :disabled="uploadBusy" @click="chooseFile" @dragover.prevent @drop="handleDrop"><span class="upload-icon" aria-hidden="true">↑</span><span><strong>{{ uploadItems.length ? '继续添加音频' : '点击选择一个或多个音频' }}</strong><small>支持多文件拖放</small></span></button>
          <p v-if="uploadPolicy" class="file-hint">{{ uploadPolicy.allowed_extensions.map((extension) => extension.toUpperCase()).join(' / ') }} · 每个文件最多 {{ formatSize(uploadPolicy.max_upload_bytes) }}</p>
          <p v-else-if="policyLoading" class="file-hint" role="status">正在获取后端上传限制…</p>
          <div v-if="policyError" class="upload-policy-error" role="alert"><span>{{ policyError }}</span><button class="text-button" type="button" :disabled="policyLoading || uploadBusy" @click="loadUploadPolicy">重试加载限制</button></div>
          <p v-if="uploadFeedback" class="upload-feedback" role="status">{{ uploadFeedback }}</p>
          <section v-if="uploadItems.length" class="upload-queue" aria-labelledby="upload-queue-title">
            <div class="upload-queue-heading"><h4 id="upload-queue-title">{{ validCount }} 个有效 / {{ uploadItems.length }} 个已选</h4><button class="text-button" type="button" :disabled="uploadBusy" @click="clearUploadQueue">清空队列</button></div>
            <p class="upload-summary" role="status" aria-live="polite">{{ createdCount }} 条已上传 · {{ pendingCount }} 条待完成 · {{ failedCount }} 条上传失败 · {{ uploadItems.length - validCount }} 个校验失败</p>
            <ul class="upload-list">
              <li v-for="item in uploadItems" :key="item.id" class="upload-row" :class="`is-${item.status}`">
                <div class="upload-file-copy"><strong :title="item.file.name">{{ item.file.name }}</strong><span>{{ formatSize(item.file.size) }}</span><span v-if="item.snapshot">项目名称：{{ item.snapshot.name }} · 源语言：{{ item.snapshot.language.toUpperCase() }}</span></div>
                <span class="upload-status" :class="`is-${item.status}`">{{ uploadStatusLabel(item.status) }}</span>
                <div class="upload-row-actions"><button v-if="item.projectId" class="text-button" type="button" :disabled="uploadBusy" @click="openProject(item.projectId, item.audioId)">打开项目</button><button v-if="item.status === 'failed'" class="text-button" type="button" :disabled="uploadBusy" :aria-label="`重试上传 ${item.file.name}`" @click="retryUpload(item.id)">重试此文件</button><button v-if="item.status !== 'created'" class="text-button" type="button" :disabled="uploadBusy" :aria-label="`移除 ${item.file.name}`" @click="upload.remove(item.id)">移除</button></div>
                <p v-if="item.error" class="upload-item-error">{{ item.error }}</p>
              </li>
            </ul>
            <p class="file-hint">所有音频进入同一个项目。重复选择会提示，同名文件作为不同音频保存。</p>
            <p v-if="createdCount" class="upload-result-note">音频已上传，尚未开始处理。打开项目配置工作流；返回列表后仍可回到此队列。</p>
          </section>
          <p v-if="uploadBusy" class="upload-lock-note" role="status">正在逐个上传，请等待队列完成后离开。文件、项目名称和源语言已固定。</p>
          <div class="form-actions"><button class="secondary-button" type="button" :disabled="uploadBusy" @click="cancelCreate">返回列表</button><button v-if="upload.targetProjectId.value" class="secondary-button" type="button" :disabled="uploadBusy" @click="openProject(upload.targetProjectId.value)">打开项目</button><button class="primary-button" type="submit" :disabled="!readyCount || uploadBusy || !uploadPolicy"><span v-if="uploadBusy" class="button-loader" aria-hidden="true"></span>{{ uploadSubmitLabel }}</button></div>
          <input ref="fileInput" class="visually-hidden" type="file" multiple :accept="uploadAccept" :disabled="uploadBusy" @change="handleFileChange" />
        </form>
      </section>

      <section v-else class="workspace">
        <div class="workspace-header"><button class="back-button" type="button" @click="showList">← 返回项目列表</button><div class="workspace-actions"><button class="secondary-button danger-button" type="button" :disabled="detail.anyRunning || detail.actionLoading" @click="deleteProject(detail.project?.id || '', detail.project?.name || '')">删除项目</button><button v-if="!detail.isRunning" class="primary-button" type="button" :disabled="detail.loading || detail.actionLoading || detail.configLocked || !detail.configValid || detail.configLoading || detail.configSaving" @click="runJob"><span v-if="detail.actionLoading" class="button-loader" aria-hidden="true"></span>{{ detail.job?.status === 'completed' ? '重新处理当前音频' : detail.job?.status === 'pending' ? '开始当前音频' : '继续当前音频' }}</button><button v-if="detail.isRunning" class="secondary-button danger-button" type="button" :disabled="detail.actionLoading" @click="cancelJob">{{ detail.actionLoading ? '取消中…' : '取消当前音频' }}</button></div></div>
        <div v-if="detail.loading && !detail.project" class="workspace-skeleton"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        <template v-else-if="detail.project">
          <section class="audio-library" aria-labelledby="audio-library-title">
            <div class="panel-heading"><div><span class="section-kicker">AUDIO LIBRARY</span><h3 id="audio-library-title">项目音频 · {{ detail.audios.length }} 条</h3><p class="config-overview-note">点击文件名查看详情；勾选控制应用配置和开始处理的范围。</p></div><button class="secondary-button" type="button" :disabled="detail.actionLoading || detail.configSaving" @click="addProjectAudio">添加音频</button></div>
            <div class="audio-selection-bar"><label class="check-option"><input type="checkbox" :checked="!!detail.audios.length && detail.selectedAudioIds.length === detail.audios.length" :indeterminate="detail.selectedAudioIds.length > 0 && detail.selectedAudioIds.length < detail.audios.length" :disabled="detail.actionLoading || detail.configSaving" @change="detail.selectAll(($event.target as HTMLInputElement).checked)" /><span>全选</span></label><span>已选 {{ detail.selectedAudioIds.length }} / {{ detail.audios.length }} 条</span><button class="text-button" type="button" :disabled="detail.loading || detail.actionLoading" @click="detail.fetchAudios()">刷新状态</button></div>
            <ul class="audio-resource-list"><li v-for="audio in detail.audios" :key="audio.id" class="audio-resource-row" :class="{ 'is-active': audio.id === detail.activeAudioId }"><input v-model="detail.selectedAudioIds" type="checkbox" :value="audio.id" :aria-label="`选择音频 ${audio.source.original_name}`" :disabled="detail.actionLoading || detail.configSaving" /><button class="audio-focus-button" type="button" :disabled="detail.actionLoading || detail.configSaving" :aria-pressed="audio.id === detail.activeAudioId" @click="focusAudio(audio.id)"><strong>{{ audio.source.original_name }}</strong><small>{{ audio.source.language.toUpperCase() }} · {{ formatSize(audio.source.size_bytes) }}<span v-if="audio.id === detail.activeAudioId"> · 当前查看</span></small></button><span class="audio-job-status" :class="`is-${audio.job.status}`">{{ audio.job.status === 'pending' && audio.job.config_snapshot ? '排队中' : statusLabel(audio.job.status) }} · {{ Math.round(audio.job.progress * 100) }}%</span></li></ul>
            <div class="audio-batch-actions"><button class="secondary-button" type="button" :disabled="detail.loading || detail.actionLoading || !detail.configValid || !detail.selectedAudioIds.length" @click="detail.applyConfigToSelected()">应用到所选音频（{{ detail.selectedAudioIds.length }}）</button><button class="primary-button" type="button" :disabled="detail.loading || detail.actionLoading || !detail.selectedAudioIds.length || detail.selectedConfigFailed" @click="detail.runSelected()">开始所选音频（{{ detail.selectedAudioIds.length }}）</button></div>
            <p class="file-hint">应用配置会复制当前查看音频的完整工作流，源语言自动匹配每条音频。开始所选音频使用各自已保存配置创建新任务；继续当前音频使用原任务快照。</p>
            <p v-if="detail.selectedConfigFailed" class="upload-item-error" role="alert">所选音频有配置应用失败项，请修正后重新应用，或取消勾选失败项再开始。</p>
            <ul v-if="detail.batchResults.length" class="audio-action-results" aria-live="polite"><li v-for="result in detail.batchResults" :key="result.audio_id" :class="{ 'is-error': result.error }"><strong>{{ detail.audios.find((audio) => audio.id === result.audio_id)?.source.original_name || result.audio_id }}</strong><span>{{ result.error?.message || (result.status === 'running' ? '已开始处理' : result.status === 'pending' ? '已加入处理队列' : result.status) }}</span></li></ul>
          </section>
          <p v-if="detail.loading" class="config-loading" role="status">正在加载当前音频的配置、进度与输出…</p>
          <section class="config-overview" aria-labelledby="config-title">
            <div class="panel-heading config-overview-heading"><div><span class="section-kicker">CONFIGURATION MAP</span><h3 id="config-title">节点配置</h3><p class="config-overview-note">当前音频：{{ detail.activeAudio?.source.original_name }}。节点弹窗只修改这条音频；点击“应用到所选音频”复制完整工作流。</p></div><div class="config-overview-actions"><span class="config-state" :class="{ 'is-valid': detail.configValid, 'is-locked': detail.configLocked }">{{ detail.configLocked ? '运行中只读' : detail.configValid ? '配置有效' : '需要修正' }}</span><button class="secondary-button compact-button" type="button" :disabled="detail.configLocked || !detail.configDirty || detail.configSaving" @click="detail.saveConfig">{{ detail.configSaving ? '保存中…' : '保存全部配置' }}</button></div></div>
            <div v-if="detail.configLoading" class="config-loading">正在加载当前音频配置…</div>
            <template v-else-if="detail.config">
              <div class="config-map">
                <article v-for="stage in stageList" :key="stage.key" class="config-tile" :class="{ 'is-invalid': !detail.configValid, 'is-locked': detail.configLocked }">
                  <div class="config-tile-head"><span class="config-tile-index">{{ String(stageList.findIndex((item) => item.key === stage.key) + 1).padStart(2, '0') }}</span><span class="config-tile-status" :class="`is-${stageVisualStatus(stage.key)}`">{{ stageStatusLabel(stageVisualStatus(stage.key)) }}</span></div>
                  <h4>{{ stage.label }}</h4><p>{{ stageConfigSummary(stage.key) }}</p>
                  <button class="config-tile-edit" type="button" :disabled="detail.configLocked" @click="openStageConfig(stage.key)">编辑配置 <span aria-hidden="true">↗</span></button>
                </article>
              </div>
              <ul v-if="detail.configErrors.length" class="config-errors"><li v-for="message in detail.configErrors" :key="message">{{ message }}</li></ul>
              <p class="config-footer-note">{{ detail.configDirty ? '有未保存的修改，保存后才能开始处理。' : '配置会在开始处理前固定为任务快照。' }}</p>
            </template>
          </section>
          <section class="workspace-title"><div><span class="section-kicker">{{ detail.activeAudio?.source.language.toUpperCase() }} · {{ detail.project.preset }}</span><h2>{{ detail.activeAudio?.source.original_name || '选择音频' }}</h2><p>{{ detail.project.name }} · {{ formatSize(detail.activeAudio?.source.size_bytes || 0) }}</p></div><div class="total-progress"><span>当前音频进度</span><strong>{{ detail.progressPercent }}%</strong><div class="progress-track"><span :style="{ width: `${detail.progressPercent}%` }"></span></div><small>{{ statusLabel(detail.job?.status) }}</small></div></section>
          <section class="pipeline-panel" aria-labelledby="pipeline-title"><div class="panel-heading"><div><span class="section-kicker">PIPELINE</span><h3 id="pipeline-title">处理进度</h3><p class="pipeline-hint">选择节点打开配置</p></div><span class="live-badge"><i></i>{{ detail.isRunning ? '实时同步' : '状态已保存' }}</span></div><div class="pipeline-scroll"><ol class="pipeline-list"><li v-for="(stage, index) in stageList" :key="stage.key" class="pipeline-step" :class="{ 'is-last': index === stageList.length - 1 }"><button class="pipeline-node" :class="[`is-${stageVisualStatus(stage.key)}`, { 'is-selected': selectedStage === stage.key }]" type="button" :aria-label="`打开${stage.label}配置`" :aria-pressed="selectedStage === stage.key" @click="openStageConfig(stage.key)"><span class="node-icon">{{ stageIcon(stage.key) }}</span><span class="node-copy"><strong>{{ stage.label }}</strong><small>{{ stageStatusLabel(stageVisualStatus(stage.key)) }}</small></span><span class="node-percent">{{ Math.round(stageState(stage.key).progress * 100) }}%</span></button><span v-if="index < stageList.length - 1" class="pipeline-connector" :class="{ 'is-complete': stageVisualStatus(stage.key) === 'completed' }" aria-hidden="true"></span></li></ol></div></section>
          <section class="inspector-grid"><article class="inspector-panel"><div class="inspector-heading"><div><span class="section-kicker">STAGE {{ String(selectedStageIndex + 1).padStart(2, '0') }}</span><h3>{{ selectedStageMeta.label }}</h3><p>{{ selectedStageMeta.description }}</p></div><span class="stage-badge" :class="`is-${stageVisualStatus(selectedStage)}`">{{ stageStatusLabel(stageVisualStatus(selectedStage)) }}</span></div><div class="metric-row"><div><span>阶段进度</span><strong>{{ selectedStagePercent }}%</strong></div><div><span>阶段结果</span><strong>{{ stageMetric(selectedStage) }}</strong></div><div><span>更新时间</span><strong>{{ detail.job?.updated_at ? formatDate(detail.job.updated_at) : '—' }}</strong></div></div><div class="timeline-block"><div class="timeline-heading"><span>音频时间轴</span><small>{{ detail.transcript?.duration ? formatTime(detail.transcript.duration) : '等待时间轴数据' }}</small></div><div class="waveform" :class="{ muted: stageVisualStatus(selectedStage) === 'pending' }"><span v-for="(height, index) in waveformBars" :key="index" :style="{ height: `${height}%` }"></span></div><div class="timeline-scale"><span>00:00</span><span>01:00</span><span>02:00</span><span>03:00</span></div></div><div class="log-block"><div class="log-heading"><span>处理日志</span><small>{{ detail.job?.updated_at ? new Date(detail.job.updated_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }}</small></div><p :class="{ 'is-error': !!selectedStageValue.error }"><i :class="`log-dot is-${stageVisualStatus(selectedStage)}`"></i>{{ selectedStageLog }}</p></div></article><aside class="transcript-panel"><div class="panel-heading"><div><span class="section-kicker">LIVE PREVIEW</span><h3>字幕预览</h3></div><span v-if="detail.transcript" class="count-label">{{ detail.transcript.segments.length }} 条</span></div><div v-if="!detail.transcript" class="preview-empty"><span class="empty-wave" aria-hidden="true">∿</span><h4>字幕还在路上</h4><p>语音识别完成后，片段会显示在这里。</p></div><div v-else class="subtitle-list"><div v-for="segment in detail.transcript.segments.slice(0, 6)" :key="segment.id" class="subtitle-row"><div class="subtitle-time">{{ formatTime(segment.start) }}<br /><span>{{ formatTime(segment.end) }}</span></div><div><p>{{ segment.source_text }}</p><p class="translated-line">{{ segment.translated_text || '等待翻译' }}</p></div></div><div v-if="detail.transcript.segments.length > 6" class="more-hint">还有 {{ detail.transcript.segments.length - 6 }} 条字幕…</div></div></aside></section>
          <section class="outputs-rail"><div class="panel-heading"><div><span class="section-kicker">DELIVERABLES</span><h3>输出文件</h3></div><span class="count-label">{{ detail.outputs.length }} 个文件</span></div><div v-if="!detail.outputs.length" class="outputs-empty">导出完成后，SRT、VTT 或媒体文件会出现在这里。</div><div v-else class="output-list"><a v-for="output in detail.outputs" :key="output.name" class="output-item" :href="output.download_url" download><span class="output-icon">↓</span><span><strong>{{ output.name }}</strong><small>{{ formatOutputSize(output.size_bytes) }}</small></span><span class="download-label">下载</span></a></div></section>
          <div v-if="configModalOpen" class="modal-backdrop" role="presentation" @click.self="closeStageConfig()">
            <section class="config-modal" role="dialog" aria-modal="true" :aria-labelledby="`config-modal-${configModalStage}`">
              <div class="config-modal-header"><div><span class="section-kicker">NODE {{ String(selectedStageIndex + 1).padStart(2, '0') }}</span><h3 :id="`config-modal-${configModalStage}`">{{ configModalStageMeta.label }}配置</h3><p>{{ configModalStageMeta.description }}</p></div><button class="modal-close" type="button" aria-label="关闭节点配置" @click="closeStageConfig()">×</button></div>
              <div v-if="detail.configLocked" class="modal-lock-note"><span aria-hidden="true">▣</span>任务运行或取消中，配置已锁定。完成后可以重新处理并修改。</div>
              <form v-if="detail.config" class="config-modal-form" @submit.prevent="saveStageConfig">
                <template v-if="configModalStage === 'preprocess'"><div class="modal-field-grid"><label class="config-field"><span>处理 Provider</span><select v-model="detail.config.preprocess.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="ffmpeg">FFmpeg（真实音频）</option></select></label><label class="config-field"><span>采样率</span><select v-model.number="detail.config.preprocess.sample_rate" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="8000">8 kHz</option><option :value="16000">16 kHz</option><option :value="44100">44.1 kHz</option><option :value="48000">48 kHz</option></select></label><label class="config-field"><span>声道数</span><select v-model.number="detail.config.preprocess.channels" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="1">单声道</option><option :value="2">双声道</option></select></label></div><label class="check-option modal-check"><input v-model="detail.config.preprocess.preserve_original" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>保留原始音频，便于后续重新处理</span></label></template>
                <template v-else-if="configModalStage === 'asr'"><div class="modal-field-grid"><label class="config-field"><span>识别 Provider</span><select v-model="detail.config.asr.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="crispasr">CrispASR（本机）</option></select></label><label class="config-field"><span>识别后端</span><select v-model="detail.config.asr.backend" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="qwen3-1.7b">Qwen3 ASR 1.7B</option><option value="whisper">Whisper</option><option value="mega-asr">Mega ASR</option><option value="voxtral">Voxtral</option></select></label><label class="config-field"><span>识别模型</span><input v-model="detail.config.asr.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>识别语言</span><input v-model="detail.config.asr.language" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>分段时长（秒）</span><input v-model.number="detail.config.asr.chunk_seconds" type="number" min="1" max="600" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label></div><label class="check-option modal-check"><input v-model="detail.config.asr.vad_enabled" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>启用语音活动检测（VAD）</span></label></template>
                <template v-else-if="configModalStage === 'alignment'"><label class="check-option modal-check"><input v-model="detail.config.alignment.enabled" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>启用时间轴对齐</span></label><div class="modal-field-grid"><label class="config-field"><span>对齐 Provider</span><input v-model="detail.config.alignment.provider" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>对齐模型</span><input v-model="detail.config.alignment.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>失败回退策略</span><select v-model="detail.config.alignment.fallback" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="keep_asr_timestamps">保留识别时间轴</option><option value="fail_job">让任务失败</option></select></label></div></template>
                <template v-else-if="configModalStage === 'translation'"><div class="modal-field-grid"><label class="config-field"><span>协议 Provider</span><select v-model="detail.config.translation.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="openai_chat">OpenAI-compatible Chat Completions</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option></select></label><label class="config-field"><span>模型</span><input v-model="detail.config.translation.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>目标语言</span><select v-model="detail.config.target_language" :disabled="detail.configLocked" @change="syncTranslationTarget"><option value="zh-CN">中文（简体）</option><option value="en">英语</option><option value="ja">日语</option><option value="ko">韩语</option></select></label><label class="config-field"><span>批量大小</span><input v-model.number="detail.config.translation.batch_size" type="number" min="1" max="128" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>温度</span><input v-model.number="detail.config.translation.temperature" type="number" min="0" max="2" step="0.1" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>请求超时（秒）</span><input v-model.number="detail.config.translation.timeout_seconds" type="number" min="1" max="3600" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label></div><label class="config-field modal-wide-field"><span>接口地址（可选）</span><input v-model="detail.config.translation.base_url" type="url" placeholder="例如：https://api.openai.com/v1" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field modal-wide-field"><span>API Key 环境变量名</span><input v-model="detail.config.translation.api_key_env" placeholder="例如：OPENAI_API_KEY（不会保存密钥值）" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field modal-wide-field"><span>翻译指令（可选）</span><textarea v-model="detail.config.translation.instructions" rows="4" placeholder="告诉模型术语、语气或格式要求" :disabled="detail.configLocked" @input="detail.markConfigDirty"></textarea></label></template>
                <template v-else><div class="modal-field-grid"><label class="config-field modal-wide-field"><span>文件命名方式</span><select v-model="detail.config.export.naming" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="language">按语言命名</option><option value="original">沿用源文件名</option></select></label></div><div class="format-picker"><span>导出格式</span><label v-for="format in ['srt', 'vtt', 'lrc']" :key="format" class="format-option"><input type="checkbox" :checked="detail.config.export.formats.includes(format)" :disabled="detail.configLocked" @change="toggleExportFormat(format)" /><span>{{ format.toUpperCase() }}</span></label></div><label class="check-option modal-check"><input v-model="detail.config.export.bilingual" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>导出双语字幕</span></label></template>
                <ul v-if="detail.configErrors.length" class="config-errors modal-errors"><li v-for="message in detail.configErrors" :key="message">{{ message }}</li></ul>
                <div class="config-modal-footer"><span>{{ hasModalChanges() ? '有未保存修改' : '当前节点配置已保存' }}</span><div><button class="secondary-button" type="button" @click="closeStageConfig()">取消</button><button class="primary-button" type="submit" :disabled="detail.configLocked || !hasModalChanges() || detail.configSaving">{{ detail.configSaving ? '保存中…' : '保存节点配置' }}</button></div></div>
              </form>
            </section>
          </div>
        </template>
      </section>
    </main>
  </div>
</template>
