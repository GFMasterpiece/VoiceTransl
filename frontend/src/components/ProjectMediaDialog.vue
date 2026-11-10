<script setup lang="ts">
import { computed, reactive, ref, provide, nextTick, watch } from 'vue'
import { languageSuggestionsKey, useLanguageSuggestions } from '../composables/languageSuggestions'
import { t } from '../i18n'
import type { Audio } from '../stores/projects'
import type { BatchManifest } from '../composables/useBatchManifest'
import type { ProjectMedia } from '../composables/useProjectMedia'
import AudioManifest from './AudioManifest.vue'
import CanvasDialog from './CanvasDialog.vue'
const props = defineProps<{ media: ProjectMedia; manifest: BatchManifest; audios: readonly Audio[]; locked: boolean }>()
const emit = defineEmits<{ close: [] }>()
const media = reactive(props.media), manifest = reactive(props.manifest)
const tab = ref<'assets' | 'groups'>('assets'), confirming = ref(false), memberOffset = ref(0)
let action: (() => void) | null = null
const editor = ref<HTMLElement | null>(null), keepEditing = ref<HTMLButtonElement | null>(null)
let previousFocus: HTMLElement | null = null, previousScroll = 0
function cancelConfirmation() { action = null; confirming.value = false }
function close() { if (confirming.value) cancelConfirmation(); else request(() => emit('close')) }
watch(confirming, async (value) => {
  await nextTick()
  if (confirming.value !== value) return
  const body = (value ? keepEditing.value : editor.value)?.closest('.canvas-dialog-body')
  if (body) body.scrollTop = value ? 0 : previousScroll
  if (value) keepEditing.value?.focus({ preventScroll: true })
  else if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
})
const busy = computed(() => props.locked || media.saving || manifest.uploading)
const inheritedLanguages = useLanguageSuggestions()
provide(languageSuggestionsKey, { languages: inheritedLanguages?.languages || computed(() => undefined), suspended: computed(() => busy.value || confirming.value), context: inheritedLanguages?.context })
const unsaved = computed(() => media.dirty || media.uncertain || manifest.pendingLocal)
const valid = computed(() => !!media.draft.name.trim() && new TextEncoder().encode(media.draft.background).length <= 65536)
const members = computed(() => media.draft.audio_ids.slice(memberOffset.value, memberOffset.value + 20))
function request(actionToRun: () => void) {
  if (busy.value || confirming.value) return
  if (unsaved.value) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    previousScroll = editor.value?.closest('.canvas-dialog-body')?.scrollTop || 0
    action = actionToRun; confirming.value = true
  } else actionToRun()
}
function discard() { media.discard(); manifest.discardLocal(); confirming.value = false; const next = action; action = null; next?.() }
function name(id: string) { return props.audios.find(audio => audio.id === id)?.source.original_name || media.audioPage.items.find(audio => audio.id === id)?.source.original_name || id }
function select(id: string, checked: boolean) { media.selected = checked ? [...new Set([...media.selected, id])] : media.selected.filter(value => value !== id) }
function pagination(page: { total: number; offset: number; items: unknown[] }) { return t('batch.pagination', { from: page.total ? page.offset + 1 : 0, to: page.offset + page.items.length, total: page.total }) }
</script>
<template>
  <CanvasDialog class="project-media-dialog" :title="t('media.title')" :close-disabled="busy" @close="close">
    <div class="project-media" data-project-media>
      <section v-if="confirming" class="media-confirmation" role="alert" data-media-confirm-view>
        <p>{{ t('media.unsaved') }}</p>
        <div class="batch-actions"><button ref="keepEditing" type="button" data-media-keep-editing @click="cancelConfirmation">{{ t('media.keepEditing') }}</button><button type="button" data-media-confirm-discard @click="discard">{{ t('media.discard') }}</button></div>
      </section>
      <div ref="editor" v-show="!confirming" :inert="confirming || undefined" :aria-hidden="confirming || undefined" data-media-editor>
      <nav class="batch-actions"><button type="button" :aria-pressed="tab === 'assets'" data-media-assets @click="tab = 'assets'">{{ t('media.assets') }}</button><button type="button" :aria-pressed="tab === 'groups'" data-media-groups @click="tab = 'groups'">{{ t('media.groups') }}</button><button type="button" :disabled="busy" @click="media.refresh">{{ t('media.refresh') }}</button></nav>
      <p v-if="media.error.text" role="alert" class="workflow-run-error">{{ media.error.text }}</p>
      <div v-if="media.uncertain" role="alert"><p>{{ t('media.uncertain') }}</p><button type="button" :disabled="busy" data-media-ack @click="media.uncertain = false">{{ t('media.checked') }}</button></div>
      <template v-if="tab === 'assets'">
        <AudioManifest :manifest="props.manifest" :audios="[]" :locked="locked || media.saving" library-only />
        <h3>{{ t('media.assets') }}</h3>
        <label v-for="audio in media.audioPage.items" :key="audio.id" class="media-asset" :data-media-audio="audio.id"><input type="checkbox" :checked="media.selected.includes(audio.id)" :disabled="busy" @change="select(audio.id, ($event.target as HTMLInputElement).checked)" /><span>{{ audio.source.original_name }} · {{ audio.source.language }}</span></label>
        <nav class="batch-actions"><button type="button" :disabled="media.loading || media.audioPage.offset === 0" data-media-audio-previous @click="media.loadAudios(Math.max(0, media.audioPage.offset - 20))">{{ t('batch.previous') }}</button><span>{{ pagination(media.audioPage) }}</span><button type="button" :disabled="media.loading || media.audioPage.offset + 20 >= media.audioPage.total" data-media-audio-next @click="media.loadAudios(media.audioPage.offset + 20)">{{ t('batch.next') }}</button></nav>
        <div class="batch-actions"><button type="button" :disabled="busy" data-media-create-selection @click="request(() => { media.createDraft(); memberOffset = 0; tab = 'groups' })">{{ t('media.newGroup') }}</button><button v-if="media.editing" type="button" :disabled="busy || !media.selected.length" data-media-add-selection @click="media.addMembers(); tab = 'groups'">{{ t('media.addMembers') }}</button></div>
      </template>
      <template v-else>
        <div class="batch-actions"><button type="button" :disabled="busy" data-media-new-group @click="request(() => { media.createDraft(); memberOffset = 0 })">{{ t('media.newGroup') }}</button></div>
        <article v-for="group in media.groupPage.items" :key="group.id" class="media-group-row"><button type="button" :disabled="busy" :data-media-group="group.id" @click="request(() => { void media.edit(group.id); memberOffset = 0 })">{{ group.name }} · {{ group.audio_count }} · {{ t(`media.mode_${group.mode}`) }}</button><button type="button" :disabled="busy || media.uncertain" :aria-label="t('media.deleteGroup', { name: group.name })" @click="request(() => { void media.remove(group.id) })">{{ t('media.delete') }}</button></article>
        <nav class="batch-actions"><button type="button" :disabled="media.loading || media.groupPage.offset === 0" @click="media.loadGroups(Math.max(0, media.groupPage.offset - 20))">{{ t('batch.previous') }}</button><span>{{ pagination(media.groupPage) }}</span><button type="button" :disabled="media.loading || media.groupPage.offset + 20 >= media.groupPage.total" @click="media.loadGroups(media.groupPage.offset + 20)">{{ t('batch.next') }}</button></nav>
        <form v-if="media.editing" class="media-group-form" @submit.prevent="media.save">
          <label>{{ t('media.name') }}<input v-model="media.draft.name" maxlength="200" :disabled="busy" data-group-name /></label>
          <label>{{ t('media.mode') }}<select v-model="media.draft.mode" :disabled="busy" data-group-mode><option v-for="mode in ['independent','shared','continuous']" :key="mode" :value="mode">{{ t(`media.mode_${mode}`) }}</option></select></label>
          <label>{{ t('media.background') }}<textarea v-model="media.draft.background" rows="5" :disabled="busy" data-group-background /></label>
          <h4>{{ t('media.members') }} · {{ media.draft.audio_ids.length }}</h4>
          <div v-for="id in members" :key="id" class="media-member" :data-group-member="id"><span>{{ name(id) }}</span><button type="button" :disabled="busy || media.draft.audio_ids.indexOf(id) === 0" :aria-label="t('media.up')" @click="media.move(id, -1)">↑</button><button type="button" :disabled="busy || media.draft.audio_ids.indexOf(id) === media.draft.audio_ids.length - 1" :aria-label="t('media.down')" @click="media.move(id, 1)">↓</button><button type="button" :disabled="busy" :aria-label="t('media.removeMember')" @click="media.draft.audio_ids = media.draft.audio_ids.filter(value => value !== id)">×</button></div>
          <nav class="batch-actions"><button type="button" :disabled="memberOffset === 0" @click="memberOffset = Math.max(0, memberOffset - 20)">{{ t('batch.previous') }}</button><button type="button" :disabled="memberOffset + 20 >= media.draft.audio_ids.length" @click="memberOffset += 20">{{ t('batch.next') }}</button><button type="button" :disabled="busy" @click="tab = 'assets'">{{ t('media.chooseMembers') }}</button></nav>
          <div class="batch-actions"><button type="submit" :disabled="busy || media.uncertain || !valid || (!media.dirty && !!media.draft.id)" data-group-save>{{ t('media.save') }}</button><button type="button" :disabled="busy" data-group-discard @click="media.discard">{{ t('media.discard') }}</button><button v-if="media.draft.id" type="button" :disabled="busy" data-group-reload @click="request(() => { void media.edit(media.draft.id) })">{{ t('media.reload') }}</button></div>
        </form>
      </template>
      </div>
    </div>
  </CanvasDialog>
</template>
