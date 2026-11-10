import { setLocale } from '../i18n'
import { describe, expect, it } from 'vitest'
import catalog from './modularCapabilities.fixture.json'
import { readCapabilities } from './capabilities'
import { createNodeRegistry } from './nodeRegistry'
import { readSettings } from '../composables/globalSettings'
import { workflowFieldHeight } from '../stores/workflow'

const fixture = (): any => structuredClone(catalog)
const owner = (value: any, type: string) => value.workflow.nodes.find((node: any) => node.type === type)
describe('modular capability declarations', () => {
  it('reads typed builders, optional parameter dependencies and configuration roots without a node whitelist', () => {
    const capabilities = readCapabilities(fixture())
    const registry = createNodeRegistry(capabilities)
    expect(registry.kind('text_join')).toMatchObject({ evaluationKind: 'configuration', canStartWithoutInputs: true, isParameterSource: false, requiredInputPorts: [] })
    expect(registry.kind('llm_model')).toMatchObject({ evaluationKind: 'configuration', canStartWithoutInputs: false, requiredInputPorts: ['llm_connection'] })
    expect(registry.kind('llm_service')!.inlineConnectionInput!.timeoutKey).toBeNull()
    expect(registry.portsCompatible('string', 'context')).toBe(true)
    expect(registry.portsCompatible('json', 'additional_parameters')).toBe(true)
    expect(registry.portsCompatible('asr_connection', 'llm_model')).toBe(false)
    expect(registry.portsCompatible('llm_model', 'llm_model')).toBe(true)
    expect(registry.kind('llm_translate')).toMatchObject({ evaluationKind: 'processing', fields: [expect.objectContaining({ key: 'context', inputPort: 'context', widget: 'multiline' })] })
    expect(registry.kind('qwen_asr')!.fields).toEqual([])
    expect(registry.kind('qwen_asr_streaming')!.fields).toEqual([])
    const field = registry.kind('string_value')!.fields[0]!
    expect(field.widget).toBe('multiline')
    expect(workflowFieldHeight(field)).toBe(124)
    expect(registry.kind('text_join')!.dynamicInputs!.field.widget).toBe('multiline')
  })
  it.each(['1.2', '1.3', '1.4', '1.5', '1.6', '1.7', '1.8', '1.9', '1.10', '1.11', '1.12', '1.13', '1.14', '1.15', '1.16', '1.17'])('rejects old catalog %s instead of inferring a new configuration graph', version => {
    const value = fixture(); value.workflow.schema_version = version
    expect(() => readCapabilities(value)).toThrow('workflow.schema_version')
  })
  it.each(['missing evaluation', 'unknown evaluation', 'missing start', 'invalid start', 'processing root', 'missing widget', 'unknown widget', 'numeric multiline', 'language multiline', 'missing binding type', 'json binding missing', 'json binding wrong', 'bad color', 'missing color'])('rejects inconsistent modular metadata: %s', mutation => {
    const value = fixture(); const processor = owner(value, 'llm_translate'); const context = owner(value, 'llm_model'); const string = owner(value, 'string_value').config.fields[0]; const glossary = context.config.fields.find((field: any) => field.key === 'additional_parameters')
    if (mutation === 'missing evaluation') delete processor.evaluation_kind
    if (mutation === 'unknown evaluation') processor.evaluation_kind = 'agent'
    if (mutation === 'missing start') delete processor.can_start_without_inputs
    if (mutation === 'invalid start') processor.can_start_without_inputs = 'false'
    if (mutation === 'processing root') processor.can_start_without_inputs = true
    if (mutation === 'missing widget') delete string.widget
    if (mutation === 'unknown widget') string.widget = 'html'
    if (mutation === 'numeric multiline') { string.type = 'integer'; string.default = 1; string.widget = 'multiline' }
    if (mutation === 'language multiline') { string.suggestion_source = 'languages'; string.widget = 'multiline' }
    if (mutation === 'missing binding type') delete glossary.input_type_id
    if (mutation === 'json binding missing') glossary.input_type_id = null
    if (mutation === 'json binding wrong') glossary.input_type_id = 'string'
    if (mutation === 'bad color') value.workflow.data_types[0].presentation = { color: 'red;display:none' }
    if (mutation === 'missing color') value.workflow.data_types[0].presentation = {}
    expect(() => readCapabilities(value)).toThrow()
  })
  it('preserves presentation colors declared by an extension type without interpreting its ID', () => {
    const value = fixture(); value.workflow.data_types.find((type: any) => type.id === 'llm_model').presentation = { color: '#Ab34Ef' }
    expect(readCapabilities(value).dataTypes.find(type => type.id === 'llm_model')!.presentation).toEqual({ color: '#Ab34Ef' })
  })
  it('rejects old settings documents instead of applying their node defaults to the new catalog', () => {
    expect(() => readSettings({ schema_version: '1.0', revision: 0, global_fields: [], global: {}, node_defaults: {} })).toThrow()
  })

})

