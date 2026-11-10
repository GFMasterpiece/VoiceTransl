import { expect, it } from 'vitest'
import { connectionEntryPath, connectionPath } from './connectionGeometry'
it('leaves ordinary header edges on their existing cubic', () => {
  expect(connectionPath(100, 50, 420, 300)).toBe('M 100 50 C 244 50, 276 300, 420 300')
})
it.each([
  [420, 50, 420, 300, 400],
  [800, 10, 420, 500, 400],
  [410, -500, 420, 300, 400],
  [100, 300, 420, 300, 400],
  [380, 20, 420, 500, 400],
])('routes every field approach (%s,%s) through the target boundary', (sx, sy, tx, ty, boundary) => {
  const global = connectionPath(sx, sy, tx, ty, boundary)
  const entry = connectionEntryPath(sx, sy, tx, ty, boundary)
  expect(entry).toBe(`M ${boundary} ${ty} L ${tx} ${ty}`)
  expect(global).toBe(`${connectionPath(sx, sy, boundary, ty)} L ${tx} ${ty}`)
  expect(global.endsWith(entry.replace(`M ${boundary} ${ty}`, `${boundary} ${ty}`))).toBe(true)
  const bend = Math.max(48, Math.abs(boundary - sx) * .45)
  expect(global).toContain(`${boundary - bend} ${ty}, ${boundary} ${ty} L`)
})
it('does not invent an internal segment for a socket on the card boundary', () => {
  expect(connectionEntryPath(100, 50, 400, 300, 400)).toBe('')
  expect(connectionPath(100, 50, 400, 300, 400)).toBe(connectionPath(100, 50, 400, 300))
})
