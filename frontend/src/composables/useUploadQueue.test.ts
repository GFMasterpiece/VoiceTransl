import { describe, expect, it, vi } from 'vitest'
import { fileStem, useUploadQueue, validateUpload, type UploadPolicy } from './useUploadQueue'

const policy: UploadPolicy = { allowed_extensions: ['wav', 'mp3', 'm4a', 'flac', 'aac', 'ogg', 'opus'], max_upload_bytes: 1024 }
const file = (name: string, lastModified = 1) => new File(['audio'], name, { type: 'audio/wav', lastModified })

function queueWith(create = vi.fn().mockResolvedValue({ id: 'project-1' })) {
  const append = vi.fn().mockResolvedValue({ id: 'aud_extra' })
  const queue = useUploadQueue(create, append)
  queue.setPolicy(policy)
  return { queue, create, append }
}

describe('upload validation matches the server creation contract', () => {
  it('uses server extension/size settings, including the exact inclusive boundary', () => {
    expect(validateUpload(file('song.WAV'), policy)).toBe('')
    expect(validateUpload(file('song.opus'), policy)).toBe('')
    expect(validateUpload(file('song.flac'), { ...policy, allowed_extensions: ['ogg'] })).toContain('OGG')
    expect(validateUpload(new File([new Uint8Array(1024)], 'limit.wav'), policy)).toBe('')
    expect(validateUpload(new File([new Uint8Array(1025)], 'large.wav'), policy)).toContain('拆分')
    expect(validateUpload(file('hidden.wav'), null)).toContain('获取上传限制')
  })

  it('does not impose MIME, empty-file, or metadata filename rules the server lacks', () => {
    expect(validateUpload(new File([], 'empty.wav', { type: 'application/octet-stream' }), policy)).toBe('')
    expect(validateUpload(new File(['x'], '../metadata.wav', { type: 'text/plain' }), policy)).toBe('')
    expect(validateUpload(new File(['x'], 'dir/metadata.wav/.'), policy)).toBe('')
    expect(validateUpload(file('..wav'), policy)).toBe('')
    expect(validateUpload(new File(['x'], 'CON.wav'), policy)).toBe('')
    expect(validateUpload(file('.wav'), policy)).toContain('格式不支持')
    expect(validateUpload(file('song.wav.exe'), policy)).toContain('格式不支持')
    expect(fileStem('episode.part-1.wav')).toBe('episode.part-1')
  })

  it('revalidates selected files when the server policy becomes available', () => {
    const queue = useUploadQueue(vi.fn(), vi.fn())
    queue.add([file('voice.wav')])
    expect(queue.items.value[0].status).toBe('invalid')
    queue.setPolicy(policy)
    expect(queue.readyCount.value).toBe(1)
  })
})

