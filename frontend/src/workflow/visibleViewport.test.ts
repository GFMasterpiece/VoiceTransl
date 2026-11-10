import { describe, expect, it } from 'vitest'
import { visibleViewport } from './visibleViewport'

const stage = { left: 100, top: 50, width: 1000, height: 800 }
describe('visible canvas viewport', () => {
  it('keeps equal socket margins without an intersecting toolbar', () => {
    expect(visibleViewport(stage)).toEqual({ x: 24, y: 24, width: 952, height: 752 })
    expect(visibleViewport(stage, { left: 1200, top: 50, width: 200, height: 100 })).toEqual(visibleViewport(stage))
    expect(visibleViewport(stage, { left: 100, top: 900, width: 500, height: 100 })).toEqual(visibleViewport(stage))
  })
  it('reserves only the actual intersection in stage-local coordinates', () => {
    expect(visibleViewport(stage, { left: 80, top: 20, width: 1000, height: 130 })).toEqual({ x: 24, y: 124, width: 952, height: 652 })
  })
  it('recomputes available height for wrapped narrow toolbars and handles a fully covered stage', () => {
    const narrow = { left: 0, top: 0, width: 390, height: 811 }
    expect(visibleViewport(narrow, { left: 8, top: 8, width: 374, height: 220 })).toEqual({ x: 24, y: 252, width: 342, height: 535 })
    expect(visibleViewport(narrow, { left: 8, top: 8, width: 374, height: 300 }).height).toBe(455)
    expect(visibleViewport(narrow, { left: 8, top: 8, width: 374, height: 900 }).height).toBe(0)
  })
})
