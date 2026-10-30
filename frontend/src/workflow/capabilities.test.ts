import { describe, expect, it } from 'vitest'
import { readCapabilities } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'
import { setLocale } from '../i18n'

const livePayload = {
  schema_version: '1.0',
  workflow: {
    schema_version: '1.1',
    ports: ['audio', 'transcript', 'text'],
    nodes: [
      { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, is_source: true, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
      { type: 'asr', label: '语音识别', input_ports: ['audio'], output_ports: ['transcript'], terminal: false, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'asr', fields: [
        { key: 'provider', type: 'select', required: false, default: 'mock', minimum: null, maximum: null, options: [{ value: 'mock', label: 'Mock' }] },
        { key: 'language', type: 'string', required: false, default: 'ja', minimum: null, maximum: null, options: [] },
      ] } },
      { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
    ],
    constraints: { max_nodes: 64, max_edges: 64, min_sources: 1, max_sources: 1, min_terminals: 1 },
    default_graph: {
      nodes: [
        { id: 'input', type: 'audio_input', config: {}, position: { x: 80, y: 120 } },
        { id: 'output', type: 'audio_output', config: { filename_stem: 'audio-copy' }, position: { x: 420, y: 120 } },
      ],
      edges: [{ id: 'input-output', source: 'input', source_port: 'audio', target: 'output', target_port: 'audio' }],
    },
  },
  asr: { providers: [{ id: 'mock', label: 'Mock' }], status: 'available', message: null, truncated: false, warnings: [] },
  export: { formats: [{ id: 'srt', label: 'SRT' }], naming_modes: [{ id: 'language', label: '按语言命名' }] },
}

describe('capabilities parser', () => {
  it('accepts the live response shape and derives an editable registry', () => {
    const caps = readCapabilities(livePayload)
    const registry = createNodeRegistry(caps)
    expect(caps.schemaVersion).toBe('1.1')
    expect(caps.nodeTypes).toEqual(['audio_input', 'asr', 'audio_output'])
    expect(caps.asr.providers.map((provider) => provider.value)).toEqual(['mock'])
    expect(registry.createDefaultConfig('asr')).toEqual({ provider: 'mock', language: 'ja' })
    expect(registry.validateConfig('asr', { provider: 'mock', language: 'ja' })).toEqual([])
    expect(registry.validateConfig('asr', { provider: null, language: 'ja' }).map((issue) => issue.code)).toEqual(['INVALID_NODE_CONFIG'])
    expect(registry.validateConfig('asr', { provider: 'mock', backend: 'qwen3' })[0].code).toBe('INVALID_NODE_CONFIG')
  })

  it('does not impose a frontend Mock provider whitelist', () => {
    const withCatalogProvider = structuredClone(livePayload) as any
    withCatalogProvider.asr.providers.push({ id: 'future_provider', label: 'Future provider' })
    const caps = readCapabilities(withCatalogProvider)
    expect(caps.asr.providers.map((provider) => provider.value)).toEqual(['mock', 'future_provider'])
    expect(caps.nodes.some((node) => node.type === 'asr')).toBe(true)
  })

  it('rejects missing and unsupported schema versions', () => {
    const missing = structuredClone(livePayload) as Record<string, unknown>
    delete missing.schema_version
    expect(() => readCapabilities(missing)).toThrow('schema_version')
    expect(() => readCapabilities({ ...livePayload, schema_version: '2.0' })).toThrow('schema_version')
  })

  it('requires the current workflow capability schema and rejects legacy versions', () => {
    const missingWorkflowSchema = structuredClone(livePayload) as any
    delete missingWorkflowSchema.workflow.schema_version
    expect(() => readCapabilities(missingWorkflowSchema)).toThrow('workflow.schema_version')

    const legacyWorkflowSchema = structuredClone(livePayload) as any
    legacyWorkflowSchema.workflow.schema_version = '1.0'
    expect(() => readCapabilities(legacyWorkflowSchema)).toThrow('workflow.schema_version')

    const unsupportedWorkflowSchema = structuredClone(livePayload) as any
    unsupportedWorkflowSchema.workflow.schema_version = '2.0'
    expect(() => readCapabilities(unsupportedWorkflowSchema)).toThrow('workflow.schema_version')

    unsupportedWorkflowSchema.workflow.schema_version = null
    expect(() => readCapabilities(unsupportedWorkflowSchema)).toThrow('workflow.schema_version')
  })

  it('rejects missing nodes, unknown ports and malformed node fields', () => {
    expect(() => readCapabilities({ ...livePayload, workflow: { ...livePayload.workflow, nodes: [] } })).toThrow('没有节点')
    const unknownPort = structuredClone(livePayload)
    unknownPort.workflow.nodes[1].input_ports = ['video']
    expect(() => readCapabilities(unknownPort)).toThrow('端口未声明')
    const badField = structuredClone(livePayload) as any
    badField.workflow.nodes[1].config.fields[0].type = 'unsafe'
    expect(() => readCapabilities(badField)).toThrow('字段类型')
  })

  it('keeps config option values independent from node and port identifier syntax', () => {
    const withLanguageOption = structuredClone(livePayload) as any
    withLanguageOption.workflow.nodes[1].config.fields[1].options = [{ value: 'zh-CN', label: 'Chinese (Simplified)' }]
    expect(readCapabilities(withLanguageOption).nodes[1].fields[1].options).toEqual([{ value: 'zh-CN', label: 'Chinese (Simplified)' }])
  })

  it('rejects internally inconsistent graph constraints', () => {
    const invalid = structuredClone(livePayload)
    invalid.workflow.constraints.min_sources = 3
    invalid.workflow.constraints.max_sources = 2
    expect(() => readCapabilities(invalid)).toThrow('工作流图约束')
  })

  it('accepts a catalog-added node without changing generic parser or registry code', () => {
    const extended = structuredClone(livePayload) as any
    extended.workflow.nodes.splice(1, 0, {
      type: 'denoise', label: '降噪', input_ports: ['audio'], output_ports: ['denoised_audio'], terminal: false,
      max_instances: null, required_inbound: 1, removable: true,
      config: { kind: 'denoise', fields: [
        { key: 'strength', type: 'integer', required: true, default: 2, minimum: 1, maximum: 5, options: [] },
        { key: 'mode', type: 'select', required: false, default: 'soft', options: [{ value: 'soft', label: '柔和' }, { value: 'hard', label: '强力' }] },
      ] },
    })
    extended.workflow.ports.push('denoised_audio')
    const registry = createNodeRegistry(readCapabilities(extended))
    expect(registry.createDefaultConfig('denoise')).toEqual({ strength: 2, mode: 'soft' })
    expect(registry.validateConfig('denoise', { strength: 3, mode: 'hard' })).toEqual([])
    expect(registry.validateConfig('denoise', { strength: 0, mode: 'other' }).map((issue) => issue.code)).toEqual(['INVALID_NODE_CONFIG', 'INVALID_NODE_CONFIG'])
  })

  it('translates built-in presentation without changing raw capabilities, defaults, or ports', () => {
    const caps = readCapabilities(livePayload)
    const original = JSON.stringify(caps)
    const registry = createNodeRegistry(caps)
    expect(registry.label('asr')).toBe('语音识别')
    setLocale('en')
    expect(registry.label('asr')).toBe('Speech recognition')
    expect(registry.kind('asr')?.fields.find((field) => field.key === 'language')?.label).toBe('Language')
    expect(registry.kind('audio_input')?.addLabel).toBe('Audio input')
    expect(registry.createDefaultConfig('asr')).toEqual({ provider: 'mock', language: 'ja' })
    expect(registry.inputPortForNode('asr')).toBe('audio')
    expect(registry.outputPortForNode('asr')).toBe('transcript')
    expect(JSON.stringify(caps)).toBe(original)
    setLocale('zh-CN')
    expect(registry.label('asr')).toBe('语音识别')
  })

  it('preserves exact extension node, field, and option labels in both languages', () => {
    const extended = structuredClone(livePayload) as any
    extended.workflow.nodes.push({
      type: 'extension', label: '扩展节点 · Plugin', add_label: '插件入口',
      input_ports: ['audio'], output_ports: ['audio'], config: { kind: 'extension', fields: [
        { key: 'provider', label: '插件服务', type: 'select', default: 'future', options: [{ value: 'future', label: 'Future 服务' }] },
      ] },
    })
    extended.workflow.nodes[1].config.fields[0].options.push({ value: 'future', label: 'Future 服务' })
    extended.workflow.nodes[1].config.fields.push({ key: 'custom', label: '自定义选项', type: 'string', default: '用户值' })
    const caps = readCapabilities(extended)
    const before = JSON.stringify(caps)
    const registry = createNodeRegistry(caps)
    for (const language of ['en', 'zh-CN']) {
      setLocale(language)
      expect(registry.kind('extension')?.label).toBe('扩展节点 · Plugin')
      expect(registry.kind('extension')?.addLabel).toBe('插件入口')
      expect(registry.kind('extension')?.fields[0].label).toBe('插件服务')
      expect(registry.kind('extension')?.fields[0].options[0].label).toBe('Future 服务')
      expect(registry.kind('asr')?.fields[0].options[1].label).toBe('Future 服务')
      expect(registry.kind('asr')?.fields[2].label).toBe('自定义选项')
      expect(registry.createDefaultConfig('extension')).toEqual({ provider: 'future' })
      expect(registry.validateConfig('extension', { provider: 'future' })).toEqual([])
      expect(registry.has('extension')).toBe(true)
    }
    expect(JSON.stringify(caps)).toBe(before)
  })

  it('keeps parser errors localizable after they have been thrown', () => {
    let failure: unknown
    try { readCapabilities({ ...livePayload, workflow: { ...livePayload.workflow, nodes: [] } }) } catch (reason) { failure = reason }
    expect((failure as Error).message).toContain('没有节点')
    setLocale('en')
    expect((failure as Error).message).toBe('The capability catalog has no node definitions.')
  })
})
