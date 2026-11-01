<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import { resolveArtifactUrl, type WorkflowNodeRun } from '../stores/workflow'
import { previewRenderer } from '../workflow/previewRenderers'
const props = defineProps<{ renderer: string; record?: WorkflowNodeRun; runId?: string; audioId?: string; historical: boolean }>()
const rendererId = computed(() => props.record?.preview?.renderer || props.renderer)
const renderer = computed(() => previewRenderer(rendererId.value))
const url = computed(() => props.record?.preview?.renderer === props.renderer ? resolveArtifactUrl(props.record.preview.content_url) : '')
const identity = computed(() => `${props.runId}/${props.audioId}/${props.record?.node_id}/${rendererId.value}`)
</script>
<template>
  <div class="node-preview" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop>
    <span v-if="runId" class="node-preview-version">{{ t(historical ? 'workflow.previewHistorical' : 'workflow.previewCurrent', { id: runId || '—' }) }}</span>
    <p v-if="!renderer" role="alert">{{ t('workflow.previewUnsupported', { renderer: rendererId }) }}</p>
    <p v-else-if="!record?.preview">{{ t('workflow.previewEmpty') }}</p>
    <p v-else-if="!url" role="alert">{{ t('workflow.previewFailed') }}</p>
    <component :is="renderer" v-else :key="identity" :url="url" :identity="identity" />
  </div>
</template>
