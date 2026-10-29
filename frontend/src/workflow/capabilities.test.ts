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
        { key: 'backend', type: 'string', required: false, default: 'qwen3', minimum: null, maximum: null, options: [] },
        { key: 'chunk_seconds', type: 'integer', required: false, default: 30, minimum: 1, maximum: 600, options: [] },
      ] } },
      { type: 'audio_output', label: '音频输出', input_ports: ['audio'], output_ports: [], terminal: true, max_instances: null, required_inbound: 1, removable: true, config: { kind: 'audio_output', fields: [] } },
    ],
  },
  asr: { providers: [{ id: 'mock', label: 'Mock' }, { id: 'crispasr', label: 'CrispASR' }], backends: [], status: 'unavailable', message: '当前无法读取本地 ASR 能力；Mock 节点仍可用', truncated: false, warnings: [] },
  export: { formats: [{ id: 'srt', label: 'SRT' }], naming_modes: [{ id: 'language', label: '按语言命名' }] },
}

describe('capabilities parser', () => {
  it('accepts the live response shape and derives an editable registry', () => {
    const caps = readCapabilities(livePayload)
    const registry = createNodeRegistry(caps)
    expect(caps.schemaVersion).toBe('1.0')
    expect(caps.nodeTypes).toEqual(['audio_input', 'asr', 'audio_output'])
    expect(caps.asr.status).toBe('unavailable')
    expect(caps.asr.providers.map((provider) => provider.value)).toEqual(['mock', 'crispasr'])
    expect(registry.createDefaultConfig('asr')).toEqual({ backend: 'qwen3', chunk_seconds: 30 })
    expect(registry.validateConfig('asr', { backend: 'unknown-protocol-compatible', chunk_seconds: 30 })).toEqual([])
    expect(registry.validateConfig('asr', { backend: 'qwen3', chunk_seconds: 601 })[0].code).toBe('INVALID_NODE_CONFIG')
  })

  it('keeps Mock workflow usable when local ASR discovery is unavailable', () => {
    const caps = readCapabilities(livePayload)
    expect(caps.asr.backends).toEqual([])
    expect(caps.asr.message).toContain('Mock')
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
