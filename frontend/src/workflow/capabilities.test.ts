import { describe, expect, it } from 'vitest'
import { readCapabilities } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'
import { setLocale } from '../i18n'

const livePayload = {
  schema_version: '1.0',
  workflow: {
    schema_version: '1.2',
    ports: [
      { id: 'audio', type_id: 'audio', label: '音频', capability_requirements: [] },
      { id: 'transcript', type_id: 'transcript', label: '转写结果', capability_requirements: [] },
      { id: 'text', type_id: 'text', label: '文本产物', capability_requirements: [] },
    ],
    data_types: [
      { id: 'audio', label: '音频', parent_type_id: null, capabilities: [] },
      { id: 'transcript', label: '转写结果', parent_type_id: null, capabilities: [] },
      { id: 'text', label: '文本产物', parent_type_id: null, capabilities: [] },
    ],
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
  function parameterCatalog(): any {
    const payload = structuredClone(livePayload) as any
    payload.workflow.schema_version = '1.5'
    for (const node of payload.workflow.nodes) {
      node.required_input_ports = [...node.input_ports]
      node.is_parameter_source = false
      for (const field of node.config.fields) field.input_port = null
    }
    payload.workflow.data_types.push(
      { id: 'string', label: 'String', parent_type_id: null, capabilities: [] },
      { id: 'number', label: 'Number', parent_type_id: null, capabilities: [] },
      { id: 'integer', label: 'Integer', parent_type_id: 'number', capabilities: [] },
    )
    payload.workflow.ports.push({ id: 'custom_parameter', type_id: 'integer', label: 'Plugin parameter' })
    payload.workflow.nodes.push({
      type: 'extension', label: 'Plugin node', input_ports: ['audio', 'custom_parameter'], output_ports: ['audio'],
      required_input_ports: ['audio'], required_inbound: 1, is_parameter_source: false,
      config: { kind: 'extension', fields: [{ key: 'strength', type: 'number', default: 2, input_port: 'custom_parameter' }] },
    })
    return payload
  }

  it('reads 1.8 extension categories without changing the graph and keeps old catalogs unclassified', () => {
    const value = parameterCatalog()
    const graph = JSON.stringify(value.workflow.default_graph)
    value.workflow.schema_version = '1.8'
    value.workflow.nodes.forEach((node: any) => { node.category = null })
    value.workflow.nodes[1].category = { id: 'plugin_tools', label: 'Plugin tools' }
    expect(readCapabilities(value).nodes[1]!.category).toEqual({ id: 'plugin_tools', label: 'Plugin tools' })
    expect(readCapabilities(value).nodes[0]!.category).toBeNull()
    expect(JSON.stringify(value.workflow.default_graph)).toBe(graph)
    for (const version of ['1.2', '1.3', '1.4', '1.5', '1.6', '1.7']) {
      value.workflow.schema_version = version
      value.workflow.nodes.forEach((node: any) => { delete node.category })
      expect(readCapabilities(value).nodes.every((node) => node.category === null)).toBe(true)
    }
  })

  it.each([undefined, false, [], {}, { id: 'Bad ID', label: 'Tools' }, { id: 'tools', label: '' }, { id: 'tools', label: ' padded ' },
    { id: 'tools', label: 'line\nbreak' }, { id: 'tools', label: 'x'.repeat(129) }, { id: 'tools', label: 'Tools', extra: true }])('rejects malformed 1.8 category %j', (category) => {
    const value = parameterCatalog(); value.workflow.schema_version = '1.8'
    value.workflow.nodes.forEach((node: any) => { node.category = null })
    value.workflow.nodes[0].category = category
    expect(() => readCapabilities(value)).toThrow()
  })

  it('rejects conflicting labels for one 1.8 category', () => {
    const value = parameterCatalog(); value.workflow.schema_version = '1.8'
    value.workflow.nodes.forEach((node: any) => { node.category = null })
    value.workflow.nodes[0].category = { id: 'tools', label: 'Tools' }
    value.workflow.nodes[1].category = { id: 'tools', label: 'Other tools' }
    expect(() => readCapabilities(value)).toThrow()
  })

  it('uses generic 1.5 field-to-port mappings and inherited primitive types without a node whitelist', () => {
    const payload = parameterCatalog()
    const registry = createNodeRegistry(readCapabilities(payload))
    expect(registry.kind('extension')?.fields[0].inputPort).toBe('custom_parameter')
    expect(registry.kind('extension')?.isParameterSource).toBe(false)
    expect(registry.validateConfig('extension', { strength: 2.5 })).toEqual([])
    expect(registry.validateConfig('extension', { strength: true })).not.toEqual([])
    for (const version of ['1.2', '1.3', '1.4']) {
      payload.workflow.schema_version = version
      expect(readCapabilities(payload).nodes[3]?.fields[0].inputPort).toBeNull()
    }
  })

  it('requires explicit 1.5 parameter metadata and retains strict named input checks', () => {
    for (const invalid of [undefined, null, 'false', 0]) {
      const payload = parameterCatalog()
      payload.workflow.nodes[0].is_parameter_source = invalid
      expect(() => readCapabilities(payload)).toThrow('参数绑定')
    }
    for (const invalid of [undefined, '', 0, false, [], {}]) {
      const payload = parameterCatalog()
      payload.workflow.nodes[1].config.fields[0].input_port = invalid
      expect(() => readCapabilities(payload)).toThrow('参数绑定')
    }
    const missing = parameterCatalog()
    delete missing.workflow.nodes[0].required_input_ports
    expect(() => readCapabilities(missing)).toThrow('入边约束')
  })

  it('rejects inconsistent mappings, mismatched types, resource bindings, and false parameter roots', () => {
    const mutations: Array<(payload: any) => void> = [
      (p) => { p.workflow.nodes.at(-1).config.fields[0].input_port = 'undeclared' },
      (p) => { p.workflow.nodes.at(-1).config.fields[0].input_port = 'audio' },
      (p) => { p.workflow.nodes.at(-1).config.fields.push({ key: 'other', type: 'number', input_port: 'custom_parameter' }) },
      (p) => { p.workflow.nodes.at(-1).config.fields[0].type = 'string' },
      (p) => { p.workflow.nodes.at(-1).config.fields[0].type = 'json' },
      (p) => { p.workflow.nodes.at(-1).config.fields[0].type = 'select' },
      (p) => { p.workflow.nodes.at(-1).is_parameter_source = true },
      (p) => { p.workflow.nodes[0].is_parameter_source = true },
      (p) => {
        p.workflow.ports.at(-1).type_id = 'string'
        p.workflow.nodes.at(-1).config.fields[0].type = 'string'
        p.workflow.nodes.at(-1).presentation = { resource_input: { resource_type: 'audio', config_key: 'strength' } }
      },
    ]
    for (const mutate of mutations) {
      const payload = parameterCatalog(); mutate(payload)
      expect(() => readCapabilities(payload)).toThrow('参数绑定')
    }
    const valid = parameterCatalog()
    valid.workflow.nodes.push({ type: 'future_value', label: 'Future root', input_ports: [], output_ports: ['custom_parameter'], required_input_ports: [], required_inbound: 0, is_parameter_source: true, config: { fields: [] } })
    expect(readCapabilities(valid).nodes[4]?.isParameterSource).toBe(true)
  })

  it('reads named required inputs from a multi-port 1.4 catalog and rejects inconsistent declarations', () => {
    const payload = structuredClone(livePayload) as any
    payload.workflow.schema_version = '1.4'
    for (const node of payload.workflow.nodes) node.required_input_ports = [...node.input_ports]
    for (const id of ['voice', 'background', 'reference', 'dry_audio', 'wet_audio']) {
      payload.workflow.ports.push({ id, type_id: 'audio', label: id, capability_requirements: [] })
    }
    payload.workflow.nodes.push({
      type: 'multi', label: 'Multi', input_ports: ['voice', 'background', 'reference'],
      output_ports: ['dry_audio', 'wet_audio'], required_input_ports: ['voice', 'background'], required_inbound: 2,
      config: { kind: 'none', fields: [] },
    })
    const registry = createNodeRegistry(readCapabilities(payload))
    expect(registry.inputPortsForNode('multi')).toEqual(['voice', 'background', 'reference'])
    expect(registry.outputPortsForNode('multi')).toEqual(['dry_audio', 'wet_audio'])
    expect(registry.kind('multi')?.requiredInputPorts).toEqual(['voice', 'background'])
    expect(registry.kind('audio_input')?.requiredInputPorts).toEqual([])
    expect(registry.inputPortForNode('multi')).toBeNull()
    expect(registry.outputPortForNode('multi')).toBeNull()
    const optional = structuredClone(payload)
    optional.workflow.nodes.at(-1).required_inbound = 0
    optional.workflow.nodes.at(-1).required_input_ports = []
    expect(createNodeRegistry(readCapabilities(optional)).kind('multi')?.requiredInputPorts).toEqual([])
    for (const invalid of [['missing'], ['voice', 'voice'], ['voice']]) {
      const broken = structuredClone(payload)
      broken.workflow.nodes.at(-1).required_input_ports = invalid
      expect(() => readCapabilities(broken)).toThrow('入边约束')
    }
    const missing = structuredClone(payload)
    delete missing.workflow.nodes[0].required_input_ports
    expect(() => readCapabilities(missing)).toThrow('入边约束')
    const duplicate = structuredClone(payload)
    duplicate.workflow.nodes.at(-1).output_ports.push('dry_audio')
    expect(() => readCapabilities(duplicate)).toThrow('节点端口')
  })

  it.each([0, 1])('requires an explicit required-input array in a 1.4 catalog for node %s', (nodeIndex) => {
    const payload = structuredClone(livePayload) as any
    payload.workflow.schema_version = '1.4'
    for (const node of payload.workflow.nodes) node.required_input_ports = [...node.input_ports]
    for (const invalid of [undefined, null, 'audio', {}, false]) {
      const broken = structuredClone(payload)
      broken.workflow.nodes[nodeIndex].required_input_ports = invalid
      expect(() => readCapabilities(broken)).toThrow('入边约束')
    }
  })

  it.each(['1.2', '1.3'])('preserves required-input inference for the legacy %s catalog', (version) => {
    const payload = structuredClone(livePayload) as any
    payload.workflow.schema_version = version
    for (const declaration of [undefined, null]) {
      for (const node of payload.workflow.nodes) node.required_input_ports = declaration
      expect(readCapabilities(payload).nodes.map((node) => node.requiredInputPorts)).toEqual([[], ['audio'], ['audio']])
    }
  })

  it('reads resource and preview presentation metadata from the 1.3 catalog without node type branches', () => {
    const payload = structuredClone(livePayload) as any
    payload.workflow.schema_version = '1.3'
    payload.workflow.nodes[0].config.fields = [{ key: 'resource_id', type: 'string', required: false, default: null }]
    payload.workflow.nodes[0].presentation = { resource_input: { resource_type: 'audio', config_key: 'resource_id' } }
    payload.workflow.nodes[2].presentation = { preview: { renderer: 'future_renderer' } }
    const registry = createNodeRegistry(readCapabilities(payload))
    expect(registry.kind('audio_input')?.resourceInput).toEqual({ resourceType: 'audio', configKey: 'resource_id' })
    expect(registry.kind('audio_output')?.preview).toEqual({ renderer: 'future_renderer' })
    expect(registry.capabilities.schemaVersion).toBe('1.3')
    payload.workflow.nodes[0].presentation.resource_input.config_key = 'missing_field'
    expect(() => readCapabilities(payload)).toThrow()
  })

  it('accepts the live response shape and derives an editable registry', () => {
    const caps = readCapabilities(livePayload)
    const registry = createNodeRegistry(caps)
    expect(caps.schemaVersion).toBe('1.2')
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
    unsupportedWorkflowSchema.workflow.schema_version = 1.5
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
    extended.workflow.ports.push({ id: 'denoised_audio', type_id: 'audio', label: '降噪音频' })
    const registry = createNodeRegistry(readCapabilities(extended))
    expect(registry.createDefaultConfig('denoise')).toEqual({ strength: 2, mode: 'soft' })
    expect(registry.validateConfig('denoise', { strength: 3, mode: 'hard' })).toEqual([])
    expect(registry.validateConfig('denoise', { strength: 0, mode: 'other' }).map((issue) => issue.code)).toEqual(['INVALID_NODE_CONFIG', 'INVALID_NODE_CONFIG'])
  })

  it('translates built-in presentation without changing raw capabilities, defaults, or ports', () => {
    const caps = readCapabilities(livePayload)
    const original = JSON.stringify(caps)
    const registry = createNodeRegistry(caps)
    expect(registry.label('asr')).toBe('语音识别（Mock）')
    setLocale('en')
    expect(registry.label('asr')).toBe('Mock speech recognition')
    expect(registry.kind('asr')?.fields.find((field) => field.key === 'language')?.label).toBe('Language')
    expect(registry.kind('audio_input')?.addLabel).toBe('Audio input')
    expect(registry.createDefaultConfig('asr')).toEqual({ provider: 'mock', language: 'ja' })
    expect(registry.inputPortForNode('asr')).toBe('audio')
    expect(registry.outputPortForNode('asr')).toBe('transcript')
    expect(JSON.stringify(caps)).toBe(original)
    setLocale('zh-CN')
    expect(registry.label('asr')).toBe('语音识别（Mock）')
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

describe('resource collection capability schema 1.6', () => {
  function payload(): any {
    const value: any = structuredClone(livePayload)
    value.workflow.schema_version = '1.6'
    for (const node of value.workflow.nodes) {
      node.is_parameter_source = false
      node.required_input_ports = node.required_inbound ? node.input_ports : []
      for (const field of node.config.fields) field.input_port = null
    }
    const source = value.workflow.nodes[0]
    source.type = 'input_extension'
    source.presentation = { resource_input: { resource_type: 'audio', config_key: 'chosen', collection_key: 'chosen_many' } }
    source.config.fields = [{ key: 'chosen', type: 'string', required: false, default: null, input_port: null }, { key: 'chosen_many', type: 'json', required: false, default: null, input_port: null }]
    return value
  }
  it('derives an extension collection key and validates ordered unique collections without changing defaults', () => {
    const registry = createNodeRegistry(readCapabilities(payload()))
    expect(registry.kind('input_extension')!.resourceInput).toEqual({ resourceType: 'audio', configKey: 'chosen', collectionKey: 'chosen_many' })
    expect(registry.createDefaultConfig('input_extension')).toEqual({})
    expect(registry.validateConfig('input_extension', { chosen_many: [] })).toEqual([])
    expect(registry.validateConfig('input_extension', { chosen_many: ['b', 'a'] })).toEqual([])
    expect(registry.validateConfig('input_extension', { chosen_many: Array.from({ length: 10001 }, (_, index) => `a${index}`) })).toEqual([])
    expect(registry.validateConfig('input_extension', { chosen_many: ['a', 'a'] })).not.toEqual([])
    expect(registry.validateConfig('input_extension', { chosen: 'a', chosen_many: ['a'] })).not.toEqual([])
  })
  it.each(['missing', 'unknown', 'same', 'parameter', 'type'])('rejects an invalid collection metadata shape: %s', (mutation) => {
    const value = payload()
    const source = value.workflow.nodes[0]
    if (mutation === 'missing') delete source.presentation.resource_input.collection_key
    if (mutation === 'unknown') source.presentation.resource_input.extra = 'x'
    if (mutation === 'same') source.presentation.resource_input.collection_key = 'chosen'
    if (mutation === 'parameter') { source.config.fields[1].input_port = 'audio'; source.input_ports = ['audio'] }
    if (mutation === 'type') source.config.fields[1].type = 'string'
    expect(() => readCapabilities(value)).toThrow()
  })
  it('separates creation defaults from defaults used by existing graph fields', () => {
    const value = payload()
    value.workflow.nodes[2].config.fields = [{ key: 'mode', type: 'select', required: false, default: 'old', creation_default: 'new', input_port: null,
      options: [{ value: 'old', label: 'Old' }, { value: 'new', label: 'New' }] }]
    const registry = createNodeRegistry(readCapabilities(value))
    expect(registry.createDefaultConfig('audio_output')).toEqual({ mode: 'old' })
    expect(registry.createDefaultConfig('audio_output', true)).toEqual({ mode: 'new' })
  })
})
