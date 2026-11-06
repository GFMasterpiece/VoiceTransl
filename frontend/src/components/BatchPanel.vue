<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { displayError, locale, t } from '../i18n'
import type { Audio } from '../stores/projects'
import type { BatchManifest } from '../composables/useBatchManifest'
import { batchActive, batchDone, batchError, batchStatus, batchTerminal, type Batches } from '../composables/useBatches'
import CanvasDialog from './CanvasDialog.vue'

const props = defineProps<{ open: boolean; manifest: BatchManifest; batches: Batches; audios: readonly Audio[]; startReason: string; revision?: number; locked: boolean }>()
const emit = defineEmits<{ close: []; start: [] }>()
const manifest = reactive(props.manifest)
const batches = reactive(props.batches)
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
const existing = ref('')
const includeCancelled = ref(false)
const manifestOffset = ref(0)
const manifestPage = computed(() => manifest.items.slice(manifestOffset.value, manifestOffset.value + 20))
watch(() => manifest.items.length, (length) => { if (manifestOffset.value >= length) manifestOffset.value = Math.max(0, Math.floor((length - 1) / 20) * 20) })
const locked = computed(() => props.locked || manifest.uploading || batches.submitting)
function choose(event: Event, folder: boolean) {
  const input = event.target as HTMLInputElement
  if (input.files && !locked.value) manifest.add(input.files, folder)
  input.value = ''
}
function addExisting() {
  const audio = props.audios.find((audio) => audio.id === existing.value)
  if (audio && !locked.value) manifest.addExisting(audio)
  existing.value = ''
}
function size(bytes?: number) { return bytes === undefined ? '—' : `${new Intl.NumberFormat(locale.value, { maximumFractionDigits: 1 }).format(bytes / 1024 / 1024)} MB` }
function date(value: string) { return Number.isNaN(Date.parse(value)) ? value : new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) }
function pageLabel(page: { total: number; offset: number; items: unknown[] }) { return t('batch.pagination', { from: page.total ? page.offset + 1 : 0, to: page.offset + page.items.length, total: page.total }) }
</script>

