<script setup lang="ts">
import { t } from '../i18n'
import type { Audio } from '../stores/projects'
import type { BatchManifest } from '../composables/useBatchManifest'
import AudioManifest from './AudioManifest.vue'
import NodeResourceInput from './NodeResourceInput.vue'
defineProps<{ projectId: string; audios: readonly Audio[]; value: unknown; sourceLanguage?: string | null; resourceType: string; locked: boolean; mode: string; manifest: BatchManifest }>()
const emit = defineEmits<{ mode: [value: string]; bind: [id: string | null]; uploaded: [audio: Audio]; busy: [value: boolean] }>()
function modeChange(event: Event) { emit('mode', (event.target as HTMLSelectElement).value) }
</script>
<template>
  <div class="node-audio-collection" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop>
    <label class="collection-mode">{{ t('batch.inputMode') }}<select data-collection-mode :value="mode" :disabled="locked" @change="modeChange"><option value="context">{{ t('batch.modeContext') }}</option><option v-if="mode === 'single'" value="single">{{ t('batch.modeSingle') }}</option><option value="collection">{{ t('batch.modeCollection') }}</option></select></label>
    <AudioManifest v-if="mode === 'collection'" :manifest="manifest" :audios="audios" :locked="locked" />
    <template v-else>
      <NodeResourceInput :project-id="projectId" :audios="audios" :value="value" :source-language="sourceLanguage" :resource-type="resourceType" :locked="locked" @bind="emit('bind', $event)" @uploaded="emit('uploaded', $event)" @busy="emit('busy', $event)" />
    </template>
  </div>
</template>
