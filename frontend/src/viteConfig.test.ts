// @vitest-environment node
import { expect, it } from 'vitest'
import config from '../vite.config'

it('forwards websocket upgrades through the actual development API proxy', () => {
  expect((config as { server: { proxy: Record<string, unknown> } }).server.proxy['/api']).toEqual({ target: 'http://127.0.0.1:8000', ws: true })
})
