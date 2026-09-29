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
  if (view.value === 'create') return '新建项目'
  if (view.value === 'detail') return detail.project?.name || '项目详情'
  return '我的项目'
})

const pageSubtitle = computed(() => {
  if (view.value === 'create') return '上传音频，开始一条可追踪的翻译流水线。'
  if (view.value === 'detail') return '从处理进度到最终字幕，所有信息都在这里。'
  return '把音频变成可交付的字幕文件。'
})

const currentError = computed(() => (view.value === 'detail' ? detail.error : store.error))
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

function chooseFile() {
  fileInput.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  selectedFile.value = input.files?.[0] ?? null
  if (selectedFile.value && !projectName.value) projectName.value = selectedFile.value.name.replace(/\.[^/.]+$/, '')
}

function handleDrop(event: DragEvent) {
  event.preventDefault()
  const file = event.dataTransfer?.files?.[0]
  if (!file) return
  selectedFile.value = file
  if (!projectName.value) projectName.value = file.name.replace(/\.[^/.]+$/, '')
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
}

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
  if (key === 'preprocess') return detail.project ? formatSize(detail.project.source.size_bytes) : '—'
  if (key === 'asr') return segments ? `${segments} 条片段` : '等待字幕'
  if (key === 'alignment') return detail.transcript?.duration ? formatTime(detail.transcript.duration) : '时间轴待生成'
  if (key === 'translation') return segments ? `${detail.transcript?.segments.filter((segment) => segment.translated_text).length || 0} 条译文` : '等待识别'
  return detail.outputs.length ? `${detail.outputs.length} 个文件` : '等待导出'
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
  selectedStage.value = 'preprocess'
  try {
    await detail.load(id)
    if (detail.job?.current_stage) selectedStage.value = detail.job.current_stage
    else if (detail.job?.status === 'completed') selectedStage.value = 'export'
    else if (detail.job?.error?.stage) selectedStage.value = detail.job.error.stage
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
    await detail.saveConfig()
    await detail.run(detail.job?.status !== 'completed')
  } catch { /* The store keeps the translated API error. */ }
}

async function cancelJob() {
  try { await detail.cancel() } catch { /* The store keeps the translated API error. */ }
}

async function deleteProject(id: string, name: string) {
  if (!window.confirm(`确定删除项目“${name}”吗？这会移除源音频、字幕和输出文件。`)) return
  try {
    await store.deleteProject(id)
    if (detail.project?.id === id) showList()
  } catch (reason) {
    store.error = reason instanceof Error ? reason.message : '项目删除失败'
  }
}

