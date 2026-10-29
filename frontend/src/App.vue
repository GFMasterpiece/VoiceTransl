<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore, type Project } from './stores/projects'
import { useProjectDetailStore } from './stores/projectDetail'
import { useWorkflowStore } from './stores/workflow'
import { useUploadQueue, type UploadPolicy, type UploadStatus } from './composables/useUploadQueue'
import WorkflowEditor from './components/WorkflowEditor.vue'

type View = 'list' | 'create' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const workflowStore = useWorkflowStore()
const view = ref<View>('list')
const workflowEditor = ref<InstanceType<typeof WorkflowEditor> | null>(null)
const projectName = ref('')
const sourceLanguage = ref('')
const targetLanguage = ref('')
const upload = useUploadQueue((file, name, language, target) => store.createProject(file, name, language, target), (id, file, language) => store.addAudio(id, file, language))
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

const pageTitle = computed(() => {
  if (view.value === 'create') return upload.targetProjectId.value ? `添加音频 · ${upload.targetName.value}` : '新建项目'
  if (view.value === 'detail') return detail.project?.name || '项目详情'
  return '我的项目'
})
const pageSubtitle = computed(() => {
  if (view.value === 'create') return '音频归入同一项目，上传后进入节点工作流。'
  if (view.value === 'detail') return '当前音频独占一条节点运行；切换音频不会混合运行历史。'
  return '从音频输入开始，连接音频输出。'
})
const currentError = computed(() => view.value === 'detail' ? detail.error || workflowStore.error : view.value === 'create' ? '' : store.error)
const workflowReady = computed(() => !!detail.project && !!detail.activeAudioId)
const workflowLeaveError = computed(() => {
  if (workflowStore.activeRun) return '节点运行尚未结束，请等待完成或取消后再离开。'
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
function guardUploadNavigation() { return !uploadBusy.value }
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

function confirmCreate() {
  return !uploadItems.value.some((item) => item.status !== 'created')
    || window.confirm('新建项目会清空当前未完成的上传队列。已上传音频会保留在原项目中，继续新建吗？')
}

function openCreateView() {
  upload.clear()
  projectName.value = ''
  sourceLanguage.value = ''
  targetLanguage.value = ''
  view.value = 'create'
}

function showCreate() {
  if (!guardUploadNavigation() || !confirmCreate()) return
  if (view.value === 'detail') leaveWorkflow(openCreateView)
  else openCreateView()
}

function returnUploadQueue() {
  if (!guardUploadNavigation()) return
  if (view.value === 'detail') leaveWorkflow(() => { view.value = 'create' })
  else view.value = 'create'
}

function leaveWorkflow(action: () => void) {
  if (view.value !== 'detail') return 'completed' as const
  if (workflowLeaveError.value) { workflowStore.error = workflowLeaveError.value; return 'blocked' as const }
  const proceed = () => { workflowStore.leaveEditor(); detail.stop(); action() }
  if (workflowEditor.value) return workflowEditor.value.requestClose(proceed)
  if (workflowStore.dirty && !window.confirm('放弃未保存的节点工作流修改并离开？')) return 'blocked' as const
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
  if (view.value === 'detail' && detail.project?.id !== id) {
    leaveWorkflow(() => { void loadProjectWorkspace(id, audioId) })
    return
  }
  await loadProjectWorkspace(id, audioId)
}

function openAddAudioView() {
  if (!detail.project) return
  if (uploadItems.value.length && !window.confirm('替换当前上传队列，向此项目添加音频吗？已上传的音频会保留。')) return
  upload.clear()
  upload.setTarget(detail.project.id, detail.project.name)
  projectName.value = detail.project.name
  sourceLanguage.value = detail.activeAudio?.source.language || detail.project.source.language
  targetLanguage.value = ''
  view.value = 'create'
}

function addProjectAudio() {
  if (!detail.project || !guardUploadNavigation()) return
  if (view.value === 'detail') leaveWorkflow(openAddAudioView)
  else openAddAudioView()
}

function switchAudio(id: string) {
  if (workflowLeaveError.value) { workflowStore.error = workflowLeaveError.value; return }
  if (!detail.selectAudio(id)) workflowStore.error = '无法切换到所选音频，请刷新项目后重试。'
}

function focusAudio(id: string) {
  if (id === detail.activeAudioId) return
  if (view.value === 'detail') leaveWorkflow(() => switchAudio(id))
  else switchAudio(id)
}

function switchWorkflowAudio(event: Event) {
  const select = event.target as HTMLSelectElement
  const audioId = select.value
  if (audioId === detail.activeAudioId) return
  if (workflowStore.activeRun) { select.value = detail.activeAudioId || ''; return }
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
  if (!window.confirm(`确定删除项目“${name}”吗？这会移除项目内全部音频、字幕和输出文件。`)) return
  try {
    await store.deleteProject(id)
    if (detail.project?.id === id) {
      workflowStore.stop()
      detail.stop()
      openListView()
    }
  } catch (reason) {
    store.error = reason instanceof Error ? reason.message : '项目删除失败'
  }
}

async function deleteProject(id: string, name: string) {
  if (view.value === 'detail' && detail.project?.id === id) {
    leaveWorkflow(() => { void deleteProjectNow(id, name) })
    return
  }
  await deleteProjectNow(id, name)
}

defineExpose({ openProject, showCreate, addProjectAudio, focusAudio, showList })

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
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="showList"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>VoiceTransl</span></a>
      <span class="topbar-divider" aria-hidden="true"></span><span class="topbar-caption">节点音频工作台</span><div class="topbar-status"><span class="status-dot"></span>服务已连接</div>
    </header>

    <main class="main-content">
      <section class="page-heading"><div><p class="eyebrow">VOICE / TRANSLATE</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><div v-if="view !== 'create'" class="heading-actions"><button class="primary-button" type="button" aria-label="新建项目" @click="showCreate"><span class="button-icon" aria-hidden="true">＋</span> 新建项目</button><button v-if="uploadItems.length || upload.targetProjectId.value" class="secondary-button" type="button" aria-label="返回上传队列" @click="returnUploadQueue">返回上传队列</button></div></section>

      <div v-if="currentError" class="alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" aria-label="关闭提示" @click="view === 'detail' ? (detail.error = '', workflowStore.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">PROJECTS</span><h2>项目列表</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span> 刷新</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>还没有项目</h3><p>上传音频后，项目会显示在这里。</p><button class="secondary-button" type="button" @click="showCreate">创建第一个项目</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" aria-label="删除项目" @click.stop="deleteProject(project.id, project.name)">删除</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="project.source.original_name">{{ project.source.original_name }}</p><div class="project-meta"><span>{{ project.source.language.toUpperCase() }}</span><span>{{ projectAudioCount(project) }} 条音频 · {{ formatSize(projectAudioSize(project)) }}</span></div></article></div>
        <nav v-if="store.total > store.limit || store.offset > 0" class="pagination-bar" aria-label="项目分页"><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasPrevious" @click="store.previousPage()">上一页</button><span>第 {{ Math.floor(store.offset / store.limit) + 1 }} / {{ store.pageCount }} 页 · 共 {{ store.total }} 个项目</span><button class="secondary-button compact-button" type="button" :disabled="store.loading || !store.hasNext" @click="store.nextPage()">下一页</button></nav>
      </section>

      <section v-else-if="view === 'create'" class="create-layout">
        <div class="create-copy"><span class="section-kicker">NEW PROJECT</span><h2>让音频开始流动。</h2><p>音频保存在同一个项目中。上传后进入节点画布，选择音频、连接输入与输出并运行。</p><div class="node-flow-preview" aria-label="最简节点工作流"><span>音频输入</span><i aria-hidden="true">→</i><span>音频输出</span></div></div>
        <form class="create-form" :aria-busy="uploadBusy" @submit.prevent="submitProject">
          <div class="form-heading"><span class="section-kicker">SOURCE</span><h3>项目资料</h3></div>
          <label class="field-label" for="project-name">项目名称 <span>{{ upload.targetProjectId.value ? '上传到现有项目' : '所有音频共用，可选' }}</span></label>
          <input id="project-name" v-model="projectName" class="text-input" type="text" :disabled="uploadBusy || !!upload.targetProjectId.value" placeholder="留空时使用第一个有效文件名" />
          <div class="modal-field-grid"><label class="config-field" for="source-language"><span>源语言 *</span><input id="source-language" v-model="sourceLanguage" class="text-input" list="language-codes" placeholder="请选择或输入语言代码，例如 ja" autocomplete="off" :disabled="uploadBusy || !!upload.targetProjectId.value" /><small>必须显式选择；接受 BCP 47 风格代码，如 ja、en、zh-CN。</small></label><label v-if="!upload.targetProjectId.value" class="config-field" for="target-language"><span>目标语言 *</span><input id="target-language" v-model="targetLanguage" class="text-input" list="language-codes" placeholder="请选择或输入语言代码，例如 zh-CN" autocomplete="off" :disabled="uploadBusy" /><small>必须与源语言不同。</small></label></div><datalist id="language-codes"><option value="ja">日语</option><option value="en">英语</option><option value="zh-CN">中文（简体）</option><option value="ko">韩语</option></datalist>
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
            <p v-if="createdCount" class="upload-result-note">音频已上传，尚未开始处理。打开项目进入节点工作流。</p>
          </section>
          <p v-if="uploadBusy" class="upload-lock-note" role="status">正在逐个上传，请等待队列完成后离开。文件、项目名称和源语言已固定。</p>
          <div class="form-actions"><button class="secondary-button" type="button" :disabled="uploadBusy" @click="cancelCreate">返回列表</button><button v-if="upload.targetProjectId.value" class="secondary-button" type="button" :disabled="uploadBusy" @click="openProject(upload.targetProjectId.value)">打开项目</button><button class="primary-button" type="submit" :disabled="!readyCount || uploadBusy || !uploadPolicy"><span v-if="uploadBusy" class="button-loader" aria-hidden="true"></span>{{ uploadSubmitLabel }}</button></div>
          <input ref="fileInput" class="visually-hidden" type="file" multiple :accept="uploadAccept" :disabled="uploadBusy" @change="handleFileChange" />
        </form>
      </section>

      <section v-else class="workspace">
        <div class="workspace-header"><button class="back-button" type="button" @click="showList">← 返回项目列表</button><div class="workspace-actions"><label class="workflow-audio-picker"><span>当前音频</span><select :value="detail.activeAudioId || ''" :disabled="!!workflowStore.activeRun" aria-label="切换节点工作流音频" @change="switchWorkflowAudio"><option v-for="audio in detail.audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }}</option></select></label><button class="secondary-button" type="button" :disabled="!!workflowStore.activeRun" @click="addProjectAudio">添加音频</button><button class="secondary-button danger-button" type="button" :disabled="detail.anyRunning || !!workflowStore.activeRun" @click="deleteProject(detail.project?.id || '', detail.project?.name || '')">删除项目</button></div></div>
        <div v-if="detail.loading && !detail.project" class="workspace-skeleton"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        <template v-else-if="detail.project">
          <WorkflowEditor v-if="workflowReady" :key="`${detail.project.id}/${detail.activeAudioId}`" ref="workflowEditor" :project-id="detail.project.id" :audio-id="detail.activeAudioId!" :audio-name="detail.activeAudio?.source.original_name || '当前音频'" />
          <div v-else class="workflow-empty workspace-empty"><strong>项目还没有可运行的音频</strong><span>添加音频后即可进入节点工作流。</span></div>
          <section v-if="!workflowReady" class="audio-library" aria-labelledby="audio-library-title"><div class="panel-heading"><div><span class="section-kicker">AUDIO LIBRARY</span><h3 id="audio-library-title">项目音频 · {{ detail.audios.length }} 条</h3><p class="audio-library-note">至少上传一条音频才能创建节点运行。</p></div></div></section>
          <p v-if="detail.loading" class="config-loading" role="status">正在加载当前音频的节点上下文…</p>
          <section class="workspace-title compact"><div><span class="section-kicker">{{ detail.activeAudio?.source.language.toUpperCase() }}</span><h2>{{ detail.activeAudio?.source.original_name || '选择音频' }}</h2><p>{{ detail.project.name }} · {{ formatSize(detail.activeAudio?.source.size_bytes || 0) }}</p></div></section>
        </template>
      </section>
    </main>
  </div>
</template>
