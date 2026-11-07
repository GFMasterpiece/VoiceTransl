import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useBatchManifest, safeOriginPath } from './useBatchManifest'
import { setLocale } from '../i18n'

const response = (payload: unknown, status = 200) => ({ ok: status < 400, status, json: async () => payload }) as Response
function file(name: string, relative = '', size = 2) {
  const value = new File(['a'.repeat(size)], name)
  Object.defineProperty(value, 'webkitRelativePath', { value: relative })
  return value
}
function setup() {
  const uploaded = vi.fn()
  const manifest = useBatchManifest('p1', uploaded)
  manifest.policy.value = { allowed_extensions: ['wav'], max_upload_bytes: 10 }
  manifest.language.value = 'ja'
  return { manifest, uploaded }
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); setLocale('zh-CN') })

describe('batch manifest', () => {
  it('keeps identically named folders and files in separate roots and filters subfolders reversibly', () => {
    const { manifest } = setup()
    manifest.add([file('same.wav', 'root/same.wav'), file('same.wav', 'root/child/same.wav')], true)
    manifest.add([file('same.wav', 'root/same.wav')], true)
    expect(manifest.items.value).toHaveLength(3)
    expect(manifest.items.value[0]!.origin!.root_id).not.toBe(manifest.items.value[2]!.origin!.root_id)
    expect(manifest.items.value[0]!.origin!.relative_path).toBe('same.wav')
    expect(manifest.items.value[1]!.origin!.relative_path).toBe('child/same.wav')
    manifest.recursive.value = false
    expect(manifest.selected.value).toHaveLength(2)
    manifest.recursive.value = true
    expect(manifest.selected.value).toHaveLength(3)
    manifest.items.value[0]!.selected = false
    expect(manifest.selected.value).toHaveLength(2)
    manifest.add([file('same.wav', 'first/same.wav'), file('same.wav', 'second/same.wav')], true)
    expect(manifest.items.value[3]!.origin!.root_id).not.toBe(manifest.items.value[4]!.origin!.root_id)
    manifest.dispose()
  })

  it('derives format and size preflight from upload policy and requires per-item language', () => {
    const { manifest } = setup()
    manifest.language.value = ''
    manifest.add([file('valid.wav'), file('large.wav', '', 11), file('no.flac')])
    expect(manifest.validation(manifest.items.value[0]!)).not.toBe('')
    manifest.language.value = 'en'; manifest.applyLanguage()
    expect(manifest.validation(manifest.items.value[0]!)).toBe('')
    expect(manifest.validation(manifest.items.value[1]!)).not.toBe('')
    expect(manifest.validation(manifest.items.value[2]!)).not.toBe('')
    manifest.policy.value = { allowed_extensions: ['flac'], max_upload_bytes: 100 }
    expect(manifest.validation(manifest.items.value[2]!)).toBe('')
    manifest.dispose()
  })

  it.each(['../x.wav', '/x.wav', 'a//x.wav', 'a/./x.wav', 'a\\x.wav', 'a:x.wav', 'CON.wav', 'a./x.wav', ' x.wav', 'a/\u0085x.wav', `${'a'.repeat(256)}/x.wav`])('rejects unsafe origin %s', (path) => {
    expect(safeOriginPath(path)).toBe(false)
  })

  it('uploads with at most three requests and retries only failed uploads while retaining origin and language', async () => {
    const { manifest, uploaded } = setup()
    manifest.add(Array.from({ length: 5 }, (_, index) => file(`${index}.wav`, `album/sub/${index}.wav`)), true)
    let active = 0; let peak = 0; let firstFailure = true
    const forms: FormData[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url, options: RequestInit) => {
      active++; peak = Math.max(peak, active)
      const form = options.body as FormData; forms.push(form)
      await Promise.resolve(); await Promise.resolve(); active--
      if ((form.get('file') as File).name === '1.wav' && firstFailure) { firstFailure = false; return response({ detail: 'Temporary failure' }, 503) }
      const id = `a${(form.get('file') as File).name}`
      return response({ audio: { id, source: { original_name: (form.get('file') as File).name, language: form.get('source_language'), size_bytes: 2 } } })
    }))
    await manifest.upload()
    expect(peak).toBe(3)
    expect(uploaded).toHaveBeenCalledTimes(4)
    expect(manifest.items.value.filter((item) => item.audioId)).toHaveLength(4)
    expect(manifest.pendingLocal.value).toBe(true)
    expect(forms[0]!.get('origin_relative_path')).toBe('sub/0.wav')
    expect(forms[0]!.get('origin_root_name')).toBe('album')
    expect((forms[0]!.get('file') as File).name).toBe('0.wav')
    manifest.language.value = 'en'; manifest.applyLanguage()
    expect(manifest.items.value[0]!.language).toBe('ja')
    await manifest.upload(true)
    expect(forms).toHaveLength(6)
    expect(uploaded).toHaveBeenCalledTimes(5)
    expect(manifest.ready.value).toBe(true)
    expect(manifest.pendingLocal.value).toBe(false)
    manifest.dispose()
  })

  it('preserves raw errors and files when switching language, and cleans up requests on disposal', async () => {
    const { manifest } = setup()
    manifest.add([file('one.wav')])
    vi.stubGlobal('fetch', vi.fn(async () => response({ detail: 'original diagnostic' }, 422)))
    await manifest.upload()
    const before = manifest.items.value[0]!
    setLocale('en'); await nextTick()
    expect(manifest.items.value[0]!).toBe(before)
    expect((before.error as Error).message).toBe('original diagnostic')
    expect(before.file?.name).toBe('one.wav')
    manifest.dispose()
  })

  it('explicitly uses the basename as the multipart filename for directory files', async () => {
    const { manifest } = setup()
    const selected = file('same.wav', 'SetA/nested/same.wav')
    manifest.add([selected], true)
    const append = vi.spyOn(FormData.prototype, 'append')
    let sent: FormData | undefined
    vi.stubGlobal('fetch', vi.fn(async (_url, options: RequestInit) => {
      sent = options.body as FormData
      return response({ audio: { id: 'a1', source: { original_name: 'same.wav', language: 'ja', size_bytes: 2 } } })
    }))
    await manifest.upload()
    // jsdom cannot model a native directory File's internal multipart path.
    // An explicit third argument is required to override that browser filename.
    expect(append.mock.calls.find(([key]) => key === 'file')).toEqual(['file', selected, 'same.wav'])
    expect((sent!.get('file') as File).name).toBe('same.wav')
    expect(sent!.get('origin_relative_path')).toBe('nested/same.wav')
    expect(sent!.get('origin_root_name')).toBe('SetA')
    expect(manifest.ready.value).toBe(true)
    manifest.dispose()
  })

  it('adds existing legacy audio without origin and prevents repeated audio IDs', () => {
    const { manifest } = setup()
    const audio = { id: 'a1', source: { original_name: 'old.wav', language: 'ja', size_bytes: 2 } }
    manifest.addExisting(audio); manifest.addExisting(audio)
    expect(manifest.items.value).toHaveLength(1)
    expect(manifest.items.value[0]!.origin).toBeNull()
    expect(manifest.ready.value).toBe(true)
    manifest.dispose()
  })

  it('leaves the selection size limit to the server while checking upload readiness', () => {
    const { manifest } = setup()
    manifest.items.value = Array.from({ length: 10001 }, (_, index) => ({ id: String(index), audioId: `a${index}`, name: `${index}.wav`, language: 'ja',
      sizeBytes: 2, origin: null, selected: true, status: 'uploaded' }))
    expect(manifest.ready.value).toBe(true)
    manifest.dispose()
  })
})
