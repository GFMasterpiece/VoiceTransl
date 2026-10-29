<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore, type Stage, type StageName } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import { useUploadQueue, type UploadPolicy, type UploadStatus } from './composables/useUploadQueue'
import GlobalSettingsModal from './components/GlobalSettingsModal.vue'
import AsrConfigFields from './components/AsrConfigFields.vue'
import WorkflowEditor from './components/WorkflowEditor.vue'
import { fetchCapabilities } from './workflow/capabilities'

type View = 'list' | 'create' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const workflowStore = useWorkflowStore()
const view = ref<View>('list')
const workspaceMode = ref<'pipeline' | 'workflow'>('pipeline')
const workflowEditor = ref<InstanceType<typeof WorkflowEditor> | null>(null)
const globalSettingsOpen = ref(false)
const exportFormats = ref<readonly { value: string; label: string }[]>([])
const exportNamingModes = ref<readonly { value: string; label: string }[]>([])
const exportCapabilitiesLoaded = ref(false)
const asrBackendDetails = ref<readonly { id: string; label: string; task: string }[]>([])
const currentExportNaming = computed(() => detail.config?.export.naming || '')
const capabilityError = ref('')
const projectName = ref('')
const sourceLanguage = ref('')
const targetLanguage = ref('')
const upload = useUploadQueue((file, name, language, target) => store.createProject(file, name, language, 'mock', target), (id, file, language) => store.addAudio(id, file, language))
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
const selectedStage = computed({ get: () => detail.selectedStage, set: (value: StageName) => { detail.selectedStage = value } })

const stageList: { key: StageName; label: string; shortLabel: string; description: string }[] = [
  { key: 'preprocess', label: '预处理', shortLabel: '源音频', description: '清理音频、统一采样率并准备识别输入。' },
  { key: 'asr', label: '语音识别', shortLabel: '识别', description: '将语音转换为带时间轴的原始字幕。' },
  { key: 'alignment', label: '时间轴对齐', shortLabel: '对齐', description: '校准每个片段的起止时间，让字幕更贴合声音。' },
  { key: 'translation', label: '翻译', shortLabel: '翻译', description: '根据源语言上下文生成目标语言字幕。' },
  { key: 'export', label: '导出', shortLabel: '交付', description: '整理字幕和媒体文件，准备下载。' },
]

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
const stageTranscript = computed(() => selectedStageValue.value.status === 'completed' && detail.stageDetail?.result?.kind === 'transcript' ? detail.stageDetail.result.transcript : null)
const stageMetadata = computed(() => selectedStageValue.value.status === 'completed' && detail.stageDetail?.result?.kind === 'preprocess' ? detail.stageDetail.result.data : null)
const stageOutputs = computed(() => selectedStageValue.value.status === 'completed' && detail.stageDetail?.result?.kind === 'outputs' ? detail.stageDetail.result.items : [])
const snapshotSummary = computed(() => {
  const config = detail.job?.config_snapshot?.[selectedStage.value]
  if (!config) return '尚未开始处理，没有任务配置快照。'
  if ('formats' in config) return `${config.formats.map((format) => format.toUpperCase()).join(' / ')} · ${config.bilingual ? '双语' : '单语'}`
  if ('enabled' in config && !config.enabled) return '已关闭，沿用识别时间轴'
  return [config.provider.toUpperCase(), 'model' in config ? config.model : `${config.sample_rate / 1000} kHz · ${config.channels} 声道`].join(' · ')
})
const configUnavailableReason = computed(() => {
  if (detail.loading || detail.configLoading) return '正在加载此音频的配置，加载完成后可查看。'
  if (!detail.config) return '此音频配置尚未加载，请刷新配置后再查看。'
  if (detail.actionLoading || detail.configSaving) return '正在保存配置或提交任务，完成后可查看。'
  return ''
})
const stageEmptyMessage = computed(() => {
  if (selectedStageValue.value.status === 'skipped') return '此阶段已跳过，没有独立产物；可查看后续阶段。'
  if (selectedStageValue.value.status === 'failed') return '此阶段处理失败；修正错误后继续当前音频。'
  if (selectedStageValue.value.status === 'running') return '正在处理此阶段，完成后会展示本阶段结果。'
  if (selectedStageValue.value.status === 'completed') return '本阶段没有可展示的结果，请刷新阶段详情。'
  return '此阶段尚未完成；配置工作流并开始当前音频后查看结果。'
})

function metadataValue(value: unknown) {
  return value === null ? '未记录' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

async function reloadStageConfig() {
  const audioId = detail.activeAudioId
  const projectId = detail.project?.id
  if (!audioId) return
  try {
    await detail.selectAudio(audioId)
  } catch (reason) {
    if (audioId !== detail.activeAudioId || projectId !== detail.project?.id) return
    detail.error = reason instanceof Error ? reason.message : '配置读取失败，请检查后端服务后重新加载。'
  }
}

function formatTimestamp(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '尚未记录'
}
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
const additionalParametersText = ref('{}')
const additionalParametersError = ref('')

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
  if (globalSettingsOpen.value) return
  if (!detail.config || detail.loading || detail.configLoading || detail.actionLoading || detail.configSaving) return
  selectedStage.value = key
  configModalStage.value = key
  configModalSnapshot.value = cloneConfig()
  configModalInitialSerialized.value = serializeConfig()
  configModalWasDirty.value = detail.configDirty
  additionalParametersText.value = JSON.stringify(detail.config.translation.additional_parameters || {}, null, 2)
  additionalParametersError.value = ''
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
  if (configModalStage.value === 'translation' && additionalParametersError.value) return
  try {
    await detail.saveConfig()
    closeStageConfig(true)
  } catch {
    // The store exposes the concrete validation or server error in the alert.
  }
}

