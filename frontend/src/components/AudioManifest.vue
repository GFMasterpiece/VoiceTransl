<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { displayError, locale, t } from '../i18n'
import type { Audio } from '../stores/projects'
import type { BatchManifest } from '../composables/useBatchManifest'
import { batchStatus } from '../composables/useBatches'
const props = defineProps<{ manifest: BatchManifest; audios: readonly Audio[]; locked: boolean }>()
const manifest = reactive(props.manifest)
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
const existing = ref('')
const manifestOffset = ref(0)
const manifestPage = computed(() => manifest.items.slice(manifestOffset.value, manifestOffset.value + 20))
watch(() => manifest.items.length, (length) => { if (manifestOffset.value >= length) manifestOffset.value = Math.max(0, Math.floor((length - 1) / 20) * 20) })
const locked = computed(() => props.locked || manifest.uploading)
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
function pageLabel(page: { total: number; offset: number; items: unknown[] }) { return t('batch.pagination', { from: page.total ? page.offset + 1 : 0, to: page.offset + page.items.length, total: page.total }) }
</script>
<template>      <section class="batch-manifest">
        <details><summary>{{ t('batch.listHelp') }}</summary><p class="inspector-help">{{ t('batch.localHint') }}</p><p class="inspector-help">{{ t('batch.uploadedHint') }}</p></details>
        <div class="batch-actions">
          <button type="button" class="secondary-button" data-batch-files :disabled="locked" @click="fileInput?.click()">{{ t('batch.files') }}</button>
          <button type="button" class="secondary-button" data-batch-folder :disabled="locked" @click="folderInput?.click()">{{ t('batch.folder') }}</button>
          <label class="batch-check"><input v-model="manifest.recursive" type="checkbox" :disabled="locked" data-batch-recursive />{{ t('batch.recursive') }}</label>
        </div>
        <input ref="fileInput" type="file" multiple class="visually-hidden" data-batch-file-input :disabled="locked" :aria-label="t('batch.files')" :accept="manifest.policy?.allowed_extensions.map((value) => `.${value}`).join(',')" @change="choose($event, false)" />
        <input ref="folderInput" type="file" webkitdirectory multiple class="visually-hidden" data-batch-folder-input :disabled="locked" :aria-label="t('batch.folder')" @change="choose($event, true)" />
        <div class="batch-fields">
          <label>{{ t('batch.defaultLanguage') }}<input v-model="manifest.language" type="text" :disabled="locked" data-batch-language :placeholder="t('app.create.sourcePlaceholder')" /></label>
          <button type="button" class="secondary-button" :disabled="locked" @click="manifest.applyLanguage">{{ t('batch.applyLanguage') }}</button>
          <label>{{ t('batch.chooseExisting') }}<select data-manifest-existing v-model="existing" :disabled="locked"><option value="">{{ t('batch.choose') }}</option><option v-for="audio in audios" :key="audio.id" :value="audio.id">{{ audio.source.original_name }} · {{ audio.source.language }}</option></select></label>
          <button type="button" class="secondary-button" :disabled="locked || !existing" data-manifest-add-existing @click="addExisting">{{ t('batch.addExisting') }}</button>
        </div>
        <p v-if="manifest.policyLoading" role="status">{{ t('batch.policyLoading') }}</p>
        <p v-if="manifest.error.text" role="alert" class="workflow-run-error">{{ manifest.error.text }}</p>
        <button v-if="!manifest.policy" type="button" class="text-button" :disabled="manifest.policyLoading || locked" @click="manifest.loadPolicy">{{ t('batch.policyRetry') }}</button>
        <p v-if="!manifest.items.length">{{ t('batch.noFiles') }}</p>
        <div v-else class="batch-table-scroll">
          <table class="batch-table manifest-table"><thead><tr><th>{{ t('batch.selected') }}</th><th>{{ t('batch.name') }}</th><th>{{ t('batch.language') }}</th><th>{{ t('batch.state') }}</th><th /></tr></thead>
            <tbody><tr v-for="item in manifestPage" :key="item.id" :class="{ 'batch-excluded': !manifest.included(item) }" :data-batch-item="item.id">
              <td><input data-manifest-selected v-model="item.selected" type="checkbox" :disabled="locked" :aria-label="`${t('batch.selected')} ${item.name}`" /></td>
              <td><strong :title="item.name">{{ item.origin?.relative_path || item.name }}</strong><small :title="item.origin ? `${item.origin.root_name} · ${item.origin.root_id}` : undefined">{{ item.fileSelection ? t('batch.flatRoot') : item.origin?.root_name || '—' }} · {{ size(item.sizeBytes) }}</small></td>
              <td><input v-model="item.language" type="text" :disabled="locked || !!item.audioId" :aria-label="`${t('batch.language')} ${item.name}`" /></td>
              <td><span class="manifest-status">{{ manifest.validation(item) ? batchStatus('invalid') : batchStatus(item.status) }}</span><small v-if="manifest.validation(item)" class="workflow-run-error">{{ manifest.validation(item) }}</small><small v-if="item.error" class="workflow-run-error">{{ displayError(item.error) }}</small></td>
              <td><button type="button" class="text-button" :disabled="locked" :aria-label="`${t('batch.remove')} ${item.name}`" @click="manifest.remove(item.id)">×</button></td>
            </tr></tbody>
          </table>
        </div>
        <nav v-if="manifest.items.length" class="batch-actions" :aria-label="t('batch.manifestList')"><button type="button" class="secondary-button" :disabled="manifestOffset === 0" data-manifest-previous @click="manifestOffset = Math.max(0, manifestOffset - 20)">{{ t('batch.previous') }}</button><span>{{ pageLabel({ items: manifestPage, total: manifest.items.length, offset: manifestOffset }) }}</span><button type="button" class="secondary-button" :disabled="manifestOffset + 20 >= manifest.items.length" data-manifest-next @click="manifestOffset += 20">{{ t('batch.next') }}</button></nav>
        <div class="batch-actions">
          <button type="button" class="secondary-button" data-batch-upload :disabled="locked || !manifest.selected.some((item) => !item.audioId && !manifest.validation(item))" @click="manifest.upload()">{{ manifest.uploading ? t('batch.uploading') : t('batch.upload') }}</button>
          <button v-if="manifest.items.some((item) => item.status === 'failed')" type="button" class="secondary-button" :disabled="locked" data-manifest-retry-upload @click="manifest.upload(true)">{{ t('batch.retryUploads') }}</button>
        </div>
      </section>
</template>
