<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useProjectsStore } from './stores/projects'

type View = 'list' | 'create'

const store = useProjectsStore()
const view = ref<View>('list')
const projectName = ref('')
const sourceLanguage = ref('ja')
const selectedFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const pageTitle = computed(() => (view.value === 'list' ? '我的项目' : '新建项目'))

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
  await store.createProject(selectedFile.value, projectName.value, sourceLanguage.value)
  projectName.value = ''
  selectedFile.value = null
  if (fileInput.value) fileInput.value.value = ''
  view.value = 'list'
}

function cancelCreate() {
  view.value = 'list'
  store.error = ''
}

onMounted(() => store.fetchProjects())
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="view = 'list'">
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
          <p v-if="view === 'list'" class="subtitle">管理音频项目，开始你的翻译流程。</p>
          <p v-else class="subtitle">上传一段音频，创建一个新的翻译项目。</p>
        </div>
        <button v-if="view === 'list'" class="primary-button" type="button" @click="view = 'create'">
          <span aria-hidden="true">＋</span> 新建项目
        </button>
      </section>

      <div v-if="store.error" class="alert" role="alert">
        <span aria-hidden="true">!</span>
        {{ store.error }}
        <button type="button" aria-label="关闭提示" @click="store.error = ''">×</button>
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
          <article v-for="project in store.projects" :key="project.id" class="project-card">
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

      <section v-else class="content-card create-card">
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
    </main>
  </div>
</template>