onMounted(() => void store.fetchProjects())
onUnmounted(() => detail.stop())
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VoiceTransl 首页" @click.prevent="showList"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span><span>VoiceTransl</span></a>
      <span class="topbar-divider" aria-hidden="true"></span><span class="topbar-caption">语音翻译工作台</span><div class="topbar-status"><span class="status-dot"></span>服务已连接</div>
    </header>

    <main class="main-content">
      <section class="page-heading"><div><p class="eyebrow">VOICE / TRANSLATE</p><h1>{{ pageTitle }}</h1><p class="subtitle">{{ pageSubtitle }}</p></div><button v-if="view === 'list'" class="primary-button" type="button" @click="view = 'create'"><span aria-hidden="true">＋</span> 新建项目</button></section>

      <div v-if="currentError" class="alert" role="alert"><span class="alert-symbol" aria-hidden="true">!</span><span>{{ currentError }}</span><button type="button" aria-label="关闭提示" @click="view === 'detail' ? (detail.error = '') : (store.error = '')">×</button></div>

      <section v-if="view === 'list'" class="list-layout">
        <div class="list-toolbar"><div><span class="section-kicker">PROJECTS</span><h2>项目列表</h2></div><button class="text-button" type="button" :disabled="store.loading" @click="store.fetchProjects()"><span :class="{ spinning: store.loading }" aria-hidden="true">↻</span> 刷新</button></div>
        <div v-if="store.loading && !store.projects.length" class="empty-state skeleton-state"><div class="skeleton-line wide"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div>
        <div v-else-if="!store.projects.length" class="empty-state"><div class="empty-icon" aria-hidden="true">◌</div><h3>还没有项目</h3><p>上传音频后，项目会显示在这里。</p><button class="secondary-button" type="button" @click="view = 'create'">创建第一个项目</button></div>
        <div v-else class="project-grid"><article v-for="project in store.projects" :key="project.id" class="project-card" role="button" tabindex="0" @click="openProject(project.id)" @keydown.enter="openProject(project.id)"><div class="project-card-head"><span class="audio-icon" aria-hidden="true">♫</span><button class="project-delete" type="button" aria-label="删除项目" @click.stop="deleteProject(project.id, project.name)">删除</button><span class="project-date">{{ formatDate(project.updated_at) }}</span></div><h3>{{ project.name }}</h3><p class="project-file" :title="project.source.original_name">{{ project.source.original_name }}</p><div class="project-meta"><span>{{ project.source.language.toUpperCase() }}</span><span>{{ formatSize(project.source.size_bytes) }}</span><span class="project-preset">{{ project.preset }}</span></div></article></div>
      </section>

      <section v-else-if="view === 'create'" class="create-layout">
        <div class="create-copy"><span class="section-kicker">NEW PROJECT</span><h2>让音频开始流动。</h2><p>上传文件后，VoiceTransl 会按五个阶段处理，并持续同步每一步的状态。</p><div class="mini-pipeline"><span v-for="stage in stageList" :key="stage.key">{{ stage.shortLabel }}</span></div></div>
        <form class="create-form" @submit.prevent="submitProject"><div class="form-heading"><span class="section-kicker">SOURCE</span><h3>项目资料</h3></div><label class="field-label" for="project-name">项目名称 <span>可选</span></label><input id="project-name" v-model="projectName" class="text-input" type="text" placeholder="例如：第一集字幕" /><label class="field-label" for="source-language">源语言</label><select id="source-language" v-model="sourceLanguage" class="text-input"><option value="ja">日语</option><option value="en">英语</option><option value="zh">中文</option><option value="ko">韩语</option></select><label class="field-label">音频文件</label><button class="upload-zone" type="button" @click="chooseFile" @dragover.prevent @drop="handleDrop"><span class="upload-icon" aria-hidden="true">↑</span><span v-if="selectedFile" class="upload-name">{{ selectedFile.name }}</span><span v-else><strong>点击选择音频文件</strong><small>支持拖放 · WAV / MP3 / M4A / FLAC</small></span></button><p v-if="selectedFile" class="file-hint">已选择 {{ formatSize(selectedFile.size) }}</p><div class="form-actions"><button class="secondary-button" type="button" @click="cancelCreate">取消</button><button class="primary-button" type="submit" :disabled="!selectedFile || store.creating"><span v-if="store.creating" class="button-loader" aria-hidden="true"></span>{{ store.creating ? '创建中…' : '创建项目' }}</button></div><input ref="fileInput" class="visually-hidden" type="file" accept="audio/*,.wav,.mp3,.m4a,.flac" @change="handleFileChange" /></form>
      </section>

      <section v-else class="workspace">
        <div class="workspace-header"><button class="back-button" type="button" @click="showList">← 返回项目列表</button><div class="workspace-actions"><button class="secondary-button danger-button" type="button" :disabled="detail.isRunning || detail.actionLoading" @click="deleteProject(detail.project?.id || '', detail.project?.name || '')">删除项目</button><button v-if="!detail.isRunning" class="primary-button" type="button" :disabled="detail.actionLoading || !detail.configValid || detail.configLoading || detail.configSaving" @click="runJob"><span v-if="detail.actionLoading" class="button-loader" aria-hidden="true"></span>{{ detail.job?.status === 'completed' ? '重新处理' : detail.job?.status === 'pending' ? '开始处理' : '继续处理' }}</button><button v-if="detail.isRunning" class="secondary-button danger-button" type="button" :disabled="detail.actionLoading" @click="cancelJob">{{ detail.actionLoading ? '取消中…' : '取消任务' }}</button></div></div>
        <div v-if="detail.loading && !detail.project" class="workspace-skeleton"><div class="skeleton-line short"></div><div class="skeleton-line wide"></div><div class="skeleton-block"></div></div>
        <template v-else-if="detail.project">
          <section class="config-panel" aria-labelledby="config-title">
            <div class="panel-heading"><div><span class="section-kicker">CONFIGURATION</span><h3 id="config-title">处理配置</h3></div><span class="config-state" :class="{ 'is-valid': detail.configValid, 'is-locked': detail.configLocked }">{{ detail.configLocked ? '运行配置只读' : detail.configValid ? '配置有效' : '需要修正' }}</span></div>
            <div v-if="detail.configLoading" class="config-loading">正在加载项目配置…</div>
            <template v-else-if="detail.config">
              <div class="config-grid">
                <label class="config-field"><span>源语言</span><select v-model="detail.config.source_language" disabled><option value="ja">日语</option><option value="en">英语</option><option value="zh">中文</option><option value="ko">韩语</option></select></label>
                <label class="config-field"><span>目标语言</span><select v-model="detail.config.target_language" :disabled="detail.configLocked" @change="detail.config.translation.target_language = detail.config.target_language; detail.markConfigDirty()"><option value="zh-CN">中文（简体）</option><option value="en">英语</option><option value="ja">日语</option><option value="ko">韩语</option></select></label>
                <label class="config-field"><span>识别 Provider</span><select v-model="detail.config.asr.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="crispasr">CrispASR（本机）</option></select></label>
                <label class="config-field"><span>识别后端</span><select v-model="detail.config.asr.backend" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="qwen3-1.7b">Qwen3 ASR 1.7B</option><option value="whisper">Whisper</option><option value="mega-asr">Mega ASR</option><option value="voxtral">Voxtral</option></select></label>
                <label class="config-field"><span>识别模型</span><input v-model="detail.config.asr.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label>
                <label class="config-field"><span>翻译 Provider</span><input v-model="detail.config.translation.provider" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label>
                <label class="config-field"><span>翻译模型</span><input v-model="detail.config.translation.model" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label>
                <label class="config-field"><span>翻译批量大小</span><input v-model.number="detail.config.translation.batch_size" type="number" min="1" max="128" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label>
                <label class="config-field"><span>预处理 Provider</span><select v-model="detail.config.preprocess.provider" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option value="mock">Mock（演示）</option><option value="ffmpeg">FFmpeg（真实音频）</option></select></label>
                <label class="config-field"><span>采样率</span><select v-model.number="detail.config.preprocess.sample_rate" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="8000">8 kHz</option><option :value="16000">16 kHz</option><option :value="44100">44.1 kHz</option><option :value="48000">48 kHz</option></select></label>
                <label class="config-field"><span>声道数</span><select v-model.number="detail.config.preprocess.channels" :disabled="detail.configLocked" @change="detail.markConfigDirty"><option :value="1">单声道</option><option :value="2">双声道</option></select></label>
              </div>
              <div class="config-options"><label class="check-option"><input v-model="detail.config.preprocess.preserve_original" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>保留原始音频</span></label><label class="check-option"><input v-model="detail.config.alignment.enabled" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>启用时间轴对齐</span></label><label class="check-option"><input v-model="detail.config.asr.vad_enabled" type="checkbox" :disabled="detail.configLocked" @change="detail.markConfigDirty" /><span>启用语音活动检测</span></label><label class="config-field compact-field"><span>分段时长（秒）</span><input v-model.number="detail.config.asr.chunk_seconds" type="number" min="1" max="600" :disabled="detail.configLocked" @input="detail.markConfigDirty" /></label><span class="config-label">导出格式</span><label v-for="format in ['srt', 'vtt', 'lrc']" :key="format" class="format-option"><input type="checkbox" :checked="detail.config.export.formats.includes(format)" :disabled="detail.configLocked" @change="toggleExportFormat(format)" /><span>{{ format.toUpperCase() }}</span></label></div>
              <p class="config-note">翻译 Provider 当前为模拟实现，识别可选择 Mock 或服务器已配置的 CrispASR。</p>
              <ul v-if="detail.configErrors.length" class="config-errors"><li v-for="message in detail.configErrors" :key="message">{{ message }}</li></ul>
              <div class="config-footer"><span>{{ detail.configDirty ? '有未保存的修改' : '配置会在开始处理前固定为任务快照' }}</span><button class="secondary-button" type="button" :disabled="detail.configLocked || !detail.configDirty || detail.configSaving" @click="detail.saveConfig">{{ detail.configSaving ? '保存中…' : '保存配置' }}</button></div>
            </template>
          </section>
          <section class="workspace-title"><div><span class="section-kicker">{{ detail.project.source.language.toUpperCase() }} · {{ detail.project.preset }}</span><h2>{{ detail.project.name }}</h2><p>{{ detail.project.source.original_name }} · {{ formatSize(detail.project.source.size_bytes) }}</p></div><div class="total-progress"><span>总进度</span><strong>{{ detail.progressPercent }}%</strong><div class="progress-track"><span :style="{ width: `${detail.progressPercent}%` }"></span></div><small>{{ statusLabel(detail.job?.status) }}</small></div></section>
          <section class="pipeline-panel" aria-labelledby="pipeline-title"><div class="panel-heading"><div><span class="section-kicker">PIPELINE</span><h3 id="pipeline-title">处理进度</h3></div><span class="live-badge"><i></i>{{ detail.isRunning ? '实时同步' : '状态已保存' }}</span></div><div class="pipeline-scroll"><ol class="pipeline-list"><li v-for="(stage, index) in stageList" :key="stage.key" class="pipeline-step" :class="{ 'is-last': index === stageList.length - 1 }"><button class="pipeline-node" :class="[`is-${stageVisualStatus(stage.key)}`, { 'is-selected': selectedStage === stage.key }]" type="button" :aria-label="`查看${stage.label}详情`" :aria-pressed="selectedStage === stage.key" @click="selectStage(stage.key)"><span class="node-icon">{{ stageIcon(stage.key) }}</span><span class="node-copy"><strong>{{ stage.label }}</strong><small>{{ stageStatusLabel(stageVisualStatus(stage.key)) }}</small></span><span class="node-percent">{{ Math.round(stageState(stage.key).progress * 100) }}%</span></button><span v-if="index < stageList.length - 1" class="pipeline-connector" :class="{ 'is-complete': stageVisualStatus(stage.key) === 'completed' }" aria-hidden="true"></span></li></ol></div></section>
          <section class="inspector-grid"><article class="inspector-panel"><div class="inspector-heading"><div><span class="section-kicker">STAGE {{ String(selectedStageIndex + 1).padStart(2, '0') }}</span><h3>{{ selectedStageMeta.label }}</h3><p>{{ selectedStageMeta.description }}</p></div><span class="stage-badge" :class="`is-${stageVisualStatus(selectedStage)}`">{{ stageStatusLabel(stageVisualStatus(selectedStage)) }}</span></div><div class="metric-row"><div><span>阶段进度</span><strong>{{ selectedStagePercent }}%</strong></div><div><span>阶段结果</span><strong>{{ stageMetric(selectedStage) }}</strong></div><div><span>更新时间</span><strong>{{ detail.job?.updated_at ? formatDate(detail.job.updated_at) : '—' }}</strong></div></div><div class="timeline-block"><div class="timeline-heading"><span>音频时间轴</span><small>{{ detail.transcript?.duration ? formatTime(detail.transcript.duration) : '等待时间轴数据' }}</small></div><div class="waveform" :class="{ muted: stageVisualStatus(selectedStage) === 'pending' }"><span v-for="(height, index) in waveformBars" :key="index" :style="{ height: `${height}%` }"></span></div><div class="timeline-scale"><span>00:00</span><span>01:00</span><span>02:00</span><span>03:00</span></div></div><div class="log-block"><div class="log-heading"><span>处理日志</span><small>{{ detail.job?.updated_at ? new Date(detail.job.updated_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—' }}</small></div><p :class="{ 'is-error': !!selectedStageValue.error }"><i :class="`log-dot is-${stageVisualStatus(selectedStage)}`"></i>{{ selectedStageLog }}</p></div></article><aside class="transcript-panel"><div class="panel-heading"><div><span class="section-kicker">LIVE PREVIEW</span><h3>字幕预览</h3></div><span v-if="detail.transcript" class="count-label">{{ detail.transcript.segments.length }} 条</span></div><div v-if="!detail.transcript" class="preview-empty"><span class="empty-wave" aria-hidden="true">∿</span><h4>字幕还在路上</h4><p>语音识别完成后，片段会显示在这里。</p></div><div v-else class="subtitle-list"><div v-for="segment in detail.transcript.segments.slice(0, 6)" :key="segment.id" class="subtitle-row"><div class="subtitle-time">{{ formatTime(segment.start) }}<br /><span>{{ formatTime(segment.end) }}</span></div><div><p>{{ segment.source_text }}</p><p class="translated-line">{{ segment.translated_text || '等待翻译' }}</p></div></div><div v-if="detail.transcript.segments.length > 6" class="more-hint">还有 {{ detail.transcript.segments.length - 6 }} 条字幕…</div></div></aside></section>
          <section class="outputs-rail"><div class="panel-heading"><div><span class="section-kicker">DELIVERABLES</span><h3>输出文件</h3></div><span class="count-label">{{ detail.outputs.length }} 个文件</span></div><div v-if="!detail.outputs.length" class="outputs-empty">导出完成后，SRT、VTT 或媒体文件会出现在这里。</div><div v-else class="output-list"><a v-for="output in detail.outputs" :key="output.name" class="output-item" :href="output.download_url" download><span class="output-icon">↓</span><span><strong>{{ output.name }}</strong><small>{{ formatOutputSize(output.size_bytes) }}</small></span><span class="download-label">下载</span></a></div></section>
        </template>
      </section>
    </main>
  </div>
</template>
