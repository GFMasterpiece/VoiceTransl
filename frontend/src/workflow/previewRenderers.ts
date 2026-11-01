import type { Component } from 'vue'
import AudioPreview from '../components/AudioPreview.vue'
import TextPreview from '../components/TextPreview.vue'

// A renderer implements the common { url, identity } props. New renderers only
// need a component and a registration here; the node shell stays unchanged.
const renderers = new Map<string, Component>([['audio', AudioPreview], ['text', TextPreview]])
export function previewRenderer(id: string): Component | null { return renderers.get(id) || null }
