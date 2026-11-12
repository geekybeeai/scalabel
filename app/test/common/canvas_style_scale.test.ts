import {
  REFERENCE_CANVAS_PIXEL_RATIO,
  zoomAwareBackingSize
} from "../../src/common/canvas_style_scale"

describe("zoomAwareBackingSize", () => {
  test("preserves the reference backing size at 1x", () => {
    expect(REFERENCE_CANVAS_PIXEL_RATIO).toBe(2)
    expect(zoomAwareBackingSize(6, 1, 1, 2)).toBeCloseTo(6)
    expect(zoomAwareBackingSize(8, 2, 1, 2)).toBeCloseTo(8)
  })

  test("uses the existing square-root zoom curve", () => {
    expect(zoomAwareBackingSize(6, 1, 4, 2)).toBeCloseTo(3)
    expect(zoomAwareBackingSize(8, 2, 4, 2)).toBeCloseTo(4)
  })

  test("keeps the visible CSS size independent when the backing grid can represent it", () => {
    const cssAt2x = zoomAwareBackingSize(12, 1, 4, 2) / 2
    const cssAt1x = zoomAwareBackingSize(12, 1, 4, 1) / 1
    const cssWhenCapped = zoomAwareBackingSize(12, 1, 4, 0.5) / 0.5
    expect(cssAt2x).toBeCloseTo(3)
    expect(cssAt1x).toBeCloseTo(cssAt2x)
    expect(cssWhenCapped).toBeCloseTo(cssAt2x)
  })

  test("preserves reliable backing-pixel floors after the canvas is severely capped", () => {
    expect(zoomAwareBackingSize(6, 1, 60, 0.05)).toBeCloseTo(1)
    expect(zoomAwareBackingSize(8, 2, 60, 0.05)).toBeCloseTo(2)
    expect(zoomAwareBackingSize(12, 3, 60, 0.05)).toBeCloseTo(3)
  })

  test("treats zoom below 1 as 1x", () => {
    expect(zoomAwareBackingSize(8, 2, 0.5, 2)).toBeCloseTo(8)
  })

  test("supports a line-width multiplier without changing the visible floor", () => {
    const thin = zoomAwareBackingSize(6 * 0.5, 1, 4, 1)
    const thick = zoomAwareBackingSize(6 * 2, 1, 4, 1)
    const floored = zoomAwareBackingSize(6 * 0.5, 1, 1000, 0.25) / 0.25
    expect(thin).toBeCloseTo(0.75)
    expect(thick).toBeCloseTo(3)
    expect(floored).toBeCloseTo(4)
  })

  test("does not apply the backing raster floor at the normal 3x resolution switch", () => {
    const thinEdgeBefore = zoomAwareBackingSize(3, 1, 3, 2) / 2
    const thinEdgeAfter = zoomAwareBackingSize(3, 1, 3.01, 1)
    const outlineBefore = zoomAwareBackingSize(1, 0.75, 3, 2) / 2
    const outlineAfter = zoomAwareBackingSize(1, 0.75, 3.01, 1)

    expect(thinEdgeAfter).toBeLessThan(thinEdgeBefore)
    expect(outlineAfter).toBeLessThanOrEqual(outlineBefore)
  })
})
