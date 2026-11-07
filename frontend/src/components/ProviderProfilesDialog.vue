<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { message, messageRef, t } from '../i18n'
import { useProviderProfilesStore, type ProviderProfile } from '../stores/providerProfiles'
import { useWorkflowStore } from '../stores/workflow'
import { randomRequestId } from '../composables/useBatchManifest'
import CanvasDialog from './CanvasDialog.vue'
const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const profiles = useProviderProfilesStore()
const workflow = useWorkflowStore()
const types = computed(() => [...new Map((workflow.capabilities?.nodes || []).flatMap((node) => node.providerProfileInput ? [[node.providerProfileInput.providerType, node.providerProfileInput] as const] : [])).values()])
const form = ref<ProviderProfile | null>(null)
const creating = ref(false)
const catalogLoading = ref(false)
const catalogError = messageRef()
const notice = messageRef()
const locked = computed(() => profiles.busy || profiles.loading || catalogLoading.value)
let catalogGeneration = 0
async function refreshCatalog() {
  const generation = ++catalogGeneration
  catalogLoading.value = true; catalogError.set('')
  try { await workflow.refreshCapabilities() }
  catch (reason) { if (generation === catalogGeneration) catalogError.set(reason) }
  finally { if (generation === catalogGeneration) catalogLoading.value = false }
}
async function refresh() {
  notice.set('')
  await Promise.allSettled([profiles.load(), refreshCatalog()])
}
watch(() => props.open, (open) => {
  if (open) void refresh()
  else { catalogGeneration++; catalogLoading.value = false }
})
onUnmounted(() => { catalogGeneration++ })
function create() {
  if (locked.value || !types.value.length) return
  const type = types.value[0]!
  form.value = { schema_version: '1.0', id: `profile_${randomRequestId().replace(/-/g, '')}`, provider_type: type.providerType, display_name: '', ...type.defaults, region: null, enabled: true }
  creating.value = true; notice.set('')
}
function edit(profile: ProviderProfile) { if (!locked.value) { form.value = JSON.parse(JSON.stringify(profile)) as ProviderProfile; creating.value = false; notice.set('') } }
function changeType() {
  const type = types.value.find((type) => type.providerType === form.value?.provider_type)
  if (form.value && creating.value && type) Object.assign(form.value, type.defaults)
}
async function save() {
  if (!form.value || locked.value) return
  const payload = { ...form.value, display_name: form.value.display_name.trim(), base_url: form.value.base_url || null, api_key_env: form.value.api_key_env || null, region: form.value.region || null, default_timeout: Number(form.value.default_timeout) }
  try {
    const value = await profiles.save(payload, creating.value)
    if (!value) return
    form.value = { ...value }; creating.value = false
    notice.set(message('profiles.saved'))
    await Promise.allSettled([profiles.load(), refreshCatalog()])
    if (catalogError.text.value) notice.set(message('profiles.reloadFailed'))
  } catch { /* Preserve the form and the store's original API error. */ }
}
async function remove(profile: ProviderProfile) {
  if (locked.value) return
  try {
    if (!await profiles.remove(profile.id)) return
    if (form.value?.id === profile.id) form.value = null
    notice.set(message('profiles.removed'))
    await Promise.allSettled([profiles.load(), refreshCatalog()])
    if (catalogError.text.value) notice.set(message('profiles.reloadFailed'))
  } catch { /* A rejected delete preserves its row and editor. */ }
}
</script>
<template>
  <CanvasDialog class="profiles-dialog" :open="open" :title="t('profiles.title')" :close-disabled="profiles.busy" @close="emit('close')">
    <div data-profile-manager>
      <p class="inspector-help">{{ t('profiles.envHint') }}</p><p class="inspector-help">{{ t('profiles.enableHint') }}</p>
      <div class="profile-actions"><button type="button" class="secondary-button" data-profile-new :disabled="locked || !types.length" @click="create">{{ t('profiles.new') }}</button><button type="button" class="text-button" data-profile-refresh :disabled="locked || profiles.loading" @click="refresh">{{ t('profiles.refresh') }}</button></div>
      <p v-if="profiles.error" class="workflow-run-error" role="alert">{{ profiles.error }}</p><p v-if="catalogError.text.value" class="workflow-run-error" role="alert">{{ catalogError.text.value }}</p><p v-if="notice.text.value" role="status">{{ notice.text.value }}</p>
      <p v-if="profiles.loading" role="status">{{ t('profiles.loading') }}</p>
      <p v-if="!types.length && !catalogLoading">{{ t('profiles.noTypes') }}</p>
      <p v-if="!profiles.items.length && !profiles.loading">{{ t('profiles.empty') }}</p>
      <div class="profile-list"><article v-for="profile in profiles.items" :key="profile.id" :data-profile-record="profile.id" class="profile-record"><div><strong>{{ profile.display_name }}</strong><small>{{ profile.provider_type }} · {{ profile.id }}</small><small>{{ profile.base_url }} · {{ profile.region || t('profiles.unknownRegion') }}</small><small v-if="!profile.enabled">{{ t('profiles.disabled') }}</small></div><div class="profile-actions"><button type="button" class="text-button" :data-profile-edit="profile.id" :disabled="locked" @click="edit(profile)">{{ t('profiles.edit') }}</button><button type="button" class="text-button danger-button" :data-profile-delete="profile.id" :disabled="locked" @click="remove(profile)">{{ t('profiles.remove') }}</button></div></article></div>
      <form v-if="form" class="profile-form" @submit.prevent="save">
        <label>{{ t('profiles.name') }}<input v-model="form.display_name" data-profile-field="display_name" type="text" required :disabled="locked" /></label>
        <label>{{ t('profiles.type') }}<select v-model="form.provider_type" data-profile-field="provider_type" :disabled="locked || !creating" @change="changeType"><option v-for="type in types" :key="type.providerType" :value="type.providerType">{{ type.label }}</option><option v-if="!types.some((type) => type.providerType === form!.provider_type)" :value="form.provider_type">{{ form.provider_type }} · {{ t('profiles.unsupportedType') }}</option></select></label>
        <label>{{ t('profiles.id') }}<input :value="form.id" data-profile-field="id" type="text" readonly /></label>
        <label>{{ t('profiles.endpoint') }}<input v-model="form.base_url" data-profile-field="base_url" type="url" :disabled="locked" /></label>
        <label>{{ t('profiles.env') }}<input v-model="form.api_key_env" data-profile-field="api_key_env" type="text" pattern="[A-Za-z_][A-Za-z0-9_]*" autocomplete="off" :disabled="locked" /></label>
        <label>{{ t('profiles.region') }}<input v-model="form.region" data-profile-field="region" type="text" :disabled="locked" /></label>
        <label>{{ t('profiles.timeout') }}<input v-model.number="form.default_timeout" data-profile-field="default_timeout" type="number" :disabled="locked" /></label>
        <label class="profile-check"><input v-model="form.enabled" data-profile-field="enabled" type="checkbox" :disabled="locked" />{{ t('profiles.enabled') }}</label>
        <button type="submit" class="primary-button" data-profile-save :disabled="locked">{{ t('profiles.save') }}</button>
      </form>
    </div>
  </CanvasDialog>
</template>
