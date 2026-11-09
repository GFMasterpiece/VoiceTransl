import { inject, type InjectionKey, type Ref } from 'vue'
import type { CapabilityLanguage } from '../workflow/capabilities'

export interface LanguageSuggestionContext {
  languages: Readonly<Ref<readonly CapabilityLanguage[] | undefined>>
  suspended: Readonly<Ref<boolean>>
  context?: Readonly<Ref<unknown>>
  finishEdit?: () => void
}
export const languageSuggestionsKey: InjectionKey<LanguageSuggestionContext> = Symbol('languageSuggestions')
export function useLanguageSuggestions() { return inject(languageSuggestionsKey, undefined) }

export function languageSearch(value: string) {
  return value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().trim()
}
export function matchesLanguage(language: CapabilityLanguage, query: string) {
  const term = languageSearch(query)
  return [language.code, language.names['zh-CN'], language.names.en, language.nativeName, ...language.aliases]
    .some((value) => languageSearch(value).includes(term))
}
