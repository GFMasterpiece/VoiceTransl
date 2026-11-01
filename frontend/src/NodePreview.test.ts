import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, reactive, type App } from 'vue'
import NodePreview from './components/NodePreview.vue'
import { setLocale } from './i18n'
import type { WorkflowNodeRun } from './stores/workflow'

let app: App | undefined
let root: HTMLDivElement | undefined
async function settle() { for (let i = 0; i < 20; i++) await Promise.resolve(); await nextTick() }
function record(url: string, renderer = 'text'): WorkflowNodeRun {
  return { node_id: 'preview', node_type: 'extension_preview', config: {}, status: 'completed', started_at: null, finished_at: null, artifact: null, error: null, preview: { renderer, content_url: url, media_type: 'text/plain' } }
}
function mount(renderer = 'text', initial = record('/api/v1/content/one')) {
  const props = reactive({ renderer, record: initial, runId: 'run-one', audioId: 'audio-one', historical: false })
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(NodePreview, props) }); app.mount(root)
  return props
}
afterEach(() => { app?.unmount(); root?.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setLocale('zh-CN') })

describe('catalog preview renderers', () => {
  it('does not label a node as a historical version before any run exists', async () => {
    vi.stubGlobal('fetch', vi.fn())
    const props = mount()
    props.runId = ''; props.record = { ...props.record, preview: null }; props.historical = true
    await settle()
    expect(root!.querySelector('.node-preview-version')).toBeNull()
    expect(root!.textContent).toContain('运行此节点后')
  })

  it('renders text as inert text, reports historical snapshots and retries a transient preview failure', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('', { status: 503 })).mockResolvedValueOnce(new Response('<script>unsafe()</script>\nhello'))
    vi.stubGlobal('fetch', fetcher)
    const props = mount(); await settle()
    expect(root!.textContent).toContain('预览加载失败')
    root!.querySelector('button')!.click(); await settle()
    expect(root!.querySelector('pre')?.textContent).toBe('<script>unsafe()</script>\nhello')
    expect(root!.querySelector('script')).toBeNull()
    props.historical = true; await settle()
    expect(root!.textContent).toContain('历史版本 · run-one')
    expect(props.record.status).toBe('completed')
  })

  it('isolates old audio/run content even if an aborted fetch still resolves', async () => {
    let resolve!: (response: Response) => void
    const old = new Promise<Response>((done) => { resolve = done })
    const fetcher = vi.fn().mockReturnValueOnce(old).mockResolvedValueOnce(new Response('new result'))
    vi.stubGlobal('fetch', fetcher)
    const props = mount(); await settle()
    props.audioId = 'audio-two'; props.runId = 'run-two'; props.record = record('/api/v1/content/two')
    await settle()
    expect(root!.querySelector('pre')?.textContent).toBe('new result')
    resolve(new Response('stale old result')); await settle()
    expect(root!.querySelector('pre')?.textContent).toBe('new result')
    expect((fetcher.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true)
  })

  it('rejects unsafe content URLs and clearly reports unknown renderers without fetching', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const props = mount('text', record('javascript:alert(1)')); await settle()
    expect(root!.textContent).toContain('预览加载失败')
    expect(fetcher).not.toHaveBeenCalled()
    props.renderer = 'future'; props.record = record('/api/v1/future', 'future'); await settle()
    expect(root!.textContent).toContain('暂不支持预览渲染器：future')
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('recreates native audio playback on retry and keeps preview failures local', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const props = mount('audio', record('/api/v1/audio-content', 'audio')); await settle()
    expect(root!.querySelector('audio')?.hasAttribute('controls')).toBe(true)
    root!.querySelector('audio')!.dispatchEvent(new Event('error')); await settle()
    expect(root!.textContent).toContain('预览加载失败')
    root!.querySelector('button')!.click(); await settle()
    expect(root!.querySelector('audio')?.src).toContain('/api/v1/audio-content')
    expect(props.record.status).toBe('completed')
    expect(fetcher).not.toHaveBeenCalled()
  })
})
