import { onUnmounted, ref, watch, type Ref } from 'vue'
import { message, messageRef } from '../i18n'
import type { useWorkflowStore, WorkflowDocumentInput } from '../stores/workflow'
import { workflowCredentials } from './workflowCredentials'
import {
  copySelection, parseWorkflowFile, preparePaste, serializeWorkflow, TransferError, WORKFLOW_TRANSFER_MAX_BYTES,
} from '../workflow/editorTransfer'

// Portable selections contain no project resource binding and can cross editor sessions.
let applicationClipboard = ''

function stableJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableJson)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => [key, stableJson(value)]))
}

export function useEditorCommands(store: ReturnType<typeof useWorkflowStore>, options: {
  stage: Ref<SVGSVGElement | null>
  context: () => string
  blocked: () => boolean
  pastePoint: () => { x: number; y: number } | undefined
}) {
  const fileInput = ref<HTMLInputElement | null>(null)
  const readingImport = ref(false)
  const clipboardBusy = ref(false)
  const pendingImport = ref<{ filename: string; graph: WorkflowDocumentInput; context: string } | null>(null)
  const notice = messageRef()
  let generation = 0
  let clipboardGeneration = 0
  let alive = true

  function report(reason: unknown, fallback: string) {
    store.setError(reason instanceof TransferError ? message(reason.code, reason.params) : message(fallback))
  }
  function cancelImport() {
    generation += 1
    readingImport.value = false
    pendingImport.value = null
    if (fileInput.value) fileInput.value.value = ''
  }
  watch(() => [options.context(), options.blocked()], () => {
    cancelImport()
    clipboardGeneration += 1
    clipboardBusy.value = false
  }, { flush: 'sync' })
  function focusCanvas() { options.stage.value?.focus() }
  async function cutSelected() {
    if (options.blocked() || !store.registry || store.historyTransactionActive) return
    const removable = store.draft.nodes.filter(node => store.selectedNodeIds.includes(node.id) && store.registry!.kind(node.type)?.removable !== false).map(node => node.id)
    const protectedCount = store.selectedNodeIds.length - removable.length
    if (!removable.length) { notice.set(message('workflow.editorCutProtected', { count: protectedCount })); return }
    try {
      const text = copySelection(store.draft, removable, store.registry)
      applicationClipboard = text
      const context = options.context()
      // Delete synchronously only after the portable application clipboard is ready.
      if (!store.deleteSelection()) return
      notice.set(message(protectedCount ? 'workflow.editorCutProtected' : 'workflow.editorCut', { count: protectedCount }))
      try {
        if (!navigator.clipboard?.writeText) throw new Error()
        await navigator.clipboard.writeText(text)
      } catch {
        if (alive && options.context() === context) notice.set(message(protectedCount ? 'workflow.editorCutProtectedFallback' : 'workflow.editorCutFallback', { count: protectedCount }))
      }
    } catch (reason) { report(reason, 'workflow.editorCopyFailed') }
  }
  async function copySelected() {
    if (options.blocked() || !store.registry) return
    try {
      const text = copySelection(store.draft, store.selectedNodeIds, store.registry)
      applicationClipboard = text
      const context = options.context()
      try {
        if (!navigator.clipboard?.writeText) throw new Error()
        await navigator.clipboard.writeText(text)
        if (alive && options.context() === context) notice.set(message('workflow.editorCopied'))
      } catch {
        if (alive && options.context() === context) notice.set(message('workflow.editorCopyFallback'))
      }
    } catch (reason) { report(reason, 'workflow.editorCopyFailed') }
  }
  function pasteText(text: string, point = options.pastePoint()) {
    if (options.blocked() || readingImport.value || pendingImport.value || !store.registry) return
    try {
      const graph = preparePaste(text, store.draft, store.registry, point)
      store.applyPastedGraph(graph)
    } catch (reason) { report(reason, 'workflow.editorPasteFailed') }
  }
  function onPaste(event: ClipboardEvent) {
    if (options.blocked() || readingImport.value || pendingImport.value) return
    event.preventDefault()
    clipboardGeneration += 1
    clipboardBusy.value = false
    const text = event.clipboardData?.getData('text/plain')
    if (text) pasteText(text)
    else if (applicationClipboard) {
      notice.set(message('workflow.editorPasteFallback'))
      pasteText(applicationClipboard)
    } else store.setError(message('workflow.editorClipboardEmpty'))
  }
  async function pasteClipboard(point = options.pastePoint()) {
    if (options.blocked() || clipboardBusy.value || readingImport.value || pendingImport.value) return
    const ticket = ++clipboardGeneration
    const context = options.context()
    clipboardBusy.value = true
    let text: string
    try {
      if (!navigator.clipboard?.readText) throw new Error()
      text = await navigator.clipboard.readText()
    } catch {
      text = applicationClipboard
      if (alive && ticket === clipboardGeneration && options.context() === context) notice.set(message('workflow.editorPasteFallback'))
    }
    if (!alive || ticket !== clipboardGeneration || context !== options.context() || options.blocked()) return
    clipboardBusy.value = false
    if (!text) { store.setError(message('workflow.editorClipboardEmpty')); return }
    pasteText(text, point)
  }
  function duplicateSelected() {
    if (options.blocked() || !store.registry) return
    try {
      const text = copySelection(store.draft, store.selectedNodeIds, store.registry)
      store.applyPastedGraph(preparePaste(text, store.draft, store.registry))
    } catch (reason) { report(reason, 'workflow.editorPasteFailed') }
  }
  function exportWorkflow() {
    if (!store.registry) return
    try {
      const text = serializeWorkflow(store.draft, store.registry)
      const url = URL.createObjectURL(new Blob([text], { type: 'application/json;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = 'workflow.json'
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (reason) { report(reason, 'workflow.editorExportFailed') }
  }
  function chooseImport() {
    if (options.blocked() || readingImport.value) return
    fileInput.value?.click()
  }
  async function readImport(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file || options.blocked() || !store.registry) return
    cancelImport()
    if (file.size > WORKFLOW_TRANSFER_MAX_BYTES) { store.setError(message('workflow.transferTooLarge')); return }
    const ticket = generation
    const context = options.context()
    readingImport.value = true
    try {
      const text = typeof file.text === 'function' ? await file.text() : await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error())
        reader.onerror = () => reject(new Error())
        reader.readAsText(file, 'utf-8')
      })
      if (!alive || ticket !== generation || options.context() !== context || options.blocked() || !store.registry) return
      const graph = parseWorkflowFile(text, store.registry)
      pendingImport.value = { filename: file.name, graph, context }
    } catch (reason) {
      if (alive && ticket === generation && context === options.context()) report(reason, 'workflow.editorImportFailed')
    } finally {
      if (alive && ticket === generation) readingImport.value = false
    }
  }
  function applyImport() {
    const pending = pendingImport.value
    if (!pending || pending.context !== options.context() || options.blocked() || store.historyTransactionActive) return
    const unchanged = JSON.stringify(stableJson(pending.graph)) === JSON.stringify(stableJson(store.draft))
    if (unchanged) workflowCredentials(store.credentialContext).forget()
    if (unchanged || store.replaceGraph(pending.graph)) { cancelImport(); focusCanvas() }
  }
  onUnmounted(() => { alive = false; cancelImport(); clipboardGeneration += 1 })
  return {
    fileInput, readingImport, clipboardBusy, pendingImport, notice: notice.text, focusCanvas,
    copySelected, cutSelected, onPaste, pasteClipboard, duplicateSelected, exportWorkflow, chooseImport, readImport, cancelImport, applyImport,
  }
}
