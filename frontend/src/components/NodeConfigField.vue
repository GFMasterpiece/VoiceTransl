<script setup lang="ts">
import { useId } from 'vue'
import LanguageCombobox from './LanguageCombobox.vue'
import type { CapabilityLanguage } from '../workflow/capabilities'
import { t } from '../i18n'
import type { CapabilityField } from '../workflow/capabilities'

defineProps<{ field: CapabilityField; text: string; disabled: boolean; error?: string; hidden?: boolean; connection?: boolean; placeholder?: string; languages?: readonly CapabilityLanguage[] }>()
const emit = defineEmits<{ edit: [value: string] }>()
const id = useId()
</script>
<template>
  <label v-show="!hidden" class="node-config-row" :class="{ 'is-json': field.type === 'json' || field.widget === 'multiline', 'has-error': !!error }">
    <span class="node-config-label" :title="field.label">{{ field.label }}</span>
    <select v-if="field.type === 'select' || field.type === 'boolean'" :data-field="connection ? undefined : field.key" :data-connection-field="connection ? field.key : undefined" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" @change="emit('edit', ($event.target as HTMLInputElement).value)">
      <template v-if="field.type === 'boolean'"><option value="true">{{ t('workflow.yes') }}</option><option value="false">{{ t('workflow.no') }}</option></template>
      <template v-else><option v-if="!text && !field.options.some((option) => option.value === '')" value="">{{ placeholder || t('connections.chooseValue') }}</option><option v-if="text && !field.options.some((option) => option.value === text)" :value="text">{{ t('connections.unknownOption', { value: text }) }}</option><option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option></template>
    </select>
    <textarea v-else-if="field.type === 'json' || field.widget === 'multiline'" :data-field="field.key" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" spellcheck="false" @input="emit('edit', ($event.target as HTMLInputElement).value)" />
    <LanguageCombobox v-else-if="field.suggestionSource === 'languages'" :model-value="text" :languages="languages" :disabled="disabled" :hidden="hidden" :allow-empty="!field.required && !field.minLength" :empty-label="t('languages.default')"
      :data-field="field.key" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" @update:model-value="emit('edit', $event)" />
    <input v-else type="text" :inputmode="field.type === 'number' || field.type === 'integer' ? 'decimal' : undefined" :data-field="connection ? undefined : field.key" :data-connection-field="connection ? field.key : undefined" :value="text" :disabled="disabled" :aria-label="field.label" :aria-invalid="!!error" :aria-describedby="error ? `${id}-error` : undefined" :min="field.minimum ?? undefined" :max="field.maximum ?? undefined" :step="field.type === 'integer' ? 1 : undefined" @input="emit('edit', ($event.target as HTMLInputElement).value)" />
    <small v-if="error" :id="`${id}-error`" class="node-field-error">{{ error }}</small>
  </label>
</template>
