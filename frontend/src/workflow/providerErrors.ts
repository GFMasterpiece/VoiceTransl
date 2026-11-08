import { hasMessage, t } from '../i18n'

export function providerErrorLabel(error: { type?: string; code?: string; message: string }): string | null {
  const code = error.code || error.type
  if (!code || !hasMessage(`connections.error_${code}`)) return null
  const status = code.startsWith('QWEN_') ? error.message.match(/\bHTTP\s*([1-5]\d{2})\b/i)?.[1] : undefined
  return `${t(`connections.error_${code}`)}${status ? ` (${t('connections.httpStatus', { status })})` : ''}`
}
