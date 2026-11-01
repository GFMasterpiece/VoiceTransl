<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore, type Audio, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import { useUploadQueue, validateLanguages, type UploadPolicy, type UploadStatus } from './composables/useUploadQueue'
import WorkflowEditor from './components/WorkflowEditor.vue'
import { locale, message, messageRef, setLocale, t } from './i18n'

type View = 'list' | 'create' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const workflowStore = useWorkflowStore()
const view = ref<View>('list')
const workflowEditor = ref<InstanceType<typeof WorkflowEditor> | null>(null)
const projectName = ref('')
const sourceLanguage = ref('')
const targetLanguage = ref('')
const batchImport = ref(false)
const upload = useUploadQueue((file, name, language, target) => store.createProject(file, name, language, target), (id, file, language) => store.addAudio(id, file, language))
const { items: uploadItems, busy: uploadBusy, feedback: uploadFeedback, policy: uploadPolicy, validCount, readyCount, createdCount, failedCount, pendingCount } = upload
const policyLoading = ref(false)
const formBusy = computed(() => uploadBusy.value || store.creating)
const { text: policyError, set: setPolicyError } = messageRef()
const uploadAccept = computed(() => uploadPolicy.value?.allowed_extensions.map((extension) => `.${extension}`).join(',') || '')
const uploadSubmitLabel = computed(() => {
  if (uploadBusy.value) return t('app.upload.uploading')
  if (readyCount.value) return t(`app.upload.submit${upload.targetProjectId.value ? 'Existing' : 'New'}${readyCount.value === 1 ? 'One' : 'Other'}`, { count: formatNumber(readyCount.value) })
  if (failedCount.value) return t('app.upload.retryFailed')
  if (createdCount.value) return t('app.upload.complete')
  return t('app.upload.wait')
})
const fileInput = ref<HTMLInputElement | null>(null)

