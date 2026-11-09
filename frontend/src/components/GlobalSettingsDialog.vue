<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import CanvasDialog from './CanvasDialog.vue'
import NodeConfigField from './NodeConfigField.vue'
import { t, displayError, hasMessage } from '../i18n'
import { loadSettings, saveSettings, type GlobalSettings } from '../composables/globalSettings'
import { createNodeRegistry } from '../workflow/nodeRegistry'
import type { Capabilities, CapabilityField, CapabilityLanguage } from '../workflow/capabilities'
const props = defineProps<{ open: boolean; credentialTypes: readonly string[]; capabilities?: Capabilities | null; languages?: readonly CapabilityLanguage[] }>()
const emit = defineEmits<{ close: [] }>()
const draft = ref<GlobalSettings | null>(null)
const active = ref('')
const loading = ref(false)
const saving = ref(false)
const busy = computed(() => loading.value || saving.value)
let loadGeneration = 0
const error = ref('')
const secretVersion = ref(0)
const secrets = new Map<string, { operation: 'set'; api_key: string } | { operation: 'clear' }>()
let baseline = ''
let pendingRequest: { signature: string; id: string } | null = null
const dirty = computed(() => { void secretVersion.value; return !!draft.value && (signature() !== baseline || secrets.size > 0) })
function signature() { return JSON.stringify(draft.value && { global: draft.value.global, node_defaults: Object.fromEntries(Object.entries(draft.value.node_defaults).map(([type, node]) => [type, node.config])) }) }
function clearSecrets() { secrets.clear(); secretVersion.value++; pendingRequest = null }
function close() { if (saving.value || (dirty.value && !window.confirm(t('settings.discard')))) return; loadGeneration++; loading.value = false; clearSecrets(); draft.value = null; emit('close') }
async function refresh() {
  if (busy.value || (dirty.value && !window.confirm(t('settings.discard')))) return
  loading.value = true; error.value = ''; const generation = ++loadGeneration
  try { const value = await loadSettings(); if (generation !== loadGeneration || !props.open) return; draft.value = JSON.parse(JSON.stringify(value)); baseline = signature(); clearSecrets(); if (active.value && !draft.value?.node_defaults[active.value]) active.value = '' }
  catch (reason) { if (generation === loadGeneration && props.open) error.value = displayError(reason) }
  finally { if (generation === loadGeneration) loading.value = false }
}
watch(() => props.open, (open) => { if (open) void refresh(); else clearSecrets() }, { immediate: true })
onBeforeUnmount(() => { loadGeneration++; clearSecrets() })
const selected = computed(() => active.value ? draft.value?.node_defaults[active.value] : null)
const registry = computed(() => props.capabilities ? createNodeRegistry(props.capabilities) : null)
function nodeLabel(type: string, fallback: string) { return registry.value?.kind(type)?.label || fallback }
const fields = computed(() => (selected.value?.fields || draft.value?.global_fields || []).map(field => ({ ...field, label: selected.value ? registry.value?.kind(active.value)?.fields.find(item => item.key === field.key)?.label || field.label : hasMessage(`settings.${field.key}`) ? t(`settings.${field.key}`) : field.label })))
const config = computed(() => selected.value?.config || draft.value?.global || {})
function text(field: CapabilityField) { const value = config.value[field.key]; return value == null ? '' : field.type === 'json' && typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value) }
function edit(field: CapabilityField, input: string) {
  let value: unknown = input
  if (field.type === 'boolean') value = input === 'true'
  if (['integer', 'number'].includes(field.type) && input.trim() && Number.isFinite(Number(input))) value = Number(input)
  if (field.type === 'json') { try { value = JSON.parse(input) } catch { /* retain incomplete input */ } }
  if (!input && field.default === null) value = null
  config.value[field.key] = value
}
function secret() { void secretVersion.value; return secrets.get(active.value) }
function secretText() { const entry = secret(); return entry?.operation === 'set' ? entry.api_key : '' }
function editSecret(value: string) { if (value) secrets.set(active.value, { operation: 'set', api_key: value }); else secrets.delete(active.value); secretVersion.value++ }
function clearSecret() { secrets.set(active.value, { operation: 'clear' }); secretVersion.value++ }
async function save() {
  if (!draft.value || busy.value) return
  const payload = { schema_version: draft.value.schema_version, expected_revision: draft.value.revision, global: draft.value.global, node_defaults: Object.fromEntries(Object.entries(draft.value.node_defaults).map(([type, node]) => [type, node.config])), credential_updates: [...secrets].map(([node_type, update]) => ({ node_type, ...update })) }
  const signature = JSON.stringify(payload)
  if (!pendingRequest || pendingRequest.signature !== signature) pendingRequest = { signature, id: crypto.randomUUID() }
  saving.value = true; error.value = ''
  try { draft.value = JSON.parse(JSON.stringify(await saveSettings({ ...payload, request_id: pendingRequest.id }))); baseline = JSON.stringify({ global: draft.value!.global, node_defaults: Object.fromEntries(Object.entries(draft.value!.node_defaults).map(([type, node]) => [type, node.config])) }); clearSecrets() }
  catch (reason) { let text = (reason as { status?: number }).status === 409 ? t('settings.conflict') : displayError(reason); for (const update of secrets.values()) if (update.operation === 'set') text = text.split(update.api_key).join('[redacted]'); error.value = text }
  finally { saving.value = false }
}
</script>
<template>
  <CanvasDialog class="global-settings-dialog" :open="open" :title="t('settings.title')" :close-disabled="saving" @close="close">
    <div v-if="error" class="alert" role="alert">{{ error }}</div>
    <p v-if="busy && !draft" role="status">{{ t('settings.loading') }}</p>
    <template v-if="draft">
      <nav class="settings-tabs" :aria-label="t('settings.title')"><button type="button" :class="{ active: !active }" :disabled="busy" @click="active = ''">{{ t('settings.general') }}</button><button v-for="(node, type) in draft.node_defaults" :key="type" type="button" :class="{ active: active === type }" :disabled="busy" @click="active = String(type)">{{ nodeLabel(String(type), node.label) }}</button></nav>
      <div class="settings-fields"><NodeConfigField v-for="field in fields" :key="`${active}:${field.key}`" :field="field" :text="text(field)" :languages="languages" :disabled="busy" @edit="edit(field, $event)" />
        <div v-if="selected && credentialTypes.includes(active)" class="node-credential-group">
          <label class="node-config-row"><span class="node-config-label">{{ t('connections.apiKey') }}</span><input type="password" data-settings-key autocomplete="new-password" :aria-label="t('connections.apiKey')" :value="secretText()" :disabled="busy" :placeholder="t('connections.keyUnchanged')" @input="editSecret(($event.target as HTMLInputElement).value)" /></label>
          <p class="node-credential-status" role="status">{{ secret()?.operation === 'clear' ? t('connections.keyClearing') : secret()?.operation === 'set' ? t('connections.keyPending') : selected.credential_configured ? t('connections.keyStored') : t('connections.keyNotStoredDirect') }}</p><button type="button" class="text-button" :disabled="busy" @click="clearSecret">{{ t('connections.clearKey') }}</button>
        </div>
      </div>
    </template>
    <div class="settings-actions"><button type="button" class="secondary-button" :disabled="busy" @click="refresh">{{ t('settings.refresh') }}</button><button type="button" class="primary-button" :disabled="busy || !dirty" @click="save">{{ t('settings.save') }}</button></div>
  </CanvasDialog>
</template>
