<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useProjectsStore } from './stores/projects'
import { useProjectDetailStore, type Stage, type StageName } from './stores/projectDetail'

type View = 'list' | 'create' | 'detail'

const store = useProjectsStore()
const detail = useProjectDetailStore()
const view = ref<View>('list')
const projectName = ref('')
const sourceLanguage = ref('ja')
const selectedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const pageTitle = computed(() => {
  if (view.value === 'create') return '新建项目'
  if (view.value === 'detail') return detail.project?.name || '项目详情'
  return '我的项目'
})

const pageSubtitle = computed(() => {
  if (view.value === 'create') return '上传一段音频，创建一个新的翻译项目。'
  if (view.value === 'detail') return '查看翻译进度、字幕内容和导出文件。'
  return '管理音频项目，开始你的翻译流程。'
})

const currentError = computed(() => (view.value === 'detail' ? detail.error : store.error))
const stageList: { key: StageName; label: string }[] = [
  { key: 'preprocess', label: '预处理' },
  { key: 'asr', label: '语音识别' },
  { key: 'alignment', label: '时间对齐' },
  { key: 'translation', label: '翻译' },
  { key: 'export', label: '导出' },
]

function chooseFile() {
  fileInput.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
  if (selectedFile.value && !projectName.value) {
    projectName.value = selectedFile.value.name.replace(/\.[^/.]+$/, '')
  }
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', year: 'numeric' }).format(
    new Date(value),
  )
}

function formatSize(size: number) {
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function submitProject() {
  if (!selectedFile.value) return
  const project = await store.createProject(selectedFile.value, projectName.value, sourceLanguage.value)
  projectName.value = ''
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  await openProject(project.id)
}

function cancelCreate() {
  view.value = 'list'
  store.error = ''
}

async function openProject(id: string) {
  view.value = 'detail'
  try {
    await detail.load(id)
  } catch {
    // The store exposes a user-facing error while keeping the detail view open.
  }
}

function showList() {
  detail.stop()
  view.value = 'list'
  detail.error = ''
  void store.fetchProjects()
}

function stageState(key: StageName): Stage {
  return detail.job?.stages[key] || { status: 'pending', progress: 0 }
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    pending: '等待开始',
    running: '进行中',
    cancelling: '正在取消',
    cancelled: '已取消',
    completed: '已完成',
    failed: '失败',
  }
  return labels[status || 'pending'] || status || '等待开始'
}

function stageStatusLabel(status?: string) {
  const labels: Record<string, string> = { pending: '待处理', running: '处理中', completed: '已完成', failed: '失败' }
  return labels[status || 'pending'] || status || '待处理'
}

