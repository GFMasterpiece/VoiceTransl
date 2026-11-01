<script setup lang="ts">
import { ref, watch } from 'vue'
import { t } from '../i18n'
const props = defineProps<{ url: string; identity: string }>()
const failed = ref(false)
const attempt = ref(0)
watch(() => [props.url, props.identity], () => { failed.value = false; attempt.value = 0 })
function retry() { attempt.value++; failed.value = false }
</script>
<template>
  <div v-if="failed" class="node-preview-error"><p role="alert">{{ t('workflow.previewFailed') }}</p><button type="button" @click="retry">{{ t('workflow.previewRetry') }}</button></div>
  <audio v-else :key="`${identity}/${attempt}`" controls preload="metadata" :src="url" @error="failed = true" />
</template>
