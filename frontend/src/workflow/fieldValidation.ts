import type { CapabilityField } from './capabilities'
export function fieldValueErrors(field: CapabilityField, value: unknown): string[] {
  if (value === undefined) return field.required ? ['requiredField'] : []
  if (value === null) return field.nullable ? [] : ['fieldType']
  const matches = field.type === 'boolean' ? typeof value === 'boolean'
    : field.type === 'integer' ? typeof value === 'number' && Number.isInteger(value)
    : field.type === 'number' ? typeof value === 'number' && Number.isFinite(value)
    : field.type === 'json' ? typeof value === 'object' && !Array.isArray(value)
    : typeof value === 'string'
  if (!matches) return ['fieldType']
  const errors: string[] = []
  if (typeof value === 'number') {
    if (field.minimum !== null && value < field.minimum) errors.push('fieldMinimum')
    if (field.maximum !== null && value > field.maximum) errors.push('fieldMaximum')
  }
  if (field.type === 'select' && !field.options.some(option => option.value === value)) errors.push('fieldOption')
  if (typeof value === 'string') {
    const length = [...value].length
    if (field.minLength !== null && length < field.minLength) errors.push('fieldMinLength')
    if (field.maxLength !== null && length > field.maxLength) errors.push('fieldMaxLength')
    if (field.pattern && !new RegExp(field.pattern).test(value)) errors.push('fieldPattern')
  }
  return errors
}
