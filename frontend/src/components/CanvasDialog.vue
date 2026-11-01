<script setup lang="ts">
import { nextTick, onUnmounted, ref, useId, watch } from 'vue'
import { t } from '../i18n'
const props = withDefaults(defineProps<{ open?: boolean; title: string; closeDisabled?: boolean }>(), { open: true, closeDisabled: false })
const emit = defineEmits<{ close: [] }>()
const titleId = useId()
const backdrop = ref<HTMLElement | null>(null)
const dialog = ref<HTMLElement | null>(null)
let trigger: Element | null = null
let alive = true
const inertStates = new Map<HTMLElement, boolean>()
function focusable() {
  return [...(dialog.value?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])') || [])].filter((item) => !item.hidden && !item.closest('[inert]'))
}
function focusFirst() { (dialog.value?.querySelector<HTMLElement>('[autofocus]:not([disabled])') || focusable()[0] || dialog.value)?.focus() }
function requestClose() { if (!props.closeDisabled) emit('close') }
function keydown(event: KeyboardEvent) {
  if (!props.open || !dialog.value) return
  if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); requestClose(); return }
  if (event.key !== 'Tab') return
  const items = focusable()
  const active = document.activeElement
  if (!items.length) { event.preventDefault(); dialog.value.focus(); return }
  if (!dialog.value.contains(active) || (!event.shiftKey && active === items[items.length - 1]) || (event.shiftKey && active === items[0])) {
    event.preventDefault(); (event.shiftKey ? items[items.length - 1] : items[0])?.focus()
  }
}
function focusin(event: FocusEvent) { if (props.open && dialog.value && !dialog.value.contains(event.target as Node)) focusFirst() }
function release() {
  document.removeEventListener('keydown', keydown, true)
  document.removeEventListener('focusin', focusin, true)
  for (const [element, wasInert] of inertStates) element.inert = wasInert
  inertStates.clear()
  if (trigger?.isConnected && 'focus' in trigger) (trigger as HTMLElement).focus()
  trigger = null
}
watch(() => props.open, async (open) => {
  if (!open) { release(); return }
  trigger = document.activeElement
  await nextTick()
  if (!alive || !props.open || !backdrop.value) return
  let branch: HTMLElement | null = backdrop.value
  while (branch?.parentElement) {
    for (const sibling of branch.parentElement.children) if (sibling !== branch && sibling instanceof HTMLElement) {
      inertStates.set(sibling, sibling.inert)
      sibling.inert = true
    }
    branch = branch.parentElement
    if (branch === document.body) break
  }
  document.addEventListener('keydown', keydown, true)
  document.addEventListener('focusin', focusin, true)
  focusFirst()
}, { immediate: true, flush: 'post' })
onUnmounted(() => { alive = false; release() })
</script>
<template>
  <div v-if="open" ref="backdrop" class="canvas-dialog-backdrop" @pointerdown.self="requestClose">
    <section ref="dialog" class="canvas-dialog" role="dialog" aria-modal="true" :aria-labelledby="titleId" tabindex="-1">
      <header class="canvas-dialog-header"><h2 :id="titleId">{{ title }}</h2><button class="canvas-dialog-close" type="button" :aria-label="t('workflow.closePanel')" :disabled="closeDisabled" @click="requestClose">×</button></header>
      <div class="canvas-dialog-body"><slot /></div>
    </section>
  </div>
</template>