function formatTime(value?: number | null) {
  if (value === null || value === undefined) return '—'
  const minutes = Math.floor(value / 60)
  const seconds = value - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(1).padStart(4, '0')}`
}

function formatOutputSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function runJob() {
  try {
    await detail.run(true)
  } catch {
    // The store keeps the translated API error in detail.error.
  }
}

async function cancelJob() {
  try {
    await detail.cancel()
  } catch {
    // The store keeps the translated API error in detail.error.
  }
}

onMounted(() => void store.fetchProjects())
onUnmounted(() => detail.stop())
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="showList">
        <span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
        <span>VoiceTransl</span>
      </a>
      <div class="topbar-caption">语音翻译工作台</div>
      <div class="topbar-status"><span class="status-dot"></span>服务已连接</div>
    </header>

    <main class="main-content">
      <section class="page-heading">
        <div>
          <p class="eyebrow">WORKSPACE</p>
          <h1>{{ pageTitle }}</h1>
          <p class="subtitle">{{ pageSubtitle }}</p>
        </div>
        <button v-if="view === 'list'" class="primary-button" type="button" @click="view = 'create'">
          <span aria-hidden="true">＋</span> 新建项目
        </button>
      </section>

      <div v-if="currentError" class="alert" role="alert">
        <span aria-hidden="true">!</span>
        {{ currentError }}
        <button type="button" aria-label="关闭提示" @click="view === 'detail' ? (detail.error = '') : (store.error = '')">×</button>
      </div>

      <section v-if="view === 'list'" class="content-card">
        <div class="card-toolbar">
          <div>
            <h2>项目列表</h2>
            <span class="count-label">{{ store.projects.length }} 个项目</span>
          </div>
          <button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()">
            <span :class="{ spinning: store.loading }" aria-hidden="true">↻</span> 刷新
          </button>
        </div>

        <div v-if="store.loading && !store.projects.length" class="empty-state loading-state">
          <span class="loader" aria-hidden="true"></span>
          <p>正在加载项目…</p>
        </div>
        <div v-else-if="!store.projects.length" class="empty-state">
          <div class="empty-icon" aria-hidden="true">◌</div>
          <h3>还没有项目</h3>
          <p>上传音频后，项目会显示在这里。</p>
          <button class="secondary-button" type="button" @click="view = 'create'">创建第一个项目</button>
        </div>
        <div v-else class="project-grid">
          <article
            v-for="project in store.projects"
            :key="project.id"
            class="project-card"
            role="button"
            tabindex="0"
            @click="openProject(project.id)"
            @keydown.enter="openProject(project.id)"
          >
            <div class="project-card-head">
              <span class="audio-icon" aria-hidden="true">♫</span>
              <span class="project-date">{{ formatDate(project.updated_at) }}</span>
            </div>
            <h3>{{ project.name }}</h3>
            <p class="project-file" :title="project.source.original_name">{{ project.source.original_name }}</p>
            <div class="project-meta">
              <span>{{ project.source.language.toUpperCase() }}</span>
              <span>{{ formatSize(project.source.size_bytes) }}</span>
              <span class="project-preset">{{ project.preset }}</span>
            </div>
          </article>
        </div>
      </section>

      <section v-else-if="view === 'create'" class="content-card create-card">
        <div class="card-toolbar">
          <div>
            <h2>项目资料</h2>
            <span class="count-label">支持 WAV、MP3 等常见音频格式</span>
          </div>
        </div>
        <form class="create-form" @submit.prevent="submitProject">
          <label class="field-label" for="project-name">项目名称 <span>可选</span></label>
          <input id="project-name" v-model="projectName" class="text-input" type="text" placeholder="例如：第一集字幕" />

          <label class="field-label" for="source-language">源语言</label>
          <select id="source-language" v-model="sourceLanguage" class="text-input">
            <option value="ja">日语</option>
            <option value="en">英语</option>
            <option value="zh">中文</option>
            <option value="ko">韩语</option>
          </select>

          <label class="field-label">音频文件</label>
          <button class="upload-zone" type="button" @click="chooseFile">
            <span class="upload-icon" aria-hidden="true">↑</span>
            <span v-if="selectedFile" class="upload-name">{{ selectedFile.name }}</span>
            <span v-else><strong>点击选择音频文件</strong><small>也可以将文件拖放到这里</small></span>
          </button>
          <input ref="fileInput" class="visually-hidden" type="file" accept="audio/*,.wav,.mp3,.m4a,.flac" @change="handleFileChange" />
          <p v-if="selectedFile" class="file-hint">已选择 {{ formatSize(selectedFile.size) }}</p>

          <div class="form-actions">
            <button class="secondary-button" type="button" @click="cancelCreate">取消</button>
            <button class="primary-button" type="submit" :disabled="!selectedFile || store.creating">
              <span v-if="store.creating" class="button-loader" aria-hidden="true"></span>
              {{ store.creating ? '创建中…' : '创建项目' }}
            </button>
          </div>
        </form>
      </section>

      <section v-else class="detail-page">
        <button class="back-button" type="button" @click="showList">← 返回项目列表</button>

        <div v-if="detail.loading && !detail.project" class="content-card empty-state loading-state">
          <span class="loader" aria-hidden="true"></span>
          <p>正在加载项目详情…</p>
        </div>

        <template v-else-if="detail.project">
          <div class="detail-intro">
            <div>
              <div class="detail-kicker">{{ detail.project.source.language.toUpperCase() }} · {{ detail.project.preset }}</div>
              <h2>{{ detail.project.name }}</h2>
              <p>{{ detail.project.source.original_name }} · {{ formatSize(detail.project.source.size_bytes) }}</p>
            </div>
            <div class="detail-actions">
              <button
                v-if="!detail.isRunning && detail.job?.status !== 'completed'"
                class="primary-button"
                type="button"
                :disabled="detail.actionLoading"
                @click="runJob"
              >
                <span v-if="detail.actionLoading" class="button-loader" aria-hidden="true"></span>
                {{ detail.job?.status === 'pending' ? '开始翻译' : '继续翻译' }}
              </button>
              <button v-if="detail.isRunning" class="secondary-button danger-button" type="button" :disabled="detail.actionLoading" @click="cancelJob">
                {{ detail.actionLoading ? '取消中…' : '取消任务' }}
              </button>
            </div>
          </div>

          <div class="detail-grid">
            <section class="content-card job-card">
              <div class="card-toolbar">
                <div><h2>翻译任务</h2><span class="count-label">{{ detail.job ? statusLabel(detail.job.status) : '加载中' }}</span></div>
                <span class="job-status" :class="`is-${detail.job?.status || 'pending'}`">{{ statusLabel(detail.job?.status) }}</span>
              </div>
              <div class="job-progress">
                <div class="progress-heading"><span>总进度</span><strong>{{ detail.progressPercent }}%</strong></div>
                <div class="progress-track"><span :style="{ width: `${detail.progressPercent}%` }"></span></div>
                <p v-if="detail.job?.current_stage">当前阶段：{{ stageList.find((item) => item.key === detail.job?.current_stage)?.label }}</p>
                <p v-else-if="detail.job?.error" class="job-error">{{ detail.job.error.message }}</p>
              </div>
              <div class="stage-list">
                <div v-for="stage in stageList" :key="stage.key" class="stage-item">
                  <span class="stage-dot" :class="`is-${stageState(stage.key).status}`"></span>
                  <span class="stage-name">{{ stage.label }}</span>
                  <span class="stage-status">{{ stageStatusLabel(stageState(stage.key).status) }}</span>
                  <span class="stage-progress">{{ Math.round(stageState(stage.key).progress * 100) }}%</span>
                </div>
              </div>
            </section>

            <section class="content-card source-card">
              <div class="card-toolbar"><div><h2>项目资料</h2><span class="count-label">源文件</span></div></div>
              <dl class="source-details">
                <div><dt>文件名</dt><dd :title="detail.project.source.original_name">{{ detail.project.source.original_name }}</dd></div>
                <div><dt>源语言</dt><dd>{{ detail.project.source.language.toUpperCase() }}</dd></div>
                <div><dt>文件大小</dt><dd>{{ formatSize(detail.project.source.size_bytes) }}</dd></div>
                <div><dt>更新时间</dt><dd>{{ formatDate(detail.project.updated_at) }}</dd></div>
              </dl>
            </section>
          </div>

          <section class="content-card transcript-card">
            <div class="card-toolbar"><div><h2>当前字幕</h2><span class="count-label">{{ detail.transcript ? `${detail.transcript.segments.length} 条字幕 · ${detail.transcript.stage}` : '任务开始后显示' }}</span></div></div>
            <div v-if="!detail.transcript" class="detail-empty">完成语音识别后，字幕会显示在这里。</div>
            <div v-else class="table-scroll">
              <table class="transcript-table"><thead><tr><th>时间</th><th>原文</th><th>译文</th></tr></thead>
                <tbody><tr v-for="segment in detail.transcript.segments" :key="segment.id"><td class="time-cell">{{ formatTime(segment.start) }}<br /><span>{{ formatTime(segment.end) }}</span></td><td>{{ segment.source_text }}</td><td>{{ segment.translated_text || '—' }}</td></tr></tbody>
              </table>
            </div>
          </section>

          <section class="content-card outputs-card">
            <div class="card-toolbar"><div><h2>输出文件</h2><span class="count-label">{{ detail.outputs.length }} 个文件</span></div></div>
            <div v-if="!detail.outputs.length" class="detail-empty">导出完成后，可在这里下载字幕文件。</div>
            <div v-else class="output-list"><a v-for="output in detail.outputs" :key="output.name" class="output-item" :href="output.download_url" download><span class="output-icon">↓</span><span><strong>{{ output.name }}</strong><small>{{ formatOutputSize(output.size_bytes) }}</small></span><span class="download-label">下载</span></a></div>
          </section>
        </template>
      </section>
    </main>
  </div>
</template>
