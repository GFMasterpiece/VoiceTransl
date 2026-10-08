import { describe, expect, it, vi } from 'vitest'
import { fileStem, useUploadQueue, validateUpload, type UploadPolicy } from './useUploadQueue'

const policy: UploadPolicy = { allowed_extensions: ['wav', 'mp3', 'm4a', 'flac', 'aac', 'ogg', 'opus'], max_upload_bytes: 1024 }
const file = (name: string, lastModified = 1) => new File(['audio'], name, { type: 'audio/wav', lastModified })

function queueWith(create = vi.fn().mockResolvedValue({ id: 'project-1' })) {
  const queue = useUploadQueue(create)
  queue.setPolicy(policy)
  return { queue, create }
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
    const queue = useUploadQueue(vi.fn())
    queue.add([file('voice.wav')])
    expect(queue.items.value[0].status).toBe('invalid')
    queue.setPolicy(policy)
    expect(queue.readyCount.value).toBe(1)
  })
})

describe('upload queue', () => {
  it('uploads sequentially, isolates a failure, and retries only that item with its original snapshot', async () => {
    let release: ((project: { id: string }) => void) | undefined
    const create = vi.fn()
      .mockImplementationOnce(() => new Promise<{ id: string }>((resolve) => { release = resolve }))
      .mockRejectedValueOnce(new Error('文件过大'))
      .mockResolvedValueOnce({ id: 'third-project' })
      .mockResolvedValueOnce({ id: 'retry-project' })
    const { queue } = queueWith(create)
    queue.add([file('first.wav'), file('second.wav'), file('third.wav')])
    const options = { name: 'Custom name ignored in batch', language: 'ja' }
    const pending = queue.submit(options)
    options.language = 'en'
    expect(create).toHaveBeenCalledTimes(1)
    expect(queue.busy.value).toBe(true)
    expect(queue.pendingCount.value).toBe(3)
    release!({ id: 'first-project' })
    await pending

    expect(create.mock.calls.map((call) => call.slice(1))).toEqual([['first', 'ja'], ['second', 'ja'], ['third', 'ja']])
    expect(queue.items.value.map((item) => item.status)).toEqual(['created', 'failed', 'created'])
    expect(queue.createdCount.value).toBe(2)
    expect(queue.failedCount.value).toBe(1)
    expect(queue.pendingCount.value).toBe(0)
    expect(queue.items.value[1].error).toContain('文件过大')
    expect(queue.items.value[1].error).toContain('重试此文件')

    await queue.submit({ name: 'Later name', language: 'en' })
    expect(create).toHaveBeenCalledTimes(3)
    await queue.retry(queue.items.value[1].id)
    expect(create).toHaveBeenCalledTimes(4)
    expect(create.mock.calls[3].slice(1)).toEqual(['second', 'ja'])
    expect(queue.items.value.map((item) => item.projectId)).toEqual(['first-project', 'retry-project', 'third-project'])
    await queue.retry(queue.items.value[0].id)
    expect(create).toHaveBeenCalledTimes(4)
  })

  it('guards additions/removals/reset/re-entry for the entire batch, including between requests', async () => {
    let release: ((project: { id: string }) => void) | undefined
    const create = vi.fn(() => new Promise<{ id: string }>((resolve) => { release = resolve }))
    const { queue } = queueWith(create)
    queue.add([file('voice.wav')])
    const pending = queue.submit({ name: 'Custom voice', language: 'en' })
    expect(queue.add([file('extra.wav')])).toBe(false)
    expect(queue.remove(queue.items.value[0].id)).toBe(false)
    expect(queue.clear()).toBe(false)
    expect(queue.setPolicy({ ...policy, allowed_extensions: ['ogg'] })).toBe(false)
    await queue.submit({ name: 'Changed', language: 'ja' })
    await queue.retry(queue.items.value[0].id)
    expect(create).toHaveBeenCalledTimes(1)
    release!({ id: 'project-1' })
    await pending
    expect(create.mock.calls[0].slice(1)).toEqual(['Custom voice', 'en'])
    expect(queue.items.value[0].snapshot).toEqual({ name: 'Custom voice', language: 'en' })
    expect(queue.remove(queue.items.value[0].id)).toBe(false)
    expect(queue.busy.value).toBe(false)
  })

  it('shows duplicate and same-name feedback without overwriting an existing item', () => {
    const { queue } = queueWith()
    queue.add([file('voice.wav')])
    queue.add([file('voice.wav')])
    expect(queue.items.value).toHaveLength(1)
    expect(queue.feedback.value).toContain('未重复添加')
    queue.add([file('voice.wav', 2)])
    expect(queue.items.value).toHaveLength(2)
    expect(queue.feedback.value).toContain('不会覆盖')
    expect(queue.items.value[0].id).not.toBe(queue.items.value[1].id)
  })

  it('excludes invalid files, reports counts, removes items, and uses stems when multiple files were selected', async () => {
    const { queue, create } = queueWith()
    queue.add([file('good.wav'), file('bad.exe'), file('remove.wav')])
    expect(queue.validCount.value).toBe(2)
    expect(queue.readyCount.value).toBe(2)
    expect(queue.remove(queue.items.value[2].id)).toBe(true)
    await queue.submit({ name: 'Single custom name must not apply', language: 'ko' })
    expect(create).toHaveBeenCalledTimes(1)
    expect(create.mock.calls[0].slice(1)).toEqual(['good', 'ko'])
    expect(queue.items.value[1].status).toBe('invalid')
    expect(queue.clear()).toBe(true)
    expect(queue.items.value).toHaveLength(0)
  })

  it('retains concrete connection recovery instructions and releases the lock on failure', async () => {
    const { queue } = queueWith(vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    queue.add([file('voice.wav')])
    await queue.submit({ name: '', language: 'en' })
    expect(queue.items.value[0].error).toContain('检查服务和网络')
    expect(queue.busy.value).toBe(false)
  })
})
