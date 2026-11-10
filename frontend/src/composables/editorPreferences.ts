import { ref } from 'vue'

export const DEBUG_MODE_STORAGE_KEY = 'voicetransl.debugMode'
function savedDebugMode() {
  try { return typeof window !== 'undefined' && window.localStorage.getItem(DEBUG_MODE_STORAGE_KEY) === 'true' }
  catch { return false }
}
export const debugMode = ref(savedDebugMode())
export function setDebugMode(value: boolean) {
  debugMode.value = value
  try { if (typeof window !== 'undefined') window.localStorage.setItem(DEBUG_MODE_STORAGE_KEY, String(value)) }
  catch { /* Keep the preference active for this browser session. */ }
}

export const SKIP_NODE_DELETE_CONFIRMATION_STORAGE_KEY = 'voicetransl.skipNodeDeleteConfirmation'
function savedSkipNodeDeleteConfirmation() {
  try { return typeof window !== 'undefined' && window.localStorage.getItem(SKIP_NODE_DELETE_CONFIRMATION_STORAGE_KEY) === 'true' }
  catch { return false }
}
export const skipNodeDeleteConfirmation = ref(savedSkipNodeDeleteConfirmation())
export function setSkipNodeDeleteConfirmation(value: boolean) {
  skipNodeDeleteConfirmation.value = value
  try { if (typeof window !== 'undefined') window.localStorage.setItem(SKIP_NODE_DELETE_CONFIRMATION_STORAGE_KEY, String(value)) }
  catch { /* Keep the preference active for this browser session. */ }
}
