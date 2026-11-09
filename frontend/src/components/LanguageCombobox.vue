<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useId, watch } from 'vue'
import { locale, t } from '../i18n'
import type { CapabilityLanguage } from '../workflow/capabilities'
import { matchesLanguage, useLanguageSuggestions } from '../composables/languageSuggestions'

defineOptions({ inheritAttrs: false })
const props = withDefaults(defineProps<{
  modelValue: string; languages?: readonly CapabilityLanguage[]; disabled?: boolean; hidden?: boolean
  allowEmpty?: boolean; emptyLabel?: string
}>(), { disabled: false, hidden: false, allowEmpty: false })
const emit = defineEmits<{ 'update:modelValue': [value: string]; searching: [value: boolean] }>()
const context = useLanguageSuggestions()
const options = computed(() => props.languages ?? context?.languages.value)
const available = computed(() => options.value !== undefined)
const blocked = computed(() => props.disabled || props.hidden || !!context?.suspended.value)
const input = ref<HTMLInputElement | null>(null)
const popup = ref<HTMLElement | null>(null)
const id = useId()
const opened = ref(false)
const query = ref(props.modelValue)
const active = ref(-1)
const composing = ref(false)
const edited = ref(false)
const searching = computed(() => opened.value && query.value !== props.modelValue)
const placement = ref({ left: '0px', top: '0px', width: '240px', maxHeight: '240px', zIndex: 60 })
type Entry = { key: string; value: string; label: string; native?: string; kind: 'language' | 'custom' | 'empty' }
const entries = computed<Entry[]>(() => {
  const filtered = (options.value || []).filter((language) => matchesLanguage(language, edited.value ? query.value : ''))
  const result: Entry[] = filtered.map((language) => ({ key: `code:${language.code}`, value: language.code,
    label: language.names[locale.value], native: language.nativeName, kind: 'language' }))
  if (!query.value && props.allowEmpty) result.unshift({ key: 'empty:', value: '', label: props.emptyLabel || t('languages.clear'), kind: 'empty' })
  if (query.value && !filtered.some((language) => language.code === query.value)) result.push({ key: 'custom:', value: query.value, label: t('languages.custom', { code: query.value }), kind: 'custom' })
  return result
})
let frame: number | null = null
let listening = false
function release() {
  if (frame !== null) cancelAnimationFrame(frame)
  frame = null
  if (!listening) return
  listening = false
  window.removeEventListener('pointerdown', outside, true)
  window.removeEventListener('keydown', escape, true)
  window.removeEventListener('blur', close)
  document.removeEventListener('visibilitychange', hiddenDocument)
}
function close() {
  opened.value = false
  query.value = props.modelValue
  active.value = -1
  composing.value = false
  release()
}
function hiddenDocument() { if (document.hidden) close() }
function outside(event: Event) {
  if (event.target instanceof Element && event.target.closest('button:disabled, input:disabled, select:disabled')) return
  if (event.target instanceof Node && !input.value?.contains(event.target) && !popup.value?.contains(event.target)) close()
}
function escape(event: KeyboardEvent) {
  if (!opened.value || event.key !== 'Escape' || event.target !== input.value) return
  event.stopImmediatePropagation()
  if (event.isComposing || composing.value || event.keyCode === 229) return
  event.preventDefault()
  close()
}
function position() {
  frame = null
  const anchor = input.value
  if (!opened.value || blocked.value || !anchor?.isConnected || anchor.closest('[inert]')) { close(); return }
  const rect = anchor.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) { close(); return }
  const viewport = window.visualViewport
  const x = viewport?.offsetLeft || 0, y = viewport?.offsetTop || 0
  const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight
  if (rect.bottom < y || rect.top > y + height || rect.right < x || rect.left > x + width) { close(); return }
  let left = Math.max(x, rect.left), right = Math.min(x + width, rect.right)
  let top = Math.max(y, rect.top), bottom = Math.min(y + height, rect.bottom)
  for (let ancestor = anchor.parentElement; ancestor; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor)
    const clipsX = /^(auto|scroll|hidden|clip)$/.test(style.overflowX || style.overflow)
    const clipsY = /^(auto|scroll|hidden|clip)$/.test(style.overflowY || style.overflow)
    if (!clipsX && !clipsY) continue
    const bounds = ancestor.getBoundingClientRect()
    if (clipsX) { left = Math.max(left, bounds.left); right = Math.min(right, bounds.right) }
    if (clipsY) { top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom) }
    if (left >= right || top >= bottom) { close(); return }
  }
  const popupWidth = Math.min(Math.max(240, rect.width), width - 16)
  const below = y + height - rect.bottom - 8
  const above = rect.top - y - 8
  const up = below < 160 && above > below
  const maxHeight = Math.max(0, Math.min(260, up ? above - 4 : below - 4))
  const actualHeight = Math.min(popup.value?.scrollHeight || maxHeight, maxHeight)
  const next = { left: `${Math.max(x + 8, Math.min(rect.left, x + width - popupWidth - 8))}px`,
    top: `${up ? rect.top - actualHeight - 4 : rect.bottom + 4}px`, width: `${popupWidth}px`, maxHeight: `${maxHeight}px`,
    zIndex: anchor.closest('[role="dialog"]') ? 110 : 60 }
  if (JSON.stringify(next) !== JSON.stringify(placement.value)) placement.value = next
  frame = requestAnimationFrame(position)
}
function open() {
  if (blocked.value || !available.value || opened.value) return
  query.value = props.modelValue
  edited.value = false
  active.value = -1
  opened.value = true
  listening = true
  window.addEventListener('pointerdown', outside, true)
  window.addEventListener('keydown', escape, true)
  window.addEventListener('blur', close)
  document.addEventListener('visibilitychange', hiddenDocument)
  frame = requestAnimationFrame(position)
}
function type(event: Event) {
  if (blocked.value) return
  const value = (event.target as HTMLInputElement).value
  if (!available.value) { emit('update:modelValue', value); return }
  open()
  query.value = value
  edited.value = true
  active.value = entries.value.length ? 0 : -1
}
function choose(entry: Entry) {
  if (blocked.value || composing.value) return
  context?.finishEdit?.()
  emit('update:modelValue', entry.value)
  context?.finishEdit?.()
  close()
  void nextTick(() => { query.value = props.modelValue })
}
function key(event: KeyboardEvent) {
  if (blocked.value || !available.value || event.isComposing || composing.value || event.keyCode === 229) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); open()
    active.value = !entries.value.length ? -1 : active.value < 0 ? (event.key === 'ArrowDown' ? 0 : entries.value.length - 1) : (active.value + (event.key === 'ArrowDown' ? 1 : -1) + entries.value.length) % entries.value.length
  } else if (event.key === 'Enter') {
    event.preventDefault()
    if (opened.value && active.value >= 0 && entries.value[active.value]) choose(entries.value[active.value]!)
    else if (opened.value) close()
  }
  if (active.value >= 0) void nextTick(() => popup.value?.querySelector(`#${id}-option-${active.value}`)?.scrollIntoView?.({ block: 'nearest' }))
}
watch(searching, (value) => emit('searching', value), { flush: 'sync' })
watch(blocked, (value) => { if (value) close() }, { flush: 'sync' })
watch(() => context?.context?.value, close, { flush: 'sync' })
watch(() => props.modelValue, close, { flush: 'sync' })
watch(() => props.languages, () => { if (opened.value) active.value = -1 })
watch(entries, () => { if (active.value >= entries.value.length) active.value = entries.value.length ? 0 : -1 })
onUnmounted(() => { close(); emit('searching', false) })
</script>