it('resolves ordered dynamic extension slots without mutating global ports and applies field bounds', () => {
  const value = fixture(); const declaration = owner(value, 'text_join')
  declaration.type = 'extension_join'; declaration.dynamic_inputs.field.max_length = 4
  const caps = readCapabilities(value); const registry = createNodeRegistry(caps)
  const config = { separator: '', items: [{ id: 'last', value: 'end' }, { id: 'first', value: 'start' }] }
  const node = { type: 'extension_join', config }
  expect(registry.inputPortsForNode(node.type, config)).toEqual(['separator', 'text_last', 'text_first'])
  expect(registry.fieldsForNode(node.type, config).map(field => field.key)).toEqual(['separator', 'items.last.value', 'items.first.value'])
  expect(registry.port('text_last')).toBeNull()
  expect(registry.port('text_last', node)?.typeId).toBe('string')
  expect(registry.portsCompatible('string', 'text_last', node)).toBe(true)
  expect(registry.validateConfig(node.type, config)).toMatchObject([{ field: 'items.first.value', code: 'INVALID_NODE_CONFIG' }])
  const defaults = registry.createDefaultConfig(node.type)
  expect(defaults.items).toEqual(declaration.dynamic_inputs.default_items)
  ;(defaults.items as any[])[0].value = 'changed'
  expect(registry.createDefaultConfig(node.type).items).toEqual(declaration.dynamic_inputs.default_items)
})
it.each(['missing defaults', 'duplicate id', 'invalid id', 'too many', 'bad value', 'overlapping fixed port'])('rejects malformed dynamic metadata: %s', mutation => {
  const value = fixture(); const declaration = owner(value, 'text_join'); const d = declaration.dynamic_inputs
  if (mutation === 'missing defaults') delete d.default_items
  if (mutation === 'duplicate id') d.default_items[1].id = d.default_items[0].id
  if (mutation === 'invalid id') d.default_items[0].id = '../unsafe'
  if (mutation === 'too many') d.max_items = 33
  if (mutation === 'bad value') d.default_items[0].value = 12
  if (mutation === 'overlapping fixed port') { d.port_prefix = 'separa_'; declaration.input_ports.push('separa_fixed') }
  expect(() => readCapabilities(value)).toThrow()
})
it('validates nullable fields independently from connection presentation', () => {
  const value = fixture(); const service = owner(value, 'llm_service'); service.presentation = {}; service.config.fields.push({ ...service.config.fields.find((field: any) => field.key === 'endpoint'), key: 'region', default: null, nullable: true })
  const registry = createNodeRegistry(readCapabilities(value))
  expect(registry.validateConfig('llm_service', { endpoint: 'www.qq.com', region: null })).toEqual([])
  expect(registry.validateConfig('llm_service', { endpoint: null })).not.toEqual([])
})

it('accepts 32 ordered string inputs and rejects invalid slot identity, shape and count', () => {
  const registry = createNodeRegistry(readCapabilities(fixture()))
  const items = Array.from({ length: 32 }, (_, index) => ({ id: `s${index}`, value: '' }))
  expect(registry.validateConfig('text_join', { items, separator: '' })).toEqual([])
  expect(registry.inputPortsForNode('text_join', { items })).toHaveLength(33)
  for (const invalid of [[], [...items, { id: 'overflow', value: '' }], [{ id: 'same', value: '' }, { id: 'same', value: '' }], [{ id: 'Invalid', value: '' }], [{ id: 'safe' }], [{ id: 'safe', value: 1 }]]) {
    expect(registry.validateConfig('text_join', { items: invalid, separator: '' })).not.toEqual([])
  }
})

it('counts nullable string bounds in Unicode code points consistently with the backend', () => {
  const value = fixture(); const service = owner(value, 'llm_service')
  const region = { ...service.config.fields.find((field: any) => field.key === 'endpoint'), key: 'region', nullable: true, default: null }; service.config.fields.push(region)
  region.min_length = 1; region.max_length = 1
  const registry = createNodeRegistry(readCapabilities(value))
  const config = { endpoint: '', region: '𠮷' }
  expect(registry.validateConfig('llm_service', config)).toEqual([])
  expect(registry.validateConfig('llm_service', { ...config, region: '𠮷𠮷' })).toMatchObject([{ field: 'region', code: 'INVALID_NODE_CONFIG' }])
  expect(registry.validateConfig('llm_service', { ...config, region: '' })).toMatchObject([{ field: 'region', code: 'INVALID_NODE_CONFIG' }])
  expect(registry.validateConfig('llm_service', { ...config, region: null })).toEqual([])
})

it('localizes default family port labels while retaining explicitly named extension ports', () => {
  const registry = createNodeRegistry(readCapabilities(fixture()))
  try {
    setLocale('zh-CN')
    expect(['llm_model', 'asr_model', 'llm_connection', 'asr_connection'].map(id => registry.port(id)!.label)).toEqual(['LLM 模型', 'ASR 模型', 'LLM 服务', 'ASR 服务'])
    setLocale('en')
    expect(['llm_model', 'asr_model', 'llm_connection', 'asr_connection'].map(id => registry.port(id)!.label)).toEqual(['LLM model', 'ASR model', 'LLM service', 'ASR service'])
    const value = fixture(); value.workflow.ports.find((port: any) => port.id === 'llm_model').label = 'Custom deployment model'
    expect(createNodeRegistry(readCapabilities(value)).port('llm_model')!.label).toBe('Custom deployment model')
  } finally { setLocale('zh-CN') }
})