const pageTitle = computed(() => {
  if (view.value === 'create') return upload.targetProjectId.value ? t('app.title.add', { name: upload.targetName.value }) : t('app.title.create')
  if (view.value === 'detail') return detail.project?.name || t('app.title.detail')
  return t('app.title.list')
})
const pageSubtitle = computed(() => {
  if (view.value === 'create') return t('app.subtitle.create')
  if (view.value === 'detail') return t('app.subtitle.detail')
  return t('app.subtitle.list')
})
const currentError = computed(() => view.value === 'detail' ? detail.error || (!detail.project || detail.loading ? workflowStore.error : '') : store.error)
const audioContextLocked = computed(() => !!workflowStore.activeRun || workflowStore.saving || workflowStore.validating || workflowStore.actionLoading || workflowStore.resourceUploading || workflowStore.loading)
const workflowLeaveError = computed(() => {
  if (workflowStore.activeRun) return message('app.errors.activeRun')
  return ''
})

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
    setPolicyError(message('app.errors.policy'))
  } finally {
    policyLoading.value = false
  }
}
function uploadStatusLabel(status: UploadStatus) {
  return t(`app.upload.status.${status}`)
}
function clearUploadQueue() {
  if (uploadBusy.value) return
  if (uploadItems.value.length && !window.confirm(t('app.confirm.clear'))) return
  upload.clear()
  projectName.value = ''
}
function guardUploadNavigation() { return !formBusy.value }
function guardUnload(event: BeforeUnloadEvent) {
  if (!formBusy.value) return
  event.preventDefault()
  event.returnValue = ''
}
function formatDate(value: string) {
  return new Intl.DateTimeFormat(locale.value, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
}
function projectSourceName(project: Project) { return project.source?.original_name || project.audios?.[0]?.source.original_name || t('app.projects.noAudio') }
function projectAudioCount(project: Project) { return project.audios?.length ?? (project.source ? 1 : 0) }
function projectAudioSize(project: Project) { return project.audios?.reduce((sum, audio) => sum + audio.source.size_bytes, 0) ?? project.source?.size_bytes ?? 0 }
function formatNumber(value: number) { return new Intl.NumberFormat(locale.value).format(value) }
function formatSize(size: number) {
  if (!size) return '0 KB'
  if (size < 1024 * 1024) return `${formatNumber(Math.max(1, Math.round(size / 1024)))} KB`
  return `${new Intl.NumberFormat(locale.value, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(size / 1024 / 1024)} MB`
}
function audioCount(count: number) { return t(`app.projects.audio${count === 1 ? 'One' : 'Other'}`, { count: formatNumber(count) }) }
function changeLocale(event: Event) { setLocale((event.target as HTMLSelectElement).value) }
async function submitProject() {
  if (formBusy.value) return
  if (!sourceLanguage.value.trim()) { upload.setFeedback(message('app.upload.chooseSource')); return }
  if (!upload.targetProjectId.value && !targetLanguage.value.trim()) { upload.setFeedback(message('app.upload.chooseTarget')); return }
  if (!batchImport.value) {
    const validation = validateLanguages(sourceLanguage.value, targetLanguage.value)
    if (validation) {
      upload.setFeedback(Object.defineProperty(new Error(), 'message', { get: () => validateLanguages(sourceLanguage.value, targetLanguage.value) }))
      return
    }
    if (!projectName.value.trim()) { upload.setFeedback(message('app.create.chooseName')); return }
    upload.setFeedback('')
    try {
      const project = await store.createEmptyProject(projectName.value, sourceLanguage.value, targetLanguage.value)
      await openProject(project.id)
    } catch {
      // The projects store exposes the creation error without losing the form.
    }
    return
  }
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

function confirmCreate() {
  return !uploadItems.value.some((item) => item.status !== 'created')
    || window.confirm(t('app.confirm.create'))
}

function openCreateView() {
  upload.clear()
  projectName.value = ''
  sourceLanguage.value = ''
  targetLanguage.value = ''
  batchImport.value = false
  store.error = ''
  view.value = 'create'
}

function showCreate() {
  if (!guardUploadNavigation() || !confirmCreate()) return
  if (view.value === 'detail') leaveWorkflow(openCreateView)
  else openCreateView()
}

function returnUploadQueue() {
  if (!guardUploadNavigation()) return
  batchImport.value = true
  if (view.value === 'detail') leaveWorkflow(() => { view.value = 'create' })
  else view.value = 'create'
}

function leaveWorkflow(action: () => void) {
  if (view.value !== 'detail') return 'completed' as const
  if (workflowLeaveError.value) { workflowStore.setError(workflowLeaveError.value); return 'blocked' as const }
  const proceed = () => { workflowStore.leaveEditor(); detail.stop(); action() }
  if (workflowEditor.value) return workflowEditor.value.requestClose(proceed)
  if (workflowStore.dirty && !window.confirm(t('app.confirm.leave'))) return 'blocked' as const
  if (workflowStore.dirty) workflowStore.discardDraft()
  proceed()
  return 'completed' as const
}

async function loadProjectWorkspace(id: string, audioId?: string) {
  if (!guardUploadNavigation()) return
  view.value = 'detail'
  workflowStore.error = ''
  try {
    await detail.load(id, audioId)
  } catch {
    // The store exposes a user-facing error while keeping the detail view open.
  }
}

async function openProject(id: string, audioId?: string) {
  if (view.value === 'detail' && detail.project?.id === id) {
    if (audioId) focusAudio(audioId)
    return
  }
  if (view.value === 'detail' && detail.project?.id !== id) {
    leaveWorkflow(() => { void loadProjectWorkspace(id, audioId) })
    return
  }
  await loadProjectWorkspace(id, audioId)
}

function switchAudio(id: string) {
  if (!detail.selectAudio(id)) workflowStore.setError(message('app.errors.switchAudio'))
}

function focusAudio(id: string) {
  if (audioContextLocked.value) return
  if (id === detail.activeAudioId) return
  switchAudio(id)
}

function audioUploaded(audio: Audio) { detail.registerAudio(audio) }

function switchWorkflowAudio(event: Event) {
  const select = event.target as HTMLSelectElement
  const audioId = select.value
  if (audioContextLocked.value) { select.value = detail.activeAudioId || ''; return }
  if (audioId === detail.activeAudioId) return
  focusAudio(audioId)
}

function openListView() {
  view.value = 'list'
  detail.error = ''
  workflowStore.error = ''
  void store.fetchProjects()
}

function showList() {
  if (!guardUploadNavigation()) return
  if (view.value === 'detail') leaveWorkflow(openListView)
  else openListView()
}

async function deleteProjectNow(id: string, name: string) {
  if (!window.confirm(t('app.confirm.delete', { name }))) return
  try {
    await store.deleteProject(id)
    if (detail.project?.id === id) {
      workflowStore.stop()
      detail.stop()
      openListView()
    }
  } catch (reason) {
    store.setError(reason instanceof Error ? reason : message('app.errors.delete'))
  }
}

async function deleteProject(id: string, name: string) {
  if (view.value === 'detail' && detail.project?.id === id) {
    leaveWorkflow(() => { void deleteProjectNow(id, name) })
    return
  }
  await deleteProjectNow(id, name)
}

defineExpose({ openProject, showCreate, focusAudio, showList })

onMounted(() => {
  void store.fetchProjects()
  void loadUploadPolicy()
  window.addEventListener('beforeunload', guardUnload)
})
onUnmounted(() => {
  workflowStore.stop()
  detail.stop()
  window.removeEventListener('beforeunload', guardUnload)
})
</script>

<template>
  <div class="app-shell" :class="{ 'is-canvas-workspace': view === 'detail' }">
    <header v-if="view !== 'detail'" class="topbar">
      <a class="brand" href="#" :aria-label="t('app.brandHome')" @click.prevent="showList"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>VoiceTransl</span></a>
      <span class="topbar-divider" aria-hidden="true"></span><span class="topbar-caption">{{ t('app.caption') }}</span><div class="topbar-status"><span class="status-dot"></span>{{ t('app.connected') }}</div>
      <select class="locale-picker" :value="locale" :aria-label="t('app.language')" @change="changeLocale"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select>
    </header>

    <main class="main-content">
      <section v-if="view !== 'detail'" class="page-heading"><div><p class="eyebrow">{{ t('app.eyebrow') }}</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><div v-if="view !== 'create'" class="heading-actions"><button class="primary-button" type="button" :aria-label="t('app.actions.create')" @click="showCreate"><span class="button-icon" aria-hidden="true">＋</span>{{ t('app.actions.create') }}</button><button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button" type="button" :aria-label="t('app.actions.queue')" @click="returnUploadQueue">{{ t('app.actions.queue') }}</button></div></section>

      <div v-if="currentError" class="alert" :class="{ 'workspace-alert': view === 'detail' }" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" :aria-label="t('app.actions.close')" @click="view === 'detail' ? (detail.error = '', workflowStore.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">{{ t('app.projects.kicker') }}</span><h2>{{ t('app.projects.title') }}</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span>{{ t('app.actions.refresh') }}</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>{{ t('app.projects.empty') }}</h3><p>{{ t('app.projects.emptyHint') }}</p><button class="secondary-button" type="button" @click="showCreate">{{ t('app.projects.first') }}</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" :aria-label="t('app.actions.deleteProject')" @click.stop="deleteProject(project.id, project.name)">{{ t('app.actions.delete') }}</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="projectSourceName(project)">{{ projectSourceName(project) }}</p><div class="project-meta"><span>{{ (project.source_language || project.source?.language || project.audios?.[0]?.source.language || '').toUpperCase() }}</span><span>{{ audioCount(projectAudioCount(project)) }} · {{ formatSize(projectAudioSize(project)) }}</span></div></article></div>
        <nav v-if="store.total > store.limit || store.offset > 0" class="pagination-bar" :aria-label="t('app.projects.pagination')"><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasPrevious" @click="store.previousPage()">{{ t('app.actions.previous') }}</button><span>{{ t('app.projects.page', { page: formatNumber(Math.floor(store.offset / store.limit) + 1), pages: formatNumber(store.pageCount), total: formatNumber(store.total) }) }}</span><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasNext" @click="store.nextPage()">{{ t('app.actions.next') }}</button></nav>
      </section>

      <section v-else-if="view === 'create'" class="create-layout">
        <div class="create-copy"><span class="section-kicker">{{ t('app.create.kicker') }}</span><h2>{{ t('app.create.title') }}</h2><p>{{ t('app.create.description') }}</p><div class="node-flow-preview" :aria-label="t('app.create.preview')"><span>{{ t('app.create.input') }}</span><i aria-hidden="true">→</i><span>{{ t('app.create.output') }}</span></div></div>
        <form class="create-form" :aria-busy="formBusy" @submit.prevent="submitProject">
          <div class="form-heading"><span class="section-kicker">{{ t('app.create.sourceKicker') }}</span><h3>{{ t('app.create.details') }}</h3></div>
          <label class="field-label" for="project-name">{{ t('app.create.name') }} <span>{{ t(upload.targetProjectId.value ? 'app.create.existing' : batchImport ? 'app.create.optional' : 'app.create.required') }}</span></label>
          <input id="project-name" v-model="projectName" class="text-input" type="text" :disabled="formBusy || !!upload.targetProjectId.value" :placeholder="t(batchImport ? 'app.create.namePlaceholder' : 'app.create.emptyNamePlaceholder')" />
          <div class="modal-field-grid"><label class="config-field" for="source-language"><span>{{ t('app.create.source') }}</span><input id="source-language" v-model="sourceLanguage" class="text-input" list="language-codes" :placeholder="t('app.create.sourcePlaceholder')" autocomplete="off" :disabled="formBusy || !!upload.targetProjectId.value" /><small>{{ t('app.create.sourceHint') }}</small></label><label v-if="!upload.targetProjectId.value" class="config-field" for="target-language"><span>{{ t('app.create.target') }}</span><input id="target-language" v-model="targetLanguage" class="text-input" list="language-codes" :placeholder="t('app.create.targetPlaceholder')" autocomplete="off" :disabled="formBusy" /><small>{{ t('app.create.targetHint') }}</small></label></div><datalist id="language-codes"><option value="ja">{{ t('app.create.japanese') }}</option><option value="en">{{ t('app.create.english') }}</option><option value="zh-CN">{{ t('app.create.chinese') }}</option><option value="ko">{{ t('app.create.korean') }}</option></datalist>
          <p v-if="uploadFeedback" class="upload-feedback" :class="{ 'settings-error': !readyCount || upload.feedback }" role="status">{{ uploadFeedback }}</p>
          <button v-if="!batchImport" class="text-button" data-test="batch-import" type="button" :disabled="formBusy" @click="batchImport = true">{{ t('app.create.batchImport') }}</button>
          <template v-if="batchImport">
          <label class="field-label" for="audio-picker">{{ t('app.create.files') }}</label>
          <button id="audio-picker" class="upload-zone" type="button" :disabled="uploadBusy" @click="chooseFile" @dragover.prevent @drop="handleDrop"><span class="upload-icon" aria-hidden="true">↑</span><span><strong>{{ t(uploadItems.length ? 'app.create.more' : 'app.create.choose') }}</strong><small>{{ t('app.create.drop') }}</small></span></button>
          <p v-if="uploadPolicy" class="file-hint">{{ t('app.create.limits', { extensions: uploadPolicy.allowed_extensions.map((extension) => extension.toUpperCase()).join(' / '), size: formatSize(uploadPolicy.max_upload_bytes) }) }}</p>
          <p v-else-if="policyLoading" class="file-hint" role="status">{{ t('app.create.loadingPolicy') }}</p>
          <div v-if="policyError" class="upload-policy-error" role="alert"><span>{{ policyError }}</span><button class="text-button" type="button" :disabled="policyLoading || uploadBusy" @click="loadUploadPolicy">{{ t('app.create.retryPolicy') }}</button></div>
          <section v-if="uploadItems.length" class="upload-queue" aria-labelledby="upload-queue-title">
            <div class="upload-queue-heading"><h4 id="upload-queue-title">{{ t('app.upload.selected', { valid: formatNumber(validCount), total: formatNumber(uploadItems.length) }) }}</h4><button class="text-button" type="button" :disabled="uploadBusy" @click="clearUploadQueue">{{ t('app.upload.clear') }}</button></div>
            <p class="upload-summary" role="status" aria-live="polite">{{ t('app.upload.summary', { created: formatNumber(createdCount), pending: formatNumber(pendingCount), failed: formatNumber(failedCount), invalid: formatNumber(uploadItems.length - validCount) }) }}</p>
            <ul class="upload-list">
              <li v-for="item in uploadItems" :key="item.id" class="upload-row" :class="`is-${item.status}`">
                <div class="upload-file-copy"><strong :title="item.file.name">{{ item.file.name }}</strong><span>{{ formatSize(item.file.size) }}</span><span v-if="item.snapshot">{{ t('app.upload.snapshot', { name: item.snapshot.name, language: item.snapshot.language.toUpperCase() }) }}</span></div>
                <span class="upload-status" :class="`is-${item.status}`">{{ uploadStatusLabel(item.status) }}</span>
                <div class="upload-row-actions"><button v-if="item.projectId" class="text-button" type="button" :disabled="uploadBusy" @click="openProject(item.projectId, item.audioId)">{{ t('app.actions.open') }}</button><button v-if="item.status === 'failed'" class="text-button" type="button" :disabled="uploadBusy" :aria-label="t('app.upload.retryLabel', { name: item.file.name })" @click="retryUpload(item.id)">{{ t('app.upload.retry') }}</button><button v-if="item.status !== 'created'" class="text-button" type="button" :disabled="uploadBusy" :aria-label="t('app.upload.removeLabel', { name: item.file.name })" @click="upload.remove(item.id)">{{ t('app.upload.remove') }}</button></div>
                <p v-if="item.error" class="upload-item-error">{{ item.error }}</p>
              </li>
            </ul>
            <p class="file-hint">{{ t('app.upload.sameProject') }}</p>
            <p v-if="createdCount" class="upload-result-note">{{ t('app.upload.result') }}</p>
          </section>
          <p v-if="uploadBusy" class="upload-lock-note" role="status">{{ t('app.upload.locked') }}</p>
          </template>
          <div class="form-actions"><button class="secondary-button" type="button" :disabled="formBusy" @click="cancelCreate">{{ t('app.actions.back') }}</button><button v-if="upload.targetProjectId.value" class="secondary-button" type="button" :disabled="formBusy" @click="openProject(upload.targetProjectId.value)">{{ t('app.actions.open') }}</button><button class="primary-button" type="submit" :disabled="batchImport ? !readyCount || formBusy || !uploadPolicy : formBusy"><span v-if="formBusy" class="button-loader" aria-hidden="true"></span>{{ batchImport ? uploadSubmitLabel : t(store.creating ? 'app.create.creating' : 'app.create.openCanvas') }}</button></div>
          <input ref="fileInput" class="visually-hidden" type="file" :aria-label="t('app.create.files')" multiple :accept="uploadAccept" :disabled="uploadBusy" @change="handleFileChange" />
        </form>
      </section>

      <section v-else class="workspace" :aria-label="t('app.workspace.canvas')">
        <WorkflowEditor v-if="detail.project && !detail.loading" :key="detail.project.id" ref="workflowEditor" :project="detail.project" :audios="detail.audios" :project-id="detail.project.id" :audio-id="detail.activeAudioId" :audio-name="detail.activeAudio?.source.original_name || t('app.workspace.choose')" @select-audio="focusAudio" @uploaded="audioUploaded">
          <template #workspace-header>
            <button class="back-button workspace-header-back" type="button" @click="showList">{{ t('app.workspace.back') }}</button>
            <h1 class="workspace-project-title" :title="detail.project.name">{{ detail.project.name }}</h1>
            <label v-if="detail.audios.length" class="workflow-audio-picker"><span>{{ t('app.workspace.current') }}</span><select :value="detail.activeAudioId || ''" :aria-label="t('app.workspace.switch')" :disabled="audioContextLocked" @change="switchWorkflowAudio"><option v-for="audio in detail.audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }}</option></select></label>
            <div class="workspace-header-actions">
              <button class="secondary-button compact-button" type="button" :aria-label="t('app.actions.create')" @click="showCreate">{{ t('app.actions.create') }}</button>
              <button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button compact-button" type="button" :aria-label="t('app.actions.queue')" @click="returnUploadQueue">{{ t('app.actions.queue') }}</button>
              <button class="secondary-button compact-button danger-button" type="button" :aria-label="t('app.actions.deleteProject')" :disabled="detail.anyRunning || audioContextLocked" @click="deleteProject(detail.project.id, detail.project.name)">{{ t('app.actions.deleteProject') }}</button>
            </div>
            <select class="locale-picker workspace-language-control" :value="locale" :aria-label="t('app.language')" @change="changeLocale"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select>
          </template>
        </WorkflowEditor>
        <div v-else class="workspace-loading-shell">
          <div class="workspace-header">
            <button class="back-button workspace-header-back" type="button" @click="showList">{{ t('app.workspace.back') }}</button>
            <h1 class="workspace-project-title">{{ pageTitle }}</h1>
            <div class="workspace-header-actions">
              <button class="secondary-button compact-button" type="button" :aria-label="t('app.actions.create')" @click="showCreate">{{ t('app.actions.create') }}</button>
              <button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button compact-button" type="button" :aria-label="t('app.actions.queue')" @click="returnUploadQueue">{{ t('app.actions.queue') }}</button>
            </div>
            <select class="locale-picker workspace-language-control" :value="locale" :aria-label="t('app.language')" @change="changeLocale"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select>
          </div>
          <div v-if="detail.loading" class="workspace-skeleton" role="status" :aria-label="t('app.workspace.loading')"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        </div>
      </section>
    </main>
  </div>
</template>
