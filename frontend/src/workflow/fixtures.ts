/** Explicit current-protocol declaration builders for editor extension fixtures. */
export function fieldFixture(value: any): any { return { input_port: null, input_type_id: null, widget: null, suggestion_source: null, ...value } }
export function typeFixture(value: any): any { return { presentation: null, ...value } }
export function nodeFixture(value: any): any {
  const resource = value.presentation?.resource_input
  return { debug_only: false, category: null, evaluation_kind: value.is_parameter_source ? 'configuration' : 'processing', can_start_without_inputs: !!value.is_parameter_source, is_parameter_source: false, required_input_ports: value.required_inbound === 0 ? [] : [...(value.input_ports || [])], ...value,
    ...(resource ? { presentation: { ...value.presentation, resource_input: { collection_key: null, ...resource } } } : {}) }
}
export function catalogFixture(value: any): any {
  const workflow = value.workflow
  const ports = (workflow.ports || []).map((port: any) => typeof port === 'string' ? { id: port, type_id: port, label: port } : port)
  return { languages: [], ...value, workflow: { ...workflow, ports, data_types: workflow.data_types || [...new Set(ports.map((port: any) => port.type_id))].map(id => typeFixture({ id, label: id, parent_type_id: null, capabilities: [] })) } }
}
export function runFixture(value: any): any { return { schema_version: '2.0', ...value, workflow: { schema_version: '2.0', ...value.workflow } } }
export function emptySettings() { return { schema_version: '2.0' as const, revision: 0, global_fields: [], global: {}, node_defaults: {} } }
