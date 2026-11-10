<script setup lang="ts">
import { computed, useId } from 'vue'
import LanguageCombobox from './LanguageCombobox.vue'
import type { CapabilityLanguage } from '../workflow/capabilities'
import { t } from '../i18n'
import type { CapabilityField } from '../workflow/capabilities'

const props = defineProps<{ field: CapabilityField; text: string; disabled: boolean; error?: string; hidden?: boolean; connection?: boolean; connectable?: boolean; socketDisabled?: boolean; socketLabel?: string; socketStyle?: Record<string, string> | undefined; placeholder?: string; languages?: readonly CapabilityLanguage[] }>()
const emit = defineEmits<{ edit: [value: string]; socketStart: [port: string, event: PointerEvent]; socketEnd: [event: PointerEvent]; socketCancel: [] }>()
const id = useId()
const hasSocket = computed(() => props.connectable && !!props.field.inputPort)
</script>
<template>
  <div v-show="!hidden || connectable" class="node-config-row" :class="{ 'in-node-field': connectable, 'has-parameter-socket': hasSocket, 'is-connected': hasSocket && hidden, 'is-json': field.type === 'json' || field.widget === 'multiline', 'has-error': !!error }">
    <button v-if="hasSocket" type="button" class="node-port node-field-socket" data-port-direction="input" :data-port-id="field.inputPort" :data-field-port="field.inputPort" :style="socketStyle" :disabled="socketDisabled" :aria-label="socketLabel || t('workflow.inputPort', { port: field.label })" :title="socketLabel || field.label" @pointerdown.stop="emit('socketStart', field.inputPort!, $event)" @pointerup.stop="emit('socketEnd', $event)" @click.stop @keydown.esc.stop.prevent="emit('socketCancel')"><span aria-hidden="true"></span></button>
    <label class="node-config-label" :for="`${id}-input`" :title="field.label">{{ field.label }}{{ field.nullable ? t('workflow.optionalSuffix') : '' }}</label>
    <div v-show="!hidden" class="node-field-control">
    <select :id="`${id}-input`" v-if="field.type === 'select' || field.type === 'boolean'" :data-field="connection ? undefined : field.key" :data-connection-field="connection ? field.key : undefined" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" @change="emit('edit', ($event.target as HTMLInputElement).value)">
      <template v-if="field.type === 'boolean'"><option value="true">{{ t('workflow.yes') }}</option><option value="false">{{ t('workflow.no') }}</option></template>
      <template v-else><option v-if="!text && !field.options.some((option) => option.value === '')" value="">{{ placeholder || t('connections.chooseValue') }}</option><option v-if="text && !field.options.some((option) => option.value === text)" :value="text">{{ t('connections.unknownOption', { value: text }) }}</option><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></template>
    </select>
    <textarea :id="`${id}-input`" v-else-if="field.type === 'json' || field.widget === 'multiline'" :data-field="field.key" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" spellcheck="false" @input="emit('edit', ($event.target as HTMLInputElement).value)" />
    <LanguageCombobox v-else-if="field.suggestionSource === 'languages'" :model-value="text" :languages="languages" :disabled="disabled" :hidden="hidden" :allow-empty="!field.required && !field.minLength" :empty-label="t('languages.default')"
      :data-field="field.key" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" @update:model-value="emit('edit', $event)" />
    <input :id="`${id}-input`" v-else type="text" :inputmode="field.type === 'number' || field.type === 'integer' ? 'decimal' : undefined" :data-field="connection ? undefined : field.key" :data-connection-field="connection ? field.key : undefined" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :step="field.type === 'integer' ? 1 : undefined" @input="emit('edit', ($event.target as HTMLInputElement).value)" />
    </div>
    <small v-if="error && !hidden" :id="`${id}-error`" class="node-field-error">{{ error }}</small>
  </div>
</template>