function updateAsrConfig(patch: Partial<NonNullable<typeof detail.config>['asr']>) {
  if (!detail.config || detail.configLocked) return
  Object.assign(detail.config.asr, patch)
  detail.markConfigDirty()
}

function updateAdditionalParameters() {
  if (!detail.config || detail.configLocked) return
  try {
    const parsed = JSON.parse(additionalParametersText.value) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      additionalParametersError.value = '附加参数必须是 JSON 对象，例如 {"top_p": 0.9}。'
      return
    }
    detail.config.translation.additional_parameters = parsed as Record<string, unknown>
    additionalParametersError.value = ''
    detail.markConfigDirty()
  } catch {
    additionalParametersError.value = '附加参数不是有效 JSON，尚未写入配置。'
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
  const labels: Record<string, string> = { pending: '待处理', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已取消', skipped: '已跳过' }
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

async function submitProject() {
  if (!sourceLanguage.value.trim()) { upload.feedback.value = '请选择或输入源语言。'; return }
  if (!upload.targetProjectId.value && !targetLanguage.value.trim()) { upload.feedback.value = '请选择或输入目标语言。'; return }
  await upload.submit({ name: projectName.value, language: sourceLanguage.value, targetLanguage: targetLanguage.value })
  if (!createdCount.value && !failedCount.value && upload.feedback.value) return
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
  if (workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(showCreate, { resetPipeline: false })
    return
  }
  if (!guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  if (uploadItems.value.some((item) => item.status !== 'created') && !window.confirm('新建项目会清空当前未完成的上传队列。已上传音频会保留在原项目中，继续新建吗？')) return
  upload.clear()
  projectName.value = ''
  sourceLanguage.value = ''
  targetLanguage.value = ''
  detail.stop()
  view.value = 'create'
}

function returnUploadQueue() {
  if (workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(returnUploadQueue, { resetPipeline: false })
    return
  }
  if (!guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  detail.stop()
  view.value = 'create'
}

async function openProject(id: string, audioId?: string) {
  if (workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(() => { void openProject(id, audioId) }, { resetPipeline: false })
    return
  }
  workspaceMode.value = 'pipeline'
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
  if (workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(addProjectAudio, { resetPipeline: false })
    return
  }
  if (!detail.project || !guardUploadNavigation() || (configModalOpen.value && !closeStageConfig())) return
  if (uploadItems.value.length && !window.confirm('替换当前上传队列，向此项目添加音频吗？已上传的音频会保留。')) return
  upload.clear()
  upload.setTarget(detail.project.id, detail.project.name)
  projectName.value = detail.project.name
  sourceLanguage.value = detail.activeAudio?.source.language || detail.project.source.language
  targetLanguage.value = detail.config?.target_language || detail.project.source.language
  detail.stop()
  view.value = 'create'
}

async function focusAudio(id: string) {
  if (workspaceMode.value === 'workflow') {
    if (detail.actionLoading || detail.configSaving || id === detail.activeAudioId) return
    guardWorkflowNavigation(() => { void switchAudioKeepingWorkflow(id) }, { resetPipeline: false })
    return
  }
  if (detail.actionLoading || detail.configSaving || id === detail.activeAudioId) return
  if (configModalOpen.value && !closeStageConfig()) return
  if (detail.configDirty && !window.confirm('当前音频有未保存配置，切换后会放弃修改。继续切换吗？')) return
  await detail.selectAudio(id)
  selectedStage.value = detail.job?.current_stage || 'preprocess'
}

async function switchAudioKeepingWorkflow(id: string) {
  const target = detail.audios.find((audio) => audio.id === id)
  if (!target) return
  await detail.selectAudio(id)
  if (detail.activeAudioId === id) workspaceMode.value = 'workflow'
}

function showList() {
  if (view.value === 'detail' && workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(showList)
    return
  }
  if (!guardUploadNavigation()) return
  if (configModalOpen.value && !closeStageConfig()) return
  detail.stop()
  view.value = 'list'
  detail.error = ''
  void store.fetchProjects()
}

function setFilenameTemplate(event: Event) {
  if (!detail.config || detail.configLocked) return
  const value = (event.target as HTMLInputElement).value
  detail.config.export.filename_template = value.trim() ? value : null
  detail.markConfigDirty()
}

function textValue(event: Event) {
  return (event.target as HTMLSelectElement).value
}

function setExportNaming(value: string) {
  if (!detail.config || detail.configLocked) return
  if (!exportNamingModes.value.some((mode) => mode.value === value)) return
  detail.config.export.naming = value
  detail.markConfigDirty()
}

function toggleExportFormat(format: string) {
  if (!detail.config || detail.configLocked) return
  const formats = detail.config.export.formats
  detail.config.export.formats = formats.includes(format)
    ? formats.filter((item) => item !== format)
    : [...formats, format]
  detail.markConfigDirty()
}

function switchWorkspaceMode(mode: 'pipeline' | 'workflow') {
  if (mode === 'workflow' && (!detail.project || !detail.activeAudioId || detail.loading)) return
  if (mode === 'pipeline' && workspaceMode.value === 'workflow') {
    guardWorkflowNavigation(() => {}, { resetPipeline: false })
    return
  }
  if (mode === 'pipeline' && configModalOpen.value && !closeStageConfig()) return
  workspaceMode.value = mode
}

function guardWorkflowNavigation(action: () => void, { resetPipeline = true } = {}) {
  if (workflowStore.activeRun) {
    workflowStore.error = '节点运行尚未结束，请等待完成或取消后再离开。'
    return
  }
  const leaveWorkflow = () => {
    workflowStore.leaveEditor()
    workspaceMode.value = 'pipeline'
    if (resetPipeline) detail.stop()
  }
  if (workspaceMode.value !== 'workflow') {
    action()
    return
  }
  const proceed = () => {
    leaveWorkflow()
    action()
  }
  if (workflowEditor.value) {
    workflowEditor.value.requestClose(proceed)
    return
  }
  if (workflowStore.dirty && !window.confirm('放弃未保存的节点工作流修改并离开？')) return
  if (workflowStore.dirty) workflowStore.discardDraft()
  proceed()
}

function switchWorkflowAudio(event: Event) {
  const select = event.target as HTMLSelectElement
  const audioId = select.value
  if (audioId === detail.activeAudioId) return
  if (workflowStore.activeRun) {
    select.value = detail.activeAudioId || ''
    return
  }
  void focusAudio(audioId)
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
  if (workspaceMode.value === 'workflow' && detail.project?.id === id) {
    guardWorkflowNavigation(() => { void deleteProject(id, name) })
    return
  }
  if (!window.confirm(`确定删除项目“${name}”吗？这会移除项目内全部音频、字幕和输出文件。`)) return
  try {
    await store.deleteProject(id)
    if (detail.project?.id === id) {
      if (workspaceMode.value === 'workflow') {
        workflowStore.stop()
        workspaceMode.value = 'pipeline'
      }
      detail.stop()
      showList()
    }
  } catch (reason) {
    store.error = reason instanceof Error ? reason.message : '项目删除失败'
  }
}

onMounted(() => {
  void fetchCapabilities().then((caps) => {
    exportFormats.value = caps.export.formats
    exportNamingModes.value = caps.export.namingModes
    exportCapabilitiesLoaded.value = true
    asrBackendDetails.value = caps.asr.backendDetails
    capabilityError.value = ''
  }).catch((reason) => {
    exportCapabilitiesLoaded.value = true
    capabilityError.value = reason instanceof Error ? reason.message : '能力目录读取失败，导出选项不可用。'
  })
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
    <header class="topbar" :inert="globalSettingsOpen">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="showList"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>VoiceTransl</span></a>
      <span class="topbar-divider" aria-hidden="true"></span><span class="topbar-caption">语音翻译工作台</span><div class="topbar-status"><span class="status-dot"></span>服务已连接</div>
      <button class="topbar-settings" type="button" aria-label="全局设置" :disabled="configModalOpen" :title="configModalOpen ? '关闭节点配置后可打开全局设置' : '全局设置'" @click="globalSettingsOpen = true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m9.5 3-.5 2-2 .9-1.9-.6-2 3.4L4.6 10v2L3 13.4l2 3.5 2-.6 2 .9.5 2h4l.5-2 2-.9 2 .6 2-3.5-1.6-1.4v-2L20 8.7l-2-3.4-2 .6-2-.9-.5-2Z"/><circle cx="11.5" cy="11.1" r="3"/></svg><span>设置</span></button>
    </header>

    <main class="main-content" :inert="globalSettingsOpen">
      <section class="page-heading"><div><p class="eyebrow">VOICE / TRANSLATE</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><div v-if="view !== 'create'" class="heading-actions"><button class="primary-button" type="button" aria-label="新建项目" @click="showCreate"><span class="button-icon" aria-hidden="true">＋</span> 新建项目</button><button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button" type="button" aria-label="返回上传队列" @click="returnUploadQueue">返回上传队列</button></div></section>

      <div v-if="currentError" class="alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" aria-label="关闭提示" @click="view === 'detail' ? (detail.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">PROJECTS</span><h2>项目列表</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span> 刷新</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>还没有项目</h3><p>上传音频后，项目会显示在这里。</p><button class="secondary-button" type="button" @click="showCreate">创建第一个项目</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" aria-label="删除项目" @click.stop="deleteProject(project.id, project.name)">删除</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="project.source.original_name">{{ project.source.original_name }}</p><div class="project-meta"><span>{{ project.source.language.toUpperCase() }}</span><span>{{ projectAudioCount(project) }} 条音频 · {{ formatSize(projectAudioSize(project)) }}</span><span class="project-preset">{{ project.preset }}</span></div></article></div>
        <nav v-if="store.total > store.limit || store.offset > 0" class="pagination-bar" aria-label="项目分页"><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasPrevious" @click="store.previousPage()">上一页</button><span>第 {{ Math.floor(store.offset / store.limit) + 1 }} / {{ store.pageCount }} 页 · 共 {{ store.total }} 个项目</span><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasNext" @click="store.nextPage()">下一页</button></nav>
      </section>

      <section v-else-if="view === 'create'" class="create-layout">
        <div class="create-copy"><span class="section-kicker">NEW PROJECT</span><h2>让音频开始流动。</h2><p>音频保存在同一个项目中。上传后逐条配置，或把当前工作流应用到勾选的音频，再批量开始处理。</p><div class="mini-pipeline"><span v-for="stage in stageList" :key="stage.key">{{ stage.shortLabel }}</span></div></div>
        <form class="create-form" :aria-busy="uploadBusy" @submit.prevent="submitProject">
          <div class="form-heading"><span class="section-kicker">SOURCE</span><h3>项目资料</h3></div>
          <label class="field-label" for="project-name">项目名称 <span>{{ upload.targetProjectId.value ? '上传到现有项目' : '所有音频共用，可选' }}</span></label>
          <input id="project-name" v-model="projectName" class="text-input" type="text" :disabled="uploadBusy || !!upload.targetProjectId.value" placeholder="留空时使用第一个有效文件名" />
          <div class="modal-field-grid"><label class="config-field" for="source-language"><span>源语言 *</span><input id="source-language" v-model="sourceLanguage" class="text-input" list="language-codes" placeholder="请选择或输入语言代码，例如 ja" autocomplete="off" :disabled="uploadBusy || !!upload.targetProjectId.value" /><small>必须显式选择；接受 BCP 47 风格代码，如 ja、en、zh-CN。</small></label><label class="config-field" for="target-language"><span>目标语言 *</span><input id="target-language" v-model="targetLanguage" class="text-input" list="language-codes" placeholder="请选择或输入语言代码，例如 zh-CN" autocomplete="off" :disabled="uploadBusy || !!upload.targetProjectId.value" :title="upload.targetProjectId.value ? '添加到现有项目时继承项目目标语言' : ''" /><small v-if="upload.targetProjectId.value">添加到现有项目时继承项目目标语言，仅源语言按此音频独立设置。</small><small v-else>必须与源语言不同。</small></label></div><datalist id="language-codes"><option value="ja">日语</option><option value="en">英语</option><option value="zh-CN">中文（简体）</option><option value="ko">韩语</option></datalist>
          <label class="field-label" for="audio-picker">音频文件</label>
          <button id="audio-picker" class="upload-zone" type="button" :disabled="uploadBusy" @click="chooseFile" @dragover.prevent @drop="handleDrop"><span class="upload-icon" aria-hidden="true">↑</span><span><strong>{{ uploadItems.length ? '继续添加音频' : '点击选择一个或多个音频' }}</strong><small>支持多文件拖放</small></span></button>
          <p v-if="uploadPolicy" class="file-hint">{{ uploadPolicy.allowed_extensions.map((extension) => extension.toUpperCase()).join(' / ') }} · 每个文件最多 {{ formatSize(uploadPolicy.max_upload_bytes) }}</p>
          <p v-else-if="policyLoading" class="file-hint" role="status">正在获取后端上传限制…</p>
          <div v-if="policyError" class="upload-policy-error" role="alert"><span>{{ policyError }}</span><button class="text-button" type="button" :disabled="policyLoading || uploadBusy" @click="loadUploadPolicy">重试加载限制</button></div>
          <p v-if="uploadFeedback" class="upload-feedback" :class="{ 'settings-error': !readyCount || upload.feedback }" role="status">{{ uploadFeedback }}</p>
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
        <div class="workspace-header"><button class="back-button" type="button" @click="showList">← 返回项目列表</button><div class="workspace-actions"><label v-if="workspaceMode === 'workflow'" class="workflow-audio-picker"><span>当前节点音频</span><select :value="detail.activeAudioId || ''" :disabled="detail.actionLoading || detail.configSaving || !!workflowStore.activeRun" aria-label="切换节点工作流音频" @change="switchWorkflowAudio"><option v-for="audio in detail.audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }}</option></select></label><button class="secondary-button danger-button" type="button" :disabled="detail.anyRunning || detail.actionLoading || !!workflowStore.activeRun" @click="deleteProject(detail.project?.id || '', detail.project?.name || '')">删除项目</button><template v-if="workspaceMode === 'pipeline'"><button v-if="!detail.isRunning" class="primary-button" type="button" :disabled="detail.loading || detail.actionLoading || detail.configLocked || !detail.configValid || detail.configLoading || detail.configSaving" @click="runJob"><span v-if="detail.actionLoading" class="button-loader" aria-hidden="true"></span>{{ detail.job?.status === 'completed' ? '重新处理当前音频' : detail.job?.status === 'pending' ? '开始当前音频' : '继续当前音频' }}</button><button v-if="detail.isRunning" class="secondary-button danger-button" type="button" :disabled="detail.actionLoading" @click="cancelJob">{{ detail.actionLoading ? '取消中…' : '取消当前音频' }}</button></template></div></div>
        <div v-if="detail.loading && !detail.project" class="workspace-skeleton"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        <template v-else-if="detail.project">
          <nav class="workspace-mode-switch" aria-label="工作区模式">
            <button type="button" :class="{ 'is-active': workspaceMode === 'pipeline' }" :aria-pressed="workspaceMode === 'pipeline'" @click="switchWorkspaceMode('pipeline')">五阶段流程</button>
            <button type="button" :class="{ 'is-active': workspaceMode === 'workflow' }" :aria-pressed="workspaceMode === 'workflow'" :disabled="!detail.activeAudioId || detail.loading" @click="switchWorkspaceMode('workflow')">节点工作流 <span>音频分支</span></button>
          </nav>
          <WorkflowEditor v-if="workspaceMode === 'workflow' && detail.activeAudioId" :key="`${detail.project.id}/${detail.activeAudioId}`" ref="workflowEditor" :project-id="detail.project.id" :audio-id="detail.activeAudioId" :audio-name="detail.activeAudio?.source.original_name || '当前音频'" @show-pipeline="switchWorkspaceMode('pipeline')" />
          <template v-else>
          <section class="audio-library" aria-labelledby="audio-library-title">
            <div class="panel-heading"><div><span class="section-kicker">AUDIO LIBRARY</span><h3 id="audio-library-title">项目音频 · {{ detail.audios.length }} 条</h3><p class="audio-library-note">点击文件名查看详情；勾选控制应用配置和开始处理的范围。</p></div><button class="secondary-button" type="button" :disabled="detail.actionLoading || detail.configSaving" @click="addProjectAudio">添加音频</button></div>
            <div class="audio-selection-bar"><label class="check-option"><input type="checkbox" :checked="!!detail.audios.length && detail.selectedAudioIds.length === detail.audios.length" :indeterminate="detail.selectedAudioIds.length > 0 && detail.selectedAudioIds.length < detail.audios.length" :disabled="detail.actionLoading || detail.configSaving" @change="detail.selectAll(($event.target as HTMLInputElement).checked)" /><span>全选</span></label><span>已选 {{ detail.selectedAudioIds.length }} / {{ detail.audios.length }} 条</span><button class="text-button" type="button" :disabled="detail.loading || detail.actionLoading" @click="detail.fetchAudios()">刷新状态</button></div>
            <ul class="audio-resource-list"><li v-for="audio in detail.audios" :key="audio.id" class="audio-resource-row" :class="{ 'is-active': audio.id === detail.activeAudioId }"><input v-model="detail.selectedAudioIds" type="checkbox" :value="audio.id" :aria-label="`选择音频 ${audio.source.original_name}`" :disabled="detail.actionLoading || detail.configSaving" /><button class="audio-focus-button" type="button" :disabled="detail.actionLoading || detail.configSaving" :aria-pressed="audio.id === detail.activeAudioId" @click="focusAudio(audio.id)"><strong>{{ audio.source.original_name }}</strong><small>{{ audio.source.language.toUpperCase() }} · {{ formatSize(audio.source.size_bytes) }}<span v-if="audio.id === detail.activeAudioId"> · 当前查看</span></small></button><span class="audio-job-status" :class="`is-${audio.job.status}`">{{ audio.job.status === 'pending' && audio.job.config_snapshot ? '排队中' : statusLabel(audio.job.status) }} · {{ Math.round(audio.job.progress * 100) }}%</span></li></ul>
            <div class="audio-batch-actions"><button class="secondary-button" type="button" :disabled="detail.loading || detail.actionLoading || !detail.configValid || !detail.selectedAudioIds.length" @click="detail.applyConfigToSelected()">应用到所选音频（{{ detail.selectedAudioIds.length }}）</button><button class="primary-button" type="button" :disabled="detail.loading || detail.actionLoading || !detail.selectedAudioIds.length || detail.selectedConfigFailed" @click="detail.runSelected()">开始所选音频（{{ detail.selectedAudioIds.length }}）</button></div>
            <p class="file-hint">应用配置会复制当前查看音频的完整工作流，源语言自动匹配每条音频。开始所选音频使用各自已保存配置创建新任务；继续当前音频使用原任务快照。</p>
            <p v-if="detail.selectedConfigFailed" class="upload-item-error" role="alert">所选音频有配置应用失败项，请修正后重新应用，或取消勾选失败项再开始。</p>
            <ul v-if="detail.batchResults.length" class="audio-action-results" aria-live="polite"><li v-for="result in detail.batchResults" :key="result.audio_id" :class="{ 'is-error': result.error }"><strong>{{ detail.audios.find((audio) => audio.id === result.audio_id)?.source.original_name || result.audio_id }}</strong><span>{{ result.error?.message || (result.status === 'running' ? '已开始处理' : result.status === 'pending' ? '已加入处理队列' : result.status) }}</span></li></ul>
          </section>
          <p v-if="detail.loading" class="config-loading" role="status">正在加载当前音频的配置、进度与输出…</p>
          <section class="workspace-title"><div><span class="section-kicker">{{ detail.activeAudio?.source.language.toUpperCase() }} · {{ detail.project.preset }}</span><h2>{{ detail.activeAudio?.source.original_name || '选择音频' }}</h2><p>{{ detail.project.name }} · {{ formatSize(detail.activeAudio?.source.size_bytes || 0) }}</p></div><div class="total-progress"><span>当前音频进度</span><strong>{{ detail.progressPercent }}%</strong><div class="progress-track"><span :style="{ width: `${detail.progressPercent}%` }"></span></div><small>{{ statusLabel(detail.job?.status) }}</small></div></section>
          <section class="pipeline-panel" aria-labelledby="pipeline-title"><div class="panel-heading"><div><span class="section-kicker">PIPELINE</span><h3 id="pipeline-title">处理进度</h3><p class="pipeline-hint">选择节点查看阶段详情</p></div><span class="live-badge"><i></i>{{ detail.isRunning ? '实时同步' : '状态已保存' }}</span></div><div class="pipeline-scroll"><ol class="pipeline-list"><li v-for="(stage, index) in stageList" :key="stage.key" class="pipeline-step" :class="{ 'is-last': index === stageList.length - 1 }"><button class="pipeline-node" :class="[`is-${stageVisualStatus(stage.key)}`, { 'is-selected': selectedStage === stage.key }]" type="button" :aria-label="`选择${stage.label}阶段`" :aria-pressed="selectedStage === stage.key" aria-controls="stage-workspace" @click="selectedStage = stage.key"><span class="node-icon">{{ stageIcon(stage.key) }}</span><span class="node-copy"><strong>{{ stage.label }}</strong><small>{{ stageStatusLabel(stageVisualStatus(stage.key)) }}</small></span><span class="node-percent">{{ Math.round(stageState(stage.key).progress * 100) }}%</span></button><span v-if="index < stageList.length - 1" class="pipeline-connector" :class="{ 'is-complete': stageVisualStatus(stage.key) === 'completed' }" aria-hidden="true"></span></li></ol></div></section>
          <section id="stage-workspace" class="stage-workspace" :aria-labelledby="`stage-title-${selectedStage}`" :aria-busy="detail.stageDetailLoading">
            <header class="stage-workspace-heading">
              <div><span class="section-kicker">STAGE {{ String(selectedStageIndex + 1).padStart(2, '0') }}</span><h3 :id="`stage-title-${selectedStage}`">{{ selectedStageMeta.label }}</h3><p>{{ selectedStageMeta.description }}</p></div>
              <div class="stage-workspace-actions"><span class="stage-badge" :class="`is-${stageVisualStatus(selectedStage)}`">{{ stageStatusLabel(stageVisualStatus(selectedStage)) }}</span><button class="primary-button stage-config-button" type="button" :disabled="!!configUnavailableReason" aria-describedby="stage-config-note" @click="openStageConfig(selectedStage)">配置此阶段<span v-if="detail.configLocked"> · 只读</span></button></div>
            </header>
            <p id="stage-config-note" class="stage-config-note">{{ configUnavailableReason || (detail.configLocked ? '当前音频正在处理、取消或排队；配置可查看，字段只读。' : '配置入口只修改当前音频的下次处理配置；任务快照保持固定。') }}</p>
            <button v-if="!detail.config && !detail.loading && !detail.configLoading" class="secondary-button compact-button stage-config-retry" type="button" :disabled="detail.actionLoading || detail.configSaving" @click="reloadStageConfig">重新加载配置</button>
            <dl class="stage-metrics"><div><dt>阶段进度</dt><dd>{{ selectedStagePercent }}%</dd></div><div><dt>开始时间</dt><dd>{{ formatTimestamp(selectedStageValue.started_at) }}</dd></div><div><dt>结束时间</dt><dd>{{ formatTimestamp(selectedStageValue.finished_at) }}</dd></div></dl>
            <p class="stage-state-note" :class="{ 'is-error': !!selectedStageValue.error || detail.job?.error?.stage === selectedStage }"><strong>阶段状态说明</strong> {{ selectedStageValue.error || (detail.job?.error?.stage === selectedStage ? detail.job.error.message : selectedStageLog) }}</p>
            <div class="stage-config-summary">
              <div><span>本次任务快照</span><strong>{{ snapshotSummary }}</strong><small v-if="detail.job?.config_snapshot">任务 {{ detail.job.id }} · 本次实际使用</small></div>
              <div><span>下次处理配置</span><strong>{{ stageConfigSummary(selectedStage) }}</strong><small>{{ detail.configLocked ? '当前任务结束后可编辑' : detail.configDirty ? '有未保存修改' : '从“配置此阶段”进入编辑' }}</small></div>
            </div>
            <ul v-if="detail.configErrors.length" class="config-errors"><li v-for="message in detail.configErrors" :key="message">{{ message }}</li></ul>
            <div v-if="selectedStage === 'preprocess' && detail.activeAudio" class="stage-source">
              <h4>源音频资料</h4><dl class="stage-metadata"><div><dt>文件名</dt><dd>{{ detail.activeAudio.source.original_name }}</dd></div><div><dt>大小</dt><dd>{{ formatSize(detail.activeAudio.source.size_bytes) }}</dd></div><div><dt>语言</dt><dd>{{ detail.activeAudio.source.language }}</dd></div><div><dt>媒体类型</dt><dd>{{ detail.activeAudio.source.media_type || '未记录' }}</dd></div></dl>
            </div>
            <div v-if="detail.stageDetailLoading" class="stage-result-loading" role="status"><span>正在读取{{ selectedStageMeta.label }}的任务快照与阶段结果…</span><div class="stage-skeleton"></div><div class="stage-skeleton short"></div></div>
            <div v-else-if="detail.stageDetailError || detail.stageDetail?.result_error" class="stage-result-error" role="alert"><p>{{ detail.stageDetailError || detail.stageDetail?.result_error?.message }}</p><p v-if="detail.stageDetail?.result_error">检查本阶段产物是否保留；恢复文件后刷新，或重新处理当前音频。</p><button class="secondary-button compact-button" type="button" @click="detail.fetchStageDetail()">刷新阶段详情</button></div>
            <div v-else-if="stageMetadata" class="stage-result stage-preprocess-result"><h4>预处理结果 · 产物元数据</h4><dl class="stage-metadata"><div v-for="(value, key) in stageMetadata" :key="key"><dt>{{ key }}</dt><dd><pre>{{ metadataValue(value) }}</pre></dd></div></dl><p v-if="!Object.keys(stageMetadata).length" class="stage-empty">此预处理产物没有记录音频元数据。</p></div>
            <div v-else-if="stageTranscript" class="stage-result stage-transcript-result">
              <div class="stage-result-heading"><h4>{{ selectedStage === 'asr' ? '原始识别文本' : selectedStage === 'alignment' ? '对齐片段时间' : '源文与译文对照' }}</h4><span class="count-label">{{ stageTranscript.segments.length }} 条片段 · 时长 {{ formatTime(stageTranscript.duration) }}</span></div>
              <div v-if="stageTranscript.segments.length" class="stage-table-scroll"><table class="stage-transcript-table"><thead><tr><th scope="col">片段</th><th scope="col">开始 → 结束</th><th scope="col">{{ selectedStage === 'translation' ? '源文' : '识别文本' }}</th><th v-if="selectedStage === 'translation'" scope="col">译文</th></tr></thead><tbody><tr v-for="segment in stageTranscript.segments" :key="segment.id"><td>{{ segment.id }}</td><td class="stage-segment-time">{{ formatTime(segment.start) }} → {{ formatTime(segment.end) }}</td><td>{{ segment.source_text }}</td><td v-if="selectedStage === 'translation'" class="translated-line">{{ segment.translated_text || '此片段未生成译文' }}</td></tr></tbody></table></div>
              <p v-else class="stage-empty">本阶段已完成，没有识别到字幕片段。</p>
            </div>
            <div v-else-if="selectedStageValue.status === 'completed' && detail.stageDetail?.result?.kind === 'outputs'" class="stage-result stage-export-result"><div class="stage-result-heading"><h4>输出文件</h4><span class="count-label">{{ stageOutputs.length }} 个文件</span></div><div v-if="stageOutputs.length" class="output-list"><a v-for="output in stageOutputs" :key="output.name" class="output-item" :href="output.download_url" download><span class="output-icon">↓</span><span><strong>{{ output.name }}</strong><small>{{ formatOutputSize(output.size_bytes) }}</small></span><span class="download-label">下载</span></a></div><p v-else class="stage-empty">当前任务没有可下载的文件，请检查导出格式并重新处理。</p></div>
            <p v-else class="stage-empty" role="status">{{ stageEmptyMessage }}</p>
          </section>
          <div v-if="configModalOpen" class="modal-backdrop" role="presentation" @click.self="closeStageConfig()">
            <section class="config-modal" role="dialog" aria-modal="true" :aria-labelledby="`config-modal-${configModalStage}`">
              <div class="config-modal-header"><div><span class="section-kicker">NODE {{ String(selectedStageIndex + 1).padStart(2, '0') }}</span><h3 :id="`config-modal-${configModalStage}`">{{ configModalStageMeta.label }}配置</h3><p>下次处理配置 · 只修改当前音频的此阶段；当前任务使用固定快照。</p></div><button class="modal-close" type="button" aria-label="关闭节点配置" @click="closeStageConfig()">×</button></div>
              <div v-if="detail.configLocked" class="modal-lock-note"><span aria-hidden="true">▣</span>任务运行、取消或排队中，字段只读。当前任务结束后可修改下次处理配置。</div>
              <form v-if="detail.config" class="config-modal-form" @submit.prevent="saveStageConfig">
                <template v-if="configModalStage === 'preprocess'"><div class="modal-field-grid"><label class="config-field"><span>处理 Provider</span><select v-model="detail.config.preprocess.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="ffmpeg">FFmpeg（真实音频）</option></select></label><label class="config-field"><span>采样率</span><select v-model.number="detail.config.preprocess.sample_rate" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="8000">8 kHz</option><option :value="16000">16 kHz</option><option :value="44100">44.1 kHz</option><option :value="48000">48 kHz</option></select></label><label class="config-field"><span>声道数</span><select v-model.number="detail.config.preprocess.channels" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="1">单声道</option><option :value="2">双声道</option></select></label></div><label class="check-option modal-check"><input v-model="detail.config.preprocess.preserve_original" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>保留原始音频，便于后续重新处理</span></label></template>
                <AsrConfigFields v-else-if="configModalStage === 'asr'" :key="`${detail.project?.id}/${detail.activeAudioId}`" :audio-key="`${detail.project?.id}/${detail.activeAudioId}`" :config="detail.config.asr" :locked="detail.configLocked" :backend-details="asrBackendDetails" @change="updateAsrConfig" />
                <template v-else-if="configModalStage === 'alignment'"><label class="check-option modal-check"><input v-model="detail.config.alignment.enabled" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>启用时间轴对齐</span></label><div class="modal-field-grid"><label class="config-field"><span>对齐 Provider</span><input v-model="detail.config.alignment.provider" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>对齐模型</span><input v-model="detail.config.alignment.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>失败回退策略</span><select v-model="detail.config.alignment.fallback" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="keep_asr_timestamps">保留识别时间轴</option><option value="fail_job">让任务失败</option></select></label></div></template>
                <template v-else-if="configModalStage === 'translation'"><div class="modal-field-grid"><label class="config-field"><span>协议 Provider</span><select v-model="detail.config.translation.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="openai_chat">OpenAI-compatible Chat Completions</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option></select></label><label class="config-field"><span>模型</span><input v-model="detail.config.translation.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>目标语言</span><select v-model="detail.config.target_language" :disabled="detail.configLocked" @change="syncTranslationTarget"><option value="zh-CN">中文（简体）</option><option value="en">英语</option><option value="ja">日语</option><option value="ko">韩语</option></select></label><label class="config-field"><span>批量大小</span><input v-model.number="detail.config.translation.batch_size" type="number" min="1" max="128" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field"><span>附加参数（JSON，默认 {}）</span><textarea v-model="additionalParametersText" rows="3" :disabled="detail.configLocked" placeholder='例如 {"top_p": 0.9}' @input="updateAdditionalParameters"></textarea><small v-if="additionalParametersError" class="settings-error">{{ additionalParametersError }}</small><small>默认不发送 temperature、response_format 等可选参数；核心字段与认证字段不可覆盖。</small></label><label class="config-field"><span>请求超时（秒）</span><input v-model.number="detail.config.translation.timeout_seconds" type="number" min="1" max="3600" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label></div><label class="config-field modal-wide-field"><span>接口地址（可选）</span><input v-model="detail.config.translation.base_url" type="url" placeholder="例如：https://api.openai.com/v1" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field modal-wide-field"><span>API Key 环境变量名</span><input v-model="detail.config.translation.api_key_env" placeholder="例如：OPENAI_API_KEY（不会保存密钥值）" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><label class="config-field modal-wide-field"><span>翻译指令（可选）</span><textarea v-model="detail.config.translation.instructions" rows="4" placeholder="告诉模型术语、语气或格式要求" :disabled="detail.configLocked" @input="detail.markConfigDirty"></textarea></label></template>
                <template v-else><div class="modal-field-grid"><label class="config-field"><span>文件命名方式</span><select :value="currentExportNaming" :disabled="detail.configLocked || !exportNamingModes.length" @change="setExportNaming(textValue($event))"><option v-if="currentExportNaming && !exportNamingModes.some((mode) => mode.value === currentExportNaming)" :value="currentExportNaming">{{ currentExportNaming }}（当前值，不可新选）</option><option v-for="mode in exportNamingModes" :key="mode.value" :value="mode.value">{{ mode.label }}</option></select></label><label class="config-field modal-wide-field"><span>自定义命名模板（可选）</span><input :value="detail.config.export.filename_template || ''" type="text" maxlength="128" placeholder="例如：{source_language}-{target_language}-{kind}-{format}" :disabled="detail.configLocked" @input="setFilenameTemplate" /><small>支持 {source_language}、{target_language}、{kind}、{format}；自定义时不按语言命名模式命名。</small></label></div><p v-if="capabilityError || (exportCapabilitiesLoaded && !exportNamingModes.length)" class="settings-feedback settings-error" role="alert">{{ capabilityError || '服务端未提供导出命名方式；为保留现有配置，命名方式暂不可新选。' }}</p><div class="format-picker" v-if="exportFormats.length"><span>导出格式</span><label v-for="format in exportFormats" :key="format.value" class="format-option"><input type="checkbox" :checked="detail.config.export.formats.includes(format.value)" :disabled="detail.configLocked" @change="toggleExportFormat(format.value)" /><span>{{ format.label }}</span></label></div><label class="check-option modal-check"><input v-model="detail.config.export.bilingual" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>导出双语字幕</span></label></template>
                <ul v-if="detail.configErrors.length" class="config-errors modal-errors"><li v-for="message in detail.configErrors" :key="message">{{ message }}</li></ul>
                <div class="config-modal-footer"><span>{{ hasModalChanges() ? '有未保存修改' : '当前节点配置已保存' }}</span><div><button class="secondary-button" type="button" @click="closeStageConfig()">取消</button><button class="primary-button" type="submit" :disabled="detail.configLocked || !hasModalChanges() || detail.configSaving">{{ detail.configSaving ? '保存中…' : '保存节点配置' }}</button></div></div>
              </form>
            </section>
          </div>
          </template>
        </template>
      </section>
    </main>
    <GlobalSettingsModal v-if="globalSettingsOpen" @close="globalSettingsOpen = false" />
  </div>
</template>
