import { describe, expect, it } from 'vitest'
import { readCapabilities } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'

const livePayload = {
  schema_version: '1.0',
  workflow: {
    schema_version: '1.0',
    ports: ['audio', 'transcript', 'text'],
    nodes: [
      { type: 'audio_input', label: '音频输入', input_ports: [], output_ports: ['audio'], terminal: false, max_instances: 1, required_inbound: 0, removable: false, config: { kind: 'none', fields: [] } },
      { type: 'asr', label: '语音识别', input_ports: ['audio'], output_ports: ['transcript'], terminal: false, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'asr', fields: [
        { key: 'provider', type: 'select', required: false, default: 'mock', minimum: null, maximum: null, options: [{ value: 'mock', label: 'Mock' }] },
        { key: 'language', type: 'string', required: false, default: 'ja', minimum: null, maximum: null, options: [] },
      ] } },
      { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
    ],
  },
  asr: { providers: [{ id: 'mock', label: 'Mock' }], status: 'available', message: null, truncated: false, warnings: [] },
  export: { formats: [{ id: 'srt', label: 'SRT' }], naming_modes: [{ id: 'language', label: '按语言命名' }] },
}

describe('capabilities parser', () => {
  it('accepts the live response shape and derives an editable registry', () => {
    const caps = readCapabilities(livePayload)
    const registry = createNodeRegistry(caps)
    expect(caps.schemaVersion).toBe('1.0')
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

  it('rejects an explicit unsupported workflow schema while accepting a missing legacy field', () => {
    const missingWorkflowSchema = structuredClone(livePayload) as any
    delete missingWorkflowSchema.workflow.schema_version
    expect(() => readCapabilities(missingWorkflowSchema)).not.toThrow()

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
})
