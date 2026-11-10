<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import type { AudioGroup, AudioGroupSummary } from '../composables/useProjectMedia'
const props = defineProps<{ value: unknown; groups: Record<string, AudioGroupSummary>; details: Record<string, AudioGroup>; error?: string; legacy?: string; source: boolean; locked: boolean; runReason?: string }>()
const emit = defineEmits<{ bind: [id: string]; manage: []; run: [] }>()
const selectionTitle = computed(() => typeof props.value === 'string' && props.value ? props.details[props.value]?.name || props.groups[props.value]?.name || props.value : props.legacy || t('media.chooseGroup'))
const current = computed(() => typeof props.value === 'string' ? props.details[props.value] : undefined)
</script>
<template><div class="node-group-input" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop>
  <label class="node-config-row in-node-field"><span class="node-config-label">{{ t('media.group') }}</span><select class="node-field-control" :title="selectionTitle" data-node-group :value="value || ''" :disabled="locked" @change="emit('bind', ($event.target as HTMLSelectElement).value)"><option value="">{{ legacy || t('media.chooseGroup') }}</option><option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }} · {{ group.audio_count }}</option><option v-if="value && !groups[String(value)]" :value="String(value)">{{ String(value) }}</option></select></label>
  <small v-if="current" class="node-group-badge">{{ t('media.groupMembers', { count: current.audio_ids.length, mode: t(`media.mode_${current.mode}`) }) }}</small>
  <small v-if="error" class="node-field-error" role="alert">{{ error }}</small>
  <div class="node-group-actions"><button class="secondary-button compact-button" type="button" data-node-manage-media :disabled="locked" @click="emit('manage')">{{ t('media.manage') }}</button><button v-if="source" class="primary-button compact-button" type="button" data-run-source :disabled="!!runReason" :title="runReason" @click="emit('run')">{{ t('media.runSource') }}</button></div>
</div></template>
