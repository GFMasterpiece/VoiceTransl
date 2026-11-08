<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { locale, t } from '../i18n'
import { batchActive, batchDone, batchError, batchStatus, batchTerminal, type Batches } from '../composables/useBatches'
import CanvasDialog from './CanvasDialog.vue'
import CloudWorkflowNotice from './CloudWorkflowNotice.vue'
const props = defineProps<{ open: boolean; batches: Batches; locked: boolean }>()
const emit = defineEmits<{ close: [] }>()
const batches = reactive(props.batches)
const includeCancelled = ref(false)
const locked = computed(() => props.locked || batches.submitting)
function date(value: string) { return Number.isNaN(Date.parse(value)) ? value : new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) }
function pageLabel(page: { total: number; offset: number; items: unknown[] }) { return t('batch.pagination', { from: page.total ? page.offset + 1 : 0, to: page.offset + page.items.length, total: page.total }) }</script>

<template>
  <CanvasDialog class="batch-dialog" :open="open" :title="t('batch.title')" @close="emit('close')">
    <div class="batch-panel" data-batch-panel>
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
        <CloudWorkflowNotice :workflow="batches.detail.workflow" :audio-count="batches.detail.total" historical />
        <h3>{{ batches.detail.id }} · {{ t('batch.snapshot', { revision: batches.detail.workflow_revision }) }}</h3>
        <div class="batch-table-scroll"><table class="batch-table"><thead><tr><th>#</th><th>{{ t('batch.name') }}</th><th>{{ t('batch.origin') }}</th><th>{{ t('batch.language') }}</th><th>{{ t('batch.state') }}</th></tr></thead><tbody><tr v-for="item in batches.items.items" :key="item.index" :data-batch-run-item="item.index"><td>{{ item.sequence ?? item.index + 1 }}</td><td>{{ item.audio_name }}<small>{{ item.origin?.relative_path }}</small></td><td>{{ item.origin?.root_name || '—' }}</td><td>{{ item.source_language }}</td><td>{{ batchStatus(item.status) }}<small v-if="item.error" class="workflow-run-error">{{ batchError(item.error) }}</small><small>{{ item.run_id }}</small></td></tr></tbody></table></div>
        <nav class="batch-actions" :aria-label="t('batch.details')"><button type="button" class="secondary-button" :disabled="batches.detailLoading || batches.items.offset === 0" @click="batches.select(batches.detail!.id, Math.max(0, batches.items.offset - batches.items.limit))">{{ t('batch.previous') }}</button><span>{{ pageLabel(batches.items) }}</span><button type="button" class="secondary-button" :disabled="batches.detailLoading || batches.items.offset + batches.items.limit >= batches.items.total" @click="batches.select(batches.detail!.id, batches.items.offset + batches.items.limit)">{{ t('batch.next') }}</button></nav>
      </section>
    </div>
  </CanvasDialog>
</template>