<template>
  <CanvasDialog class="batch-dialog" :open="open" :title="t('batch.title')" @close="emit('close')">
    <div class="batch-panel" data-batch-panel>
      <p class="inspector-help">{{ t('batch.background') }}</p>
      <section class="batch-manifest">
        <p class="inspector-help">{{ t('batch.localHint') }}</p>
        <div class="batch-actions">
          <button type="button" class="secondary-button" data-batch-files :disabled="locked" @click="fileInput?.click()">{{ t('batch.files') }}</button>
          <button type="button" class="secondary-button" data-batch-folder :disabled="locked" @click="folderInput?.click()">{{ t('batch.folder') }}</button>
          <label class="batch-check"><input v-model="manifest.recursive" type="checkbox" :disabled="locked" data-batch-recursive />{{ t('batch.recursive') }}</label>
        </div>
        <p class="inspector-help">{{ t('batch.uploadedHint') }}</p>
        <input ref="fileInput" type="file" multiple class="visually-hidden" data-batch-file-input :disabled="locked" :aria-label="t('batch.files')" :accept="manifest.policy?.allowed_extensions.map((value) => `.${value}`).join(',')" @change="choose($event, false)" />
        <input ref="folderInput" type="file" webkitdirectory multiple class="visually-hidden" data-batch-folder-input :disabled="locked" :aria-label="t('batch.folder')" @change="choose($event, true)" />
        <div class="batch-fields">
          <label>{{ t('batch.defaultLanguage') }}<input v-model="manifest.language" type="text" :disabled="locked" data-batch-language :placeholder="t('app.create.sourcePlaceholder')" /></label>
          <button type="button" class="secondary-button" :disabled="locked" @click="manifest.applyLanguage">{{ t('batch.applyLanguage') }}</button>
          <label>{{ t('batch.chooseExisting') }}<select v-model="existing" :disabled="locked"><option value="">{{ t('batch.choose') }}</option><option v-for="audio in audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }} · {{ audio.source.language }}</option></select></label>
          <button type="button" class="secondary-button" :disabled="locked || !existing" @click="addExisting">{{ t('batch.addExisting') }}</button>
        </div>
        <p v-if="manifest.policyLoading" role="status">{{ t('batch.policyLoading') }}</p>
        <p v-if="manifest.error.text" role="alert" class="workflow-run-error">{{ manifest.error.text }}</p>
        <button v-if="!manifest.policy" type="button" class="text-button" :disabled="manifest.policyLoading || locked" @click="manifest.loadPolicy">{{ t('batch.policyRetry') }}</button>
        <p v-if="!manifest.items.length">{{ t('batch.noFiles') }}</p>
        <div v-else class="batch-table-scroll">
          <table class="batch-table"><thead><tr><th>{{ t('batch.selected') }}</th><th>{{ t('batch.name') }}</th><th>{{ t('batch.origin') }}</th><th>{{ t('batch.language') }}</th><th>{{ t('batch.state') }}</th><th /></tr></thead>
            <tbody><tr v-for="item in manifestPage" :key="item.id" :class="{ 'batch-excluded': !manifest.included(item) }" :data-batch-item="item.id">
              <td><input v-model="item.selected" type="checkbox" :disabled="locked" :aria-label="`${t('batch.selected')} ${item.name}`" /></td>
              <td><strong>{{ item.origin?.relative_path || item.name }}</strong><small>{{ item.name }} · {{ size(item.sizeBytes) }}</small></td>
              <td><span>{{ item.fileSelection ? t('batch.flatRoot') : item.origin?.root_name || '—' }}</span><small>{{ item.origin?.root_id || '—' }}</small></td>
              <td><input v-model="item.language" type="text" :disabled="locked || !!item.audioId" :aria-label="`${t('batch.language')} ${item.name}`" /></td>
              <td><span>{{ manifest.validation(item) ? batchStatus('invalid') : batchStatus(item.status) }}</span><small v-if="manifest.validation(item)" class="workflow-run-error">{{ manifest.validation(item) }}</small><small v-if="item.error" class="workflow-run-error">{{ displayError(item.error) }}</small></td>
              <td><button type="button" class="text-button" :disabled="locked" :aria-label="`${t('batch.remove')} ${item.name}`" @click="manifest.remove(item.id)">×</button></td>
            </tr></tbody>
          </table>
        </div>
        <nav v-if="manifest.items.length" class="batch-actions" :aria-label="t('batch.manifestList')"><button type="button" class="secondary-button" :disabled="manifestOffset === 0" @click="manifestOffset = Math.max(0, manifestOffset - 20)">{{ t('batch.previous') }}</button><span>{{ pageLabel({ items: manifestPage, total: manifest.items.length, offset: manifestOffset }) }}</span><button type="button" class="secondary-button" :disabled="manifestOffset + 20 >= manifest.items.length" @click="manifestOffset += 20">{{ t('batch.next') }}</button></nav>
        <div class="batch-actions">
          <button type="button" class="secondary-button" data-batch-upload :disabled="locked || !manifest.selected.some((item) => !item.audioId && !manifest.validation(item))" @click="manifest.upload()">{{ manifest.uploading ? t('batch.uploading') : t('batch.upload') }}</button>
          <button v-if="manifest.items.some((item) => item.status === 'failed')" type="button" class="secondary-button" :disabled="locked" @click="manifest.upload(true)">{{ t('batch.retryUploads') }}</button>
        </div>
        <p>{{ t('batch.confirm', { revision: revision ?? '—', count: manifest.selected.length }) }}</p>
        <p v-if="startReason" class="inspector-help" data-batch-start-reason>{{ startReason }}</p>
        <button type="button" class="primary-button" data-batch-start :disabled="!!startReason || locked || !!batches.pending" @click="emit('start')">{{ t('batch.start') }}</button>
      </section>
      <p v-if="batches.error" role="alert" class="workflow-run-error">{{ batches.error }}</p>
      <div v-if="batches.pending" class="batch-pending" role="status"><p>{{ t('batch.pendingRequest') }}</p><p v-if="!batches.pendingStored" class="workflow-run-error">{{ t('batch.leavePendingVolatile') }}</p><button type="button" class="secondary-button" data-batch-retry-request :disabled="locked" @click="batches.retryPending">{{ t('batch.retryRequest') }}</button></div>
      <section class="batch-history">
        <div class="batch-actions"><h3>{{ t('batch.history') }}</h3><button type="button" class="text-button" :disabled="batches.loading" @click="batches.refresh">{{ t('batch.refresh') }}</button></div>
        <p v-if="!batches.history.items.length && !batches.loading">{{ t('batch.noHistory') }}</p>
        <article v-for="batch in batches.history.items" :key="batch.id" class="batch-record">
          <div class="batch-actions"><strong>{{ batch.id }}</strong><span>{{ batchStatus(batch.status) }}</span><time>{{ date(batch.created_at) }}</time></div>
          <p>{{ t('batch.snapshot', { revision: batch.workflow_revision }) }} · {{ t('batch.progress', { done: batchDone(batch), total: batch.total }) }}</p>
          <progress :value="batchDone(batch)" :max="batch.total" :aria-label="t('batch.progress', { done: batchDone(batch), total: batch.total })" />
          <p>{{ t('batch.counts', { ...batch.counts }) }}</p>
          <p v-if="batch.error" role="alert" class="workflow-run-error">{{ batchError(batch.error) }}</p>
          <div class="batch-actions">
            <button type="button" class="secondary-button" :disabled="batches.detailLoading" @click="batches.select(batch.id)">{{ t('batch.details') }}</button>
            <button v-if="batchActive(batch.status)" type="button" class="secondary-button" :disabled="batches.submitting || batch.cancel_requested" @click="batches.cancel(batch.id)">{{ t('batch.cancel') }}</button>
            <button v-if="batchTerminal(batch.status) && (batch.counts.failed + batch.counts.interrupted + (includeCancelled ? batch.counts.cancelled : 0)) > 0" type="button" class="secondary-button" :disabled="locked || batches.active || !!batches.pending" @click="batches.retry(batch.id, includeCancelled)">{{ t('batch.retry') }}</button>
            <a v-if="batchTerminal(batch.status)" :href="batches.download(batch.id)" class="text-button" download>{{ t('batch.download') }}</a>
          </div>
        </article>
        <label class="batch-check"><input v-model="includeCancelled" type="checkbox" :disabled="batches.submitting" />{{ t('batch.includeCancelled') }}</label>
        <nav class="batch-actions" :aria-label="t('batch.history')"><button type="button" class="secondary-button" :disabled="batches.loading || batches.history.offset === 0" @click="batches.loadHistory(Math.max(0, batches.history.offset - batches.history.limit))">{{ t('batch.previous') }}</button><span>{{ pageLabel(batches.history) }}</span><button type="button" class="secondary-button" :disabled="batches.loading || batches.history.offset + batches.history.limit >= batches.history.total" @click="batches.loadHistory(batches.history.offset + batches.history.limit)">{{ t('batch.next') }}</button></nav>
      </section>
      <section v-if="batches.detail" class="batch-detail">
        <h3>{{ batches.detail.id }} · {{ t('batch.snapshot', { revision: batches.detail.workflow_revision }) }}</h3><p>{{ t('batch.frozen') }}</p>
        <div class="batch-table-scroll"><table class="batch-table"><thead><tr><th>#</th><th>{{ t('batch.name') }}</th><th>{{ t('batch.origin') }}</th><th>{{ t('batch.language') }}</th><th>{{ t('batch.state') }}</th></tr></thead><tbody><tr v-for="item in batches.items.items" :key="item.index"><td>{{ item.index + 1 }}</td><td>{{ item.audio_name }}<small>{{ item.origin?.relative_path }}</small></td><td>{{ item.origin?.root_name || '—' }}</td><td>{{ item.source_language }}</td><td>{{ batchStatus(item.status) }}<small v-if="item.error" class="workflow-run-error">{{ batchError(item.error) }}</small><small>{{ item.run_id }}</small></td></tr></tbody></table></div>
        <nav class="batch-actions" :aria-label="t('batch.details')"><button type="button" class="secondary-button" :disabled="batches.detailLoading || batches.items.offset === 0" @click="batches.select(batches.detail!.id, Math.max(0, batches.items.offset - batches.items.limit))">{{ t('batch.previous') }}</button><span>{{ pageLabel(batches.items) }}</span><button type="button" class="secondary-button" :disabled="batches.detailLoading || batches.items.offset + batches.items.limit >= batches.items.total" @click="batches.select(batches.detail!.id, batches.items.offset + batches.items.limit)">{{ t('batch.next') }}</button></nav>
      </section>
    </div>
  </CanvasDialog>
</template>
