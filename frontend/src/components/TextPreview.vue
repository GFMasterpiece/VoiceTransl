<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { t } from '../i18n'
const props = defineProps<{ url: string; identity: string }>()
const text = ref('')
const loading = ref(false)
const failed = ref(false)
const attempt = ref(0)
let generation = 0
let controller: AbortController | null = null
watch(() => [props.url, props.identity, attempt.value], async () => {
  const token = ++generation
  controller?.abort()
  controller = new AbortController()
  text.value = ''; failed.value = false; loading.value = true
  try {
    const response = await fetch(props.url, { signal: controller.signal })
    if (!response.ok) throw new Error()
    const content = await response.text()
    if (token === generation) text.value = content
  } catch { if (token === generation) failed.value = true }
  finally { if (token === generation) loading.value = false }
}, { immediate: true })
onUnmounted(() => { generation += 1; controller?.abort() })
</script>
<template>
  <p v-if="loading" role="status">{{ t('workflow.previewLoading') }}</p>
  <div v-else-if="failed" class="node-preview-error"><p role="alert">{{ t('workflow.previewFailed') }}</p><button type="button" @click="attempt++">{{ t('workflow.previewRetry') }}</button></div>
  <pre v-else>{{ text }}</pre>
</template>
