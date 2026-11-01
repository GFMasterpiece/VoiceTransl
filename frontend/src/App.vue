<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore, type Audio, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import WorkflowEditor from './components/WorkflowEditor.vue'
import CanvasDialog from './components/CanvasDialog.vue'
import { locale, message, messageRef, setLocale, t } from './i18n'

type View = 'list' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const workflowStore = useWorkflowStore()
const view = ref<View>('list')
const workflowEditor = ref<InstanceType<typeof WorkflowEditor> | null>(null)
const createOpen = ref(false)
const createPreparing = ref(false)
const createError = messageRef()
const projectName = ref('')
const formBusy = computed(() => createPreparing.value || store.creating)

const pageTitle = computed(() => {
  if (view.value === 'detail') return detail.project?.name || t('app.title.detail')
  return t('app.title.list')
})
const pageSubtitle = computed(() => {
  if (view.value === 'detail') return t('app.subtitle.detail')
  return t('app.subtitle.list')
})
const currentError = computed(() => view.value === 'detail' ? detail.error || (!detail.project || detail.loading ? workflowStore.error : '') : store.error)
const audioContextLocked = computed(() => !!workflowStore.activeRun || workflowStore.saving || workflowStore.validating || workflowStore.actionLoading || workflowStore.resourceUploading || workflowStore.loading)
const workflowLeaveError = computed(() => {
  if (workflowStore.activeRun) return message('app.errors.activeRun')
  return ''
})