describe('upload queue', () => {
  it('creates one parent then appends sequentially, isolates failures and retries into the same parent', async () => {
    let release: ((project: { id: string }) => void) | undefined
    const create = vi.fn().mockImplementationOnce(() => new Promise<{ id: string }>((resolve) => { release = resolve }))
    const { queue, append } = queueWith(create)
    append.mockRejectedValueOnce(new Error('文件过大')).mockResolvedValueOnce({ id: 'aud_third' }).mockResolvedValueOnce({ id: 'aud_retry' })
    queue.add([file('first.wav'), file('second.wav'), file('third.wav')])
    const options = { name: 'Shared project', language: 'ja' }
    const pending = queue.submit(options)
    options.language = 'en'
    expect(create).toHaveBeenCalledTimes(1)
    expect(queue.busy.value).toBe(true)
    expect(queue.pendingCount.value).toBe(3)
    release!({ id: 'one-project' })
    await pending
    expect(create.mock.calls[0].slice(1)).toEqual(['Shared project', 'ja'])
    expect(append.mock.calls.map((call) => [call[0], call[2]])).toEqual([['one-project', 'ja'], ['one-project', 'ja']])
    expect(queue.items.value.map((item) => item.status)).toEqual(['created', 'failed', 'created'])
    expect(queue.items.value[1].error).toContain('文件过大')
    await queue.submit({ name: 'Later name', language: 'en' })
    await queue.retry(queue.items.value[1].id)
    expect(create).toHaveBeenCalledTimes(1)
    expect(append).toHaveBeenCalledTimes(3)
    expect(append.mock.calls[2][0]).toBe('one-project')
    expect(append.mock.calls[2][2]).toBe('ja')
    expect(queue.items.value.map((item) => item.projectId)).toEqual(['one-project', 'one-project', 'one-project'])
    expect(queue.items.value.map((item) => item.audioId)).toEqual(['aud_primary', 'aud_retry', 'aud_third'])
    await queue.retry(queue.items.value[0].id)
    expect(append).toHaveBeenCalledTimes(3)
  })

  it('keeps the shared name when the first file fails and the next creates the project', async () => {
    const { queue, create, append } = queueWith(vi.fn().mockRejectedValueOnce(new Error('failed')).mockResolvedValueOnce({ id: 'parent' }))
    queue.add([file('first.wav'), file('second.wav')])
    await queue.submit({ name: 'Custom project', language: 'en' })
    expect(create.mock.calls.map((call) => call[1])).toEqual(['Custom project', 'Custom project'])
    await queue.retry(queue.items.value[0].id)
    expect(append.mock.calls[0][0]).toBe('parent')
    expect(create).toHaveBeenCalledTimes(2)
  })

  it('appends to an existing project and clears the target before creating another', async () => {
    const { queue, create, append } = queueWith()
    expect(queue.setTarget('existing', 'Existing project')).toBe(true)
    queue.add([file('more.wav')])
    expect(queue.setTarget('other', 'Other project')).toBe(false)
    await queue.submit({ name: 'Existing project', language: 'ko' })
    expect(create).not.toHaveBeenCalled()
    expect(append.mock.calls[0][0]).toBe('existing')
    queue.clear()
    expect(queue.targetProjectId.value).toBeNull()
    queue.add([file('new.wav')])
    await queue.submit({ name: '', language: 'ja' })
    expect(create.mock.calls[0][1]).toBe('new')
  })

  it('guards all queue mutations while uploading and preserves successfully uploaded resources', async () => {
    let release: ((project: { id: string }) => void) | undefined
    const create = vi.fn(() => new Promise<{ id: string }>((resolve) => { release = resolve }))
    const { queue } = queueWith(create)
    queue.add([file('voice.wav')])
    const pending = queue.submit({ name: 'Custom voice', language: 'en' })
    expect(queue.add([file('extra.wav')])).toBe(false)
    expect(queue.remove(queue.items.value[0].id)).toBe(false)
    expect(queue.clear()).toBe(false)
    expect(queue.setPolicy(policy)).toBe(false)
    expect(queue.setTarget('other', 'Other')).toBe(false)
    await queue.submit({ name: 'Changed', language: 'ja' })
    expect(create).toHaveBeenCalledTimes(1)
    release!({ id: 'parent' })
    await pending
    expect(queue.remove(queue.items.value[0].id)).toBe(false)
    expect(queue.busy.value).toBe(false)
  })

  it('excludes invalid files while applying the user project name to valid files', async () => {
    const { queue, create } = queueWith()
    queue.add([file('good.wav'), file('bad.exe'), file('remove.wav')])
    expect(queue.validCount.value).toBe(2)
    queue.remove(queue.items.value[2].id)
    await queue.submit({ name: 'Project name', language: 'ko' })
    expect(create.mock.calls[0].slice(1)).toEqual(['Project name', 'ko'])
    expect(queue.items.value[1].status).toBe('invalid')
  })

  it('shows duplicate and same-name feedback without overwriting a resource', () => {
    const { queue } = queueWith()
    queue.add([file('voice.wav')])
    queue.add([file('voice.wav')])
    expect(queue.items.value).toHaveLength(1)
    expect(queue.feedback.value).toContain('未重复添加')
    queue.add([file('voice.wav', 2)])
    expect(queue.items.value).toHaveLength(2)
    expect(queue.feedback.value).toContain('不会覆盖')
  })

  it('retains recovery instructions and releases the lock after connection failure', async () => {
    const { queue } = queueWith(vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    queue.add([file('voice.wav')])
    await queue.submit({ name: '', language: 'en' })
    expect(queue.items.value[0].error).toContain('检查服务和网络')
    expect(queue.busy.value).toBe(false)
  })
})
