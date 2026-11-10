export const translationMessages = {
  'zh-CN': {
    error_LLM_CONTEXT_INVALID: '翻译上下文必须为非空的有效文本。',
    error_QWEN_CONTEXT_INVALID: '识别上下文必须为有效文本。',
    error_QWEN_CONTEXT_TOO_LONG: '识别上下文不能超过 400 个字符。',
    error_CURRENT_TEXT_INVALID: '当前文本信息无效。', error_CURRENT_TEXT_MISSING: '上游当前文本不完整。',
    node_llm_translate_label: 'LLM 翻译', node_llm_translate_add: 'LLM 翻译', category_translation: '翻译',
    error_LLM_TRANSLATION_CONFIG_INVALID: '翻译配置无效，请检查连接、模型及参数。', error_LLM_TRANSLATION_CREDENTIALS_MISSING: '请在服务连接节点设置 API Key。', error_LLM_TRANSLATION_INPUT_INVALID: '上游文本数据无效。', error_LLM_TRANSLATION_INPUT_EMPTY: '上游没有可翻译的文本。', error_LLM_TRANSLATION_SEGMENT_TOO_LONG: '单个片段超过每批最大字符数。', error_LLM_TRANSLATION_HTTP_ERROR: '翻译服务拒绝请求，请检查认证、配额及参数。', error_LLM_TRANSLATION_CONNECTION_FAILED: '无法连接翻译服务，请检查地址及网络。', error_LLM_TRANSLATION_TIMEOUT: '翻译请求超时。', error_LLM_TRANSLATION_RESPONSE_INVALID: '翻译服务返回格式无效。', error_LLM_TRANSLATION_RESPONSE_TOO_LARGE: '翻译响应超过大小限制。', error_LLM_TRANSLATION_OUTPUT_INVALID: '译文与输入片段不匹配，或输出语言无效。', error_LLM_TRANSLATION_REFUSED: '模型拒绝了翻译请求。', error_LLM_TRANSLATION_INCOMPLETE: '模型返回的翻译不完整。', error_LLM_TRANSLATION_FAILED: '翻译请求失败。', error_LLM_TRANSLATION_CANCELLED: '翻译已取消。',
  },
  en: {
    error_LLM_CONTEXT_INVALID: 'Translation context must be valid, nonempty text.',
    error_QWEN_CONTEXT_INVALID: 'Recognition context must be valid text.',
    error_QWEN_CONTEXT_TOO_LONG: 'Recognition context cannot exceed 400 characters.',
    error_CURRENT_TEXT_INVALID: 'The current text metadata is invalid.', error_CURRENT_TEXT_MISSING: 'The current upstream text is incomplete.',
    node_llm_translate_label: 'LLM translation', node_llm_translate_add: 'LLM translation', category_translation: 'Translation',
    error_LLM_TRANSLATION_CONFIG_INVALID: 'Invalid translation configuration. Check the connection, model and parameters.', error_LLM_TRANSLATION_CREDENTIALS_MISSING: 'Set an API Key in the service connection node.', error_LLM_TRANSLATION_INPUT_INVALID: 'The upstream text data is invalid.', error_LLM_TRANSLATION_INPUT_EMPTY: 'The upstream output contains no text to translate.', error_LLM_TRANSLATION_SEGMENT_TOO_LONG: 'A segment exceeds the maximum characters per batch.', error_LLM_TRANSLATION_HTTP_ERROR: 'The translation service rejected the request. Check authentication, quota and parameters.', error_LLM_TRANSLATION_CONNECTION_FAILED: 'Unable to connect to the translation service. Check its URL and the network.', error_LLM_TRANSLATION_TIMEOUT: 'The translation request timed out.', error_LLM_TRANSLATION_RESPONSE_INVALID: 'The translation service returned an invalid response.', error_LLM_TRANSLATION_RESPONSE_TOO_LARGE: 'The translation response exceeds the size limit.', error_LLM_TRANSLATION_OUTPUT_INVALID: 'The translated output does not match the input segments, or its output language is invalid.', error_LLM_TRANSLATION_REFUSED: 'The model refused the translation request.', error_LLM_TRANSLATION_INCOMPLETE: 'The model returned an incomplete translation.', error_LLM_TRANSLATION_FAILED: 'The translation request failed.', error_LLM_TRANSLATION_CANCELLED: 'Translation cancelled.',
  },
}