function guardCreateNavigation() { return !formBusy.value }
function guardUnload(event: BeforeUnloadEvent) {
  if (!formBusy.value) return
  event.preventDefault()
  event.returnValue = ''
}
function formatDate(value: string) {
  return new Intl.DateTimeFormat(locale.value, { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
}
function projectSourceName(project: Project) { return project.source?.original_name || project.audios?.[0]?.source.original_name || t('app.projects.noAudio') }
function projectSourceLanguage(project: Project) { return project.source_language || project.source?.language || project.audios?.[0]?.source.language || '' }
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
  if (formBusy.value || !createOpen.value) return
  if (!projectName.value.trim()) { createError.set(message('app.create.chooseName')); return }
  if (view.value === 'detail' && workflowStore.loading) { createError.set(message('workflow.loading')); return }
  const name = projectName.value
  createError.set('')
  createPreparing.value = true
  if (view.value === 'detail' && workflowEditor.value) {
    // Release this dialog before the editor can show its discard confirmation.
    const needsConfirmation = workflowStore.dirty && !audioContextLocked.value
    if (needsConfirmation) { createOpen.value = false; await nextTick() }
    const result = workflowEditor.value?.requestClose(() => { void performCreate(name) })
    if (result === 'blocked') {
      createPreparing.value = false
      createOpen.value = true
      createError.set(workflowStore.error || message('app.errors.create'))
    } else if (result === 'pending') createPreparing.value = false
    return
  }
  await performCreate(name)
}

async function performCreate(name: string) {
  createPreparing.value = true
  // A confirmed editor dialog must finish restoring focus and inert states first.
  await nextTick()
  createOpen.value = true
  try {
    const project = await store.createEmptyProject(name)
    createOpen.value = false
    await nextTick()
    createPreparing.value = false
    await openProject(project.id)
  } catch (reason) {
    createError.set(reason instanceof Error ? reason : message('app.errors.create'))
  } finally {
    createPreparing.value = false
  }
}
function cancelCreate() {
  if (!guardCreateNavigation()) return
  createOpen.value = false
  createError.set('')
  store.error = ''
}

function showCreate() {
  if (!guardCreateNavigation() || createOpen.value || workflowEditor.value?.hasOpenDialog()) return
  projectName.value = ''
  createError.set('')
  store.error = ''
  createOpen.value = true
}

function leaveWorkflow(action: () => void) {
  if (view.value !== 'detail') return 'completed' as const
  if (workflowLeaveError.value) { workflowStore.setError(workflowLeaveError.value); return 'blocked' as const }
  const proceed = () => { workflowStore.leaveEditor(); detail.stop(); action() }
  if (workflowEditor.value) return workflowEditor.value.requestClose(proceed)
  if (workflowStore.document && workflowStore.dirty && !window.confirm(t('app.confirm.leave'))) return 'blocked' as const
  if (workflowStore.document && workflowStore.dirty) workflowStore.discardDraft()
  proceed()
  return 'completed' as const
}

async function loadProjectWorkspace(id: string, audioId?: string) {
  if (!guardCreateNavigation()) return
  view.value = 'detail'
  workflowStore.error = ''
  try {
    await detail.load(id, audioId)
  } catch {
    // The store exposes a user-facing error while keeping the detail view open.
  }
}

async function openProject(id: string, audioId?: string) {
  if (createOpen.value || !guardCreateNavigation()) return
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
  if (createOpen.value || !guardCreateNavigation()) return
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
  if (createOpen.value || !guardCreateNavigation()) return
  if (view.value === 'detail' && detail.project?.id === id) {
    leaveWorkflow(() => { void deleteProjectNow(id, name) })
    return
  }
  await deleteProjectNow(id, name)
}

defineExpose({ openProject, showCreate, focusAudio, showList })

onMounted(() => {
  void store.fetchProjects()
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
      <section v-if="view !== 'detail'" class="page-heading"><div><p class="eyebrow">{{ t('app.eyebrow') }}</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><div class="heading-actions"><button class="primary-button" type="button" :aria-label="t('app.actions.create')" @click="showCreate"><span class="button-icon" aria-hidden="true">＋</span>{{ t('app.actions.create') }}</button></div></section>

      <div v-if="currentError && !createOpen" class="alert" :class="{ 'workspace-alert': view === 'detail' }" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" :aria-label="t('app.actions.close')" @click="view === 'detail' ? (detail.error = '', workflowStore.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">{{ t('app.projects.kicker') }}</span><h2>{{ t('app.projects.title') }}</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span>{{ t('app.actions.refresh') }}</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>{{ t('app.projects.empty') }}</h3><p>{{ t('app.projects.emptyHint') }}</p><button class="secondary-button" type="button" @click="showCreate">{{ t('app.projects.first') }}</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" :aria-label="t('app.actions.deleteProject')" @click.stop="deleteProject(project.id, project.name)">{{ t('app.actions.delete') }}</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="projectSourceName(project)">{{ projectSourceName(project) }}</p><div class="project-meta"><span v-if="projectSourceLanguage(project)">{{ projectSourceLanguage(project).toUpperCase() }}</span><span>{{ audioCount(projectAudioCount(project)) }} · {{ formatSize(projectAudioSize(project)) }}</span></div></article></div>
        <nav v-if="store.total > store.limit || store.offset > 0" class="pagination-bar" :aria-label="t('app.projects.pagination')"><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasPrevious" @click="store.previousPage()">{{ t('app.actions.previous') }}</button><span>{{ t('app.projects.page', { page: formatNumber(Math.floor(store.offset / store.limit) + 1), pages: formatNumber(store.pageCount), total: formatNumber(store.total) }) }}</span><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasNext" @click="store.nextPage()">{{ t('app.actions.next') }}</button></nav>
      </section>

      <section v-else class="workspace" :aria-label="t('app.workspace.canvas')">
        <WorkflowEditor v-if="detail.project && !detail.loading" :key="detail.project.id" ref="workflowEditor" :project="detail.project" :audios="detail.audios" :project-id="detail.project.id" :audio-id="detail.activeAudioId" :audio-name="detail.activeAudio?.source.original_name || t('app.workspace.choose')" @select-audio="focusAudio" @uploaded="audioUploaded">
          <template #workspace-header>
            <button class="back-button workspace-header-back" type="button" @click="showList">{{ t('app.workspace.back') }}</button>
            <h1 class="workspace-project-title" :title="detail.project.name">{{ detail.project.name }}</h1>
            <label v-if="detail.audios.length" class="workflow-audio-picker"><span>{{ t('app.workspace.current') }}</span><select :value="detail.activeAudioId || ''" :aria-label="t('app.workspace.switch')" :disabled="audioContextLocked" @change="switchWorkflowAudio"><option v-for="audio in detail.audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }}</option></select></label>
            <div class="workspace-header-actions">
              <button class="secondary-button compact-button" type="button" :aria-label="t('app.actions.create')" @click="showCreate">{{ t('app.actions.create') }}</button>

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

            </div>
            <select class="locale-picker workspace-language-control" :value="locale" :aria-label="t('app.language')" @change="changeLocale"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select>
          </div>
          <div v-if="detail.loading" class="workspace-skeleton" role="status" :aria-label="t('app.workspace.loading')"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        </div>
      </section>
    </main>
    <CanvasDialog class="project-create-modal" :open="createOpen" :title="t('app.title.create')" :close-disabled="formBusy" @close="cancelCreate">
      <form class="create-form" :aria-busy="formBusy" @submit.prevent="submitProject">
        <label class="field-label" for="project-name">{{ t('app.create.name') }} <span>{{ t('app.create.required') }}</span></label>
        <input id="project-name" v-model="projectName" class="text-input" type="text" maxlength="200" autofocus :disabled="formBusy" :placeholder="t('app.create.emptyNamePlaceholder')" />
        <p v-if="createError.text.value" class="create-error" role="alert">{{ createError.text.value }}</p>
        <div class="form-actions"><button class="secondary-button" type="button" :disabled="formBusy" @click="cancelCreate">{{ t('app.actions.cancel') }}</button><button class="primary-button" type="submit" :disabled="formBusy"><span v-if="formBusy" class="button-loader" aria-hidden="true"></span>{{ t(store.creating ? 'app.create.creating' : 'app.create.submit') }}</button></div>
      </form>
    </CanvasDialog>
  </div>
</template>
