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