<template>
  <span class="language-combobox" @pointerdown.stop @click.stop @dblclick.stop @keydown.stop @keyup.stop @wheel.stop>
    <input ref="input" v-bind="$attrs" type="text" :value="opened ? query : modelValue" :disabled="blocked" :role="available ? 'combobox' : undefined"
      :aria-expanded="available ? opened : undefined" :aria-controls="opened ? `${id}-list` : undefined" :aria-activedescendant="opened && active >= 0 ? `${id}-option-${active}` : undefined"
      :aria-autocomplete="available ? 'list' : undefined" data-language-input autocomplete="off" spellcheck="false"
      @focus="open" @click="open" @input="type" @blur="close" @keydown="key" @compositionstart="composing = true" @compositionend="composing = false" />
  </span>
  <Teleport to="body">
    <div v-if="opened" :id="`${id}-list`" ref="popup" role="listbox" class="language-options" :style="placement" data-language-options
      :aria-label="t('languages.candidates')" @pointerdown.stop @click.stop @dblclick.stop @wheel.stop>
      <div v-for="(entry, index) in entries" :id="`${id}-option-${index}`" :key="entry.key" role="option" :aria-selected="index === active"
        :class="{ 'is-active': index === active }" :data-language-code="entry.value" :data-language-kind="entry.kind"
        @mousedown.prevent @click.stop="choose(entry)">
        <span>{{ entry.label }}</span><small v-if="entry.kind === 'language'">{{ entry.native }} · {{ entry.value }}</small>
      </div>
    </div>
  </Teleport>
</template>
