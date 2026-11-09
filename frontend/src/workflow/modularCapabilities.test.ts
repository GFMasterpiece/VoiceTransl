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
    expect(registry.kind('translation_context')).toMatchObject({ evaluationKind: 'configuration', canStartWithoutInputs: true, isParameterSource: false, requiredInputPorts: [] })
    expect(registry.kind('model_config')).toMatchObject({ evaluationKind: 'configuration', canStartWithoutInputs: false, requiredInputPorts: ['connection'] })
    expect(registry.kind('service_connection')!.inlineConnectionInput!.timeoutKey).toBeNull()
    expect(registry.portsCompatible('json', 'glossary')).toBe(true)
    expect(registry.portsCompatible('json', 'additional_parameters')).toBe(true)
    expect(registry.portsCompatible('connection', 'model')).toBe(false)
    expect(registry.portsCompatible('model', 'model')).toBe(true)
    expect(registry.kind('llm_translate')).toMatchObject({ evaluationKind: 'processing', fields: [] })
    expect(registry.kind('qwen_asr')!.fields).toEqual([])
    expect(registry.kind('qwen_asr_streaming')!.fields).toEqual([])
    const field = registry.kind('string_value')!.fields[0]!
    expect(field.widget).toBe('multiline')
    expect(workflowFieldHeight(field)).toBe(152)
    expect(registry.kind('translation_context')!.fields.find(field => field.key === 'glossary')!.inputTypeId).toBe('json_object')
  })
  it.each(['1.2', '1.3', '1.4', '1.5', '1.6', '1.7', '1.8', '1.9', '1.10', '1.11', '1.12', '1.13', '1.14', '1.15'])('rejects old catalog %s instead of inferring a new configuration graph', version => {
    const value = fixture(); value.workflow.schema_version = version
    expect(() => readCapabilities(value)).toThrow('workflow.schema_version')
  })
  it.each(['missing evaluation', 'unknown evaluation', 'missing start', 'invalid start', 'processing root', 'missing widget', 'unknown widget', 'numeric multiline', 'language multiline', 'missing binding type', 'json binding missing', 'json binding wrong', 'bad color', 'missing color'])('rejects inconsistent modular metadata: %s', mutation => {
    const value = fixture(); const processor = owner(value, 'llm_translate'); const context = owner(value, 'translation_context'); const string = owner(value, 'string_value').config.fields[0]; const glossary = context.config.fields.find((field: any) => field.key === 'glossary')
    if (mutation === 'missing evaluation') delete processor.evaluation_kind
    if (mutation === 'unknown evaluation') processor.evaluation_kind = 'agent'
    if (mutation === 'missing start') delete processor.can_start_without_inputs
    if (mutation === 'invalid start') processor.can_start_without_inputs = 'false'
    if (mutation === 'processing root') processor.can_start_without_inputs = true
    if (mutation === 'missing widget') delete string.widget
    if (mutation === 'unknown widget') string.widget = 'html'
    if (mutation === 'numeric multiline') { string.type = 'integer'; string.default = 1; string.widget = 'multiline' }
    if (mutation === 'language multiline') context.config.fields[0].widget = 'multiline'
    if (mutation === 'missing binding type') delete glossary.input_type_id
    if (mutation === 'json binding missing') glossary.input_type_id = null
    if (mutation === 'json binding wrong') glossary.input_type_id = 'string'
    if (mutation === 'bad color') value.workflow.data_types[0].presentation = { color: 'red;display:none' }
    if (mutation === 'missing color') value.workflow.data_types[0].presentation = {}
    expect(() => readCapabilities(value)).toThrow()
  })
  it('preserves presentation colors declared by an extension type without interpreting its ID', () => {
    const value = fixture(); value.workflow.data_types.find((type: any) => type.id === 'model_binding').presentation = { color: '#Ab34Ef' }
    expect(readCapabilities(value).dataTypes.find(type => type.id === 'model_binding')!.presentation).toEqual({ color: '#Ab34Ef' })
  })
  it('rejects old settings documents instead of applying their node defaults to the new catalog', () => {
    expect(() => readSettings({ schema_version: '1.0', revision: 0, global_fields: [], global: {}, node_defaults: {} })).toThrow()
  })

})
