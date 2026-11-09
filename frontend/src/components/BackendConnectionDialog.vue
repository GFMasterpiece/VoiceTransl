<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import CanvasDialog from './CanvasDialog.vue'
import { API_BASE_URL } from '../api'
import { DEFAULT_API_BASE_URL, candidateBackendAddress, persistBackendAddress, probeBackend, reloadBackendPage } from '../backendConnection'
import { displayError, t } from '../i18n'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; switching: [] }>()
const address = ref(API_BASE_URL)
const probing = ref(false)
const switching = ref(false)
const feedback = ref<'connected' | ''>('')
const error = ref('')
let generation = 0
let controller: AbortController | null = null
function cancelProbe() { generation++; controller?.abort(); controller = null; probing.value = false; feedback.value = ''; error.value = '' }
watch(address, cancelProbe, { flush: 'sync' })
watch(() => props.open, open => { cancelProbe(); if (open) address.value = API_BASE_URL }, { immediate: true })
function close() { if (switching.value) return; cancelProbe(); emit('close') }
function reset() { if (switching.value) return; cancelProbe(); address.value = DEFAULT_API_BASE_URL }
async function connect(save: boolean) {
  if (probing.value || switching.value) return
  cancelProbe()
  let base: string
  try { base = candidateBackendAddress(address.value) } catch (reason) { error.value = displayError(reason); return }
  const ticket = generation; const request = new AbortController(); controller = request; probing.value = true
  try {
    await probeBackend(base, { signal: request.signal })
    if (ticket !== generation || !props.open) return
    feedback.value = 'connected'
    if (save) {
      persistBackendAddress(base)
      switching.value = true; emit('switching')
      await nextTick()
      reloadBackendPage()
    }
  } catch (reason) { if (ticket === generation && props.open) error.value = displayError(reason) }
  finally { if (ticket === generation) probing.value = false }
}
onBeforeUnmount(cancelProbe)
</script>
<template>
  <CanvasDialog class="backend-connection-dialog" :open="open" :title="t('backend.title')" :close-disabled="switching" @close="close">
    <form @submit.prevent="connect(true)">
      <label class="node-config-row"><span class="node-config-label">{{ t('backend.address') }}</span><input v-model="address" data-backend-address type="text" inputmode="url" autocomplete="url" spellcheck="false" :aria-label="t('backend.address')" :disabled="switching" /></label>
      <p v-if="error" class="create-error" role="alert">{{ error }}</p>
      <p v-else-if="probing || feedback || switching" class="backend-connection-feedback" role="status">{{ t(switching ? 'backend.switching' : probing ? 'backend.checking' : 'backend.connected') }}</p>
      <div class="backend-connection-actions"><button class="text-button" type="button" data-backend-reset :disabled="switching" @click="reset">{{ t('backend.reset') }}</button><button class="secondary-button" type="button" data-backend-test :disabled="probing || switching" @click="connect(false)">{{ t('backend.test') }}</button><button class="primary-button" type="submit" data-backend-save :disabled="probing || switching">{{ t('backend.save') }}</button></div>
    </form>
  </CanvasDialog>
</template>
