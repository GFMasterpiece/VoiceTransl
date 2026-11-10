<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import CanvasDialog from './CanvasDialog.vue'
import NodeConfigField from './NodeConfigField.vue'
import { t, displayError, hasMessage, locale, setLocale } from '../i18n'
import { loadSettings, saveSettings, type GlobalSettings } from '../composables/globalSettings'
import { debugMode, setDebugMode, skipNodeDeleteConfirmation, setSkipNodeDeleteConfirmation } from '../composables/editorPreferences'
import { isNodeVisible, nodeCategoryLabel } from '../workflow/nodeCategories'
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
interface SettingsGroup { id: string | null; label: string; types: string[] }
const rememberedTypes = new Map<string | null, string>()
const groups = computed(() => {
  const result = new Map<string | null, SettingsGroup>()
  for (const type of Object.keys(draft.value?.node_defaults || {})) {
    const kind = registry.value?.kind(type)
    if (!isNodeVisible(kind || undefined, debugMode.value)) continue
    const category = kind?.category
    const id = category?.id ?? null
    if (!result.has(id)) result.set(id, { id, label: nodeCategoryLabel(category), types: [] })
    result.get(id)!.types.push(type)
  }
  return [...result.values()]
})
const activeGroup = computed(() => groups.value.find(group => group.types.includes(active.value)))
function selectType(type: string) { active.value = type; const group = groups.value.find(group => group.types.includes(type)); if (group) rememberedTypes.set(group.id, type) }
function selectCategory(group: SettingsGroup) { const previous = rememberedTypes.get(group.id); selectType(previous && group.types.includes(previous) ? previous : group.types[0]!) }
watch(groups, visible => { if (active.value && !visible.some(group => group.types.includes(active.value))) active.value = '' }, { flush: 'sync' })
const fields = computed(() => (selected.value?.fields || draft.value?.global_fields || []).map(field => ({ ...field, label: selected.value ? registry.value?.kind(active.value)?.fields.find(item => item.key === field.key)?.label || field.label : hasMessage(`settings.${field.key}`) ? t(`settings.${field.key}`) : field.label })))
const connectionKeys = computed(() => { const descriptor = registry.value?.kind(active.value)?.inlineConnectionInput; return descriptor ? [descriptor.baseUrlKey, descriptor.timeoutKey, descriptor.regionKey].filter((key): key is string => !!key) : [] })
const connectionFields = computed(() => connectionKeys.value.flatMap(key => fields.value.filter(field => field.key === key)))
const parameterFields = computed(() => fields.value.filter(field => !connectionKeys.value.includes(field.key)))
const hasConnection = computed(() => !!selected.value && (!!connectionFields.value.length || props.credentialTypes.includes(active.value)))
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
function changeDebugMode(event: Event) { setDebugMode((event.target as HTMLInputElement).checked) }
function changeLocale(event: Event) { setLocale((event.target as HTMLSelectElement).value) }
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
    <nav class="settings-tabs" data-settings-tabs :aria-label="t('settings.title')"><button type="button" data-settings-general :class="{ active: !active }" :disabled="busy" @click="active = ''">{{ t('settings.general') }}</button><button v-for="group in groups" :key="group.id ?? ''" type="button" :data-settings-category="group.id ?? ''" :class="{ active: activeGroup?.id === group.id && !!active }" :disabled="busy" @click="selectCategory(group)">{{ group.label }}</button></nav>
    <div class="settings-content" data-settings-content>
      <div v-if="error" class="alert" role="alert">{{ error }}</div>
      <p v-if="busy && !draft" role="status">{{ t('settings.loading') }}</p>
      <div class="settings-fields">
        <template v-if="!active">
          <label class="node-config-row"><span class="node-config-label">{{ t('app.language') }}</span><select class="locale-picker" data-settings-locale :value="locale" :aria-label="t('app.language')" @change="changeLocale"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select></label>
          <label class="settings-debug-control"><span>{{ t('settings.debugMode') }}</span><input type="checkbox" data-settings-debug :checked="debugMode" @change="changeDebugMode" /></label>
          <label class="settings-debug-control"><span>{{ t('settings.skipNodeDeleteConfirmation') }}</span><input type="checkbox" data-settings-skip-node-delete-confirmation :checked="skipNodeDeleteConfirmation" @change="setSkipNodeDeleteConfirmation(($event.target as HTMLInputElement).checked)" /></label>
        </template>
        <label v-if="activeGroup" class="node-config-row"><span class="node-config-label">{{ t('settings.nodeType') }}</span><select data-settings-node-type :value="active" :disabled="busy" :aria-label="t('settings.nodeType')" @change="selectType(($event.target as HTMLSelectElement).value)"><option v-for="type in activeGroup.types" :key="type" :value="type">{{ nodeLabel(type, draft!.node_defaults[type]!.label) }}</option></select></label>
        <template v-if="draft">
          <section v-if="hasConnection" class="settings-field-section" data-settings-section="connection">
            <h4 class="node-config-section-title">{{ t('connections.connectionSettings') }}</h4>
            <NodeConfigField v-for="field in parameterFields" :key="`${active}:${field.key}`" :field="field" :text="text(field)" :languages="languages" :disabled="busy" @edit="edit(field, $event)" />
            <NodeConfigField v-for="field in connectionFields" :key="`${active}:${field.key}`" :field="field" :text="text(field)" :languages="languages" :disabled="busy" @edit="edit(field, $event)" />
            <div v-if="selected && credentialTypes.includes(active)" class="node-credential-group">
              <label class="node-config-row"><span class="node-config-label">{{ t('connections.apiKey') }}</span><input type="password" data-settings-key autocomplete="new-password" :aria-label="t('connections.apiKey')" :value="secretText()" :disabled="busy" :placeholder="t('connections.keyUnchanged')" @input="editSecret(($event.target as HTMLInputElement).value)" /></label>
              <p class="node-credential-status" role="status">{{ secret()?.operation === 'clear' ? t('connections.keyClearing') : secret()?.operation === 'set' ? t('connections.keyPending') : selected.credential_configured ? t('connections.keyStored') : t('connections.keyNotStoredDirect') }}</p><button type="button" class="text-button" :disabled="busy" @click="clearSecret">{{ t('connections.clearKey') }}</button>
            </div>
          </section>
          <section v-if="!hasConnection && parameterFields.length" class="settings-field-section" :data-settings-section="active ? 'parameters' : 'general'">
            <NodeConfigField v-for="field in parameterFields" :key="`${active}:${field.key}`" :field="field" :text="text(field)" :languages="languages" :disabled="busy" @edit="edit(field, $event)" />
          </section>
        </template>
      </div>
    </div>
    <div class="settings-actions" data-settings-actions><button type="button" class="secondary-button" :disabled="busy" @click="refresh">{{ t('settings.refresh') }}</button><button type="button" class="primary-button" :disabled="busy || !dirty" @click="save">{{ t('settings.save') }}</button></div>
  </CanvasDialog>
</template>
