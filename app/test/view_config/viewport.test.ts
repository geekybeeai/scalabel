/** @jest-environment node */

import { makeImageViewerConfig } from "../../src/functional/states"
import { Vector2D } from "../../src/math/vector2d"
import { ImageViewerConfigType } from "../../src/types/state"
import {
  ANNOTATION_RESOLUTION,
  applyViewportTransform,
  imageToViewport,
  makeViewportTransform,
  viewportImageBounds,
  viewportToImage,
  ViewportTransform
} from "../../src/view_config/viewport"

const ROTATIONS = [0, 90, 180, 270]
const IMAGE_WIDTH = 1600
const IMAGE_HEIGHT = 900
const PANE_WIDTH = 1000
const PANE_HEIGHT = 700

/** A canvas context that only tracks its 2D affine transform. */
class MatrixContext {
  private m = [1, 0, 0, 1, 0, 0]

  /**
   * Replace the transform.
   *
   * @param a a
   * @param b b
   * @param c c
   * @param d d
   * @param e e
   * @param f f
   */
  public setTransform(
    a: number,
    b: number,
    c: number,
    d: number,
    e: number,
    f: number
  ): void {
    this.m = [a, b, c, d, e, f]
  }

  /**
   * Translate, as CanvasRenderingContext2D.translate.
   *
   * @param x x
   * @param y y
   */
  public translate(x: number, y: number): void {
    const [a, b, c, d, e, f] = this.m
    this.m = [a, b, c, d, e + a * x + c * y, f + b * x + d * y]
  }

  /** @param angle radians */
  public rotate(angle: number): void {
    const [a, b, c, d, e, f] = this.m
    const cos = Math.cos(angle)
    const sin = Math.sin(angle)
    this.m = [
      a * cos + c * sin,
      b * cos + d * sin,
      c * cos - a * sin,
      d * cos - b * sin,
      e,
      f
    ]
  }

  /**
   * Where a point drawn in this context lands, in device pixels.
   *
   * @param x x
   * @param y y
   */
  public apply(x: number, y: number): [number, number] {
    const [a, b, c, d, e, f] = this.m
    return [a * x + c * y + e, b * x + d * y + f]
  }
}

/**
 * A panned and zoomed view at the given rotation.
 *
 * @param rotation degrees
 */
function view(rotation: number): ViewportTransform {
  const config: ImageViewerConfigType = {
    ...makeImageViewerConfig(0),
    rotation,
    viewScale: 2.5,
    displayLeft: -120,
    displayTop: -45
  }
  return makeViewportTransform(
    PANE_WIDTH,
    PANE_HEIGHT,
    IMAGE_WIDTH,
    IMAGE_HEIGHT,
    config
  )
}

const SAMPLES: Array<[number, number]> = [
  [0, 0],
  [IMAGE_WIDTH, 0],
  [0, IMAGE_HEIGHT],
  [IMAGE_WIDTH, IMAGE_HEIGHT],
  [123.5, 456.25]
]

describe.each(ROTATIONS)("viewport mapping at rotation %d", (rotation) => {
  test("drawing and clicking use the same projection", () => {
    // Drawables paint image points scaled by scale * ANNOTATION_RESOLUTION
    // into a context set up by applyViewportTransform. That must land on the
    // same device pixel as imageToViewport, which picking and the cursor use.
    const v = view(rotation)
    const pixelRatio = 1.5
    const context = new MatrixContext()
    applyViewportTransform(
      context as unknown as CanvasRenderingContext2D,
      v,
      pixelRatio
    )
    const ratio = v.scale * ANNOTATION_RESOLUTION
    for (const [x, y] of SAMPLES) {
      const [dx, dy] = context.apply(x * ratio, y * ratio)
      const expected = imageToViewport(new Vector2D(x, y), v)
      expect(dx).toBeCloseTo(expected.x * pixelRatio, 6)
      expect(dy).toBeCloseTo(expected.y * pixelRatio, 6)
    }
  })

  test("viewportToImage inverts imageToViewport", () => {
    const v = view(rotation)
    for (const [x, y] of SAMPLES) {
      const back = viewportToImage(imageToViewport(new Vector2D(x, y), v), v)
      expect(back.x).toBeCloseTo(x, 6)
      expect(back.y).toBeCloseTo(y, 6)
    }
  })

  test("clamping keeps a point outside the image on its edge", () => {
    const v = view(rotation)
    const outside = imageToViewport(new Vector2D(-50, IMAGE_HEIGHT + 50), v)
    const clamped = viewportToImage(outside, v, true)
    expect(clamped.x).toBeCloseTo(0, 6)
    expect(clamped.y).toBeCloseTo(IMAGE_HEIGHT, 6)
  })

  test("the visible bounds contain exactly what the pane shows", () => {
    const v = view(rotation)
    const [bx, by, bw, bh] = viewportImageBounds(v)
    const corners = [
      [0, 0],
      [PANE_WIDTH, 0],
      [0, PANE_HEIGHT],
      [PANE_WIDTH, PANE_HEIGHT]
    ]
    for (const [cx, cy] of corners) {
      const p = viewportToImage(new Vector2D(cx, cy), v)
      expect(p.x).toBeGreaterThanOrEqual(bx - 1e-6)
      expect(p.x).toBeLessThanOrEqual(bx + bw + 1e-6)
      expect(p.y).toBeGreaterThanOrEqual(by - 1e-6)
      expect(p.y).toBeLessThanOrEqual(by + bh + 1e-6)
    }
    // A quarter turn swaps which image axis spans the pane's width.
    const span = PANE_WIDTH / v.scale
    expect(rotation % 180 === 0 ? bw : bh).toBeCloseTo(span, 6)
  })
})

describe("fitting the image into the pane", () => {
  test.each(ROTATIONS)(
    "at reset zoom the rotated image fits and is centred (rotation %d)",
    (rotation) => {
      const v = makeViewportTransform(
        PANE_WIDTH,
        PANE_HEIGHT,
        IMAGE_WIDTH,
        IMAGE_HEIGHT,
        { ...makeImageViewerConfig(0), rotation }
      )
      const corners = SAMPLES.slice(0, 4).map(([x, y]) =>
        imageToViewport(new Vector2D(x, y), v)
      )
      const xs = corners.map((p) => p.x)
      const ys = corners.map((p) => p.y)
      const [minX, maxX] = [Math.min(...xs), Math.max(...xs)]
      const [minY, maxY] = [Math.min(...ys), Math.max(...ys)]
      expect(minX).toBeGreaterThanOrEqual(-1e-6)
      expect(minY).toBeGreaterThanOrEqual(-1e-6)
      expect(maxX).toBeLessThanOrEqual(PANE_WIDTH + 1e-6)
      expect(maxY).toBeLessThanOrEqual(PANE_HEIGHT + 1e-6)
      // Fills one dimension, centred in the other.
      const fillsWidth = Math.abs(maxX - minX - PANE_WIDTH) < 1e-6
      const fillsHeight = Math.abs(maxY - minY - PANE_HEIGHT) < 1e-6
      expect(fillsWidth || fillsHeight).toBe(true)
      expect((minX + maxX) / 2).toBeCloseTo(PANE_WIDTH / 2, 6)
      expect((minY + maxY) / 2).toBeCloseTo(PANE_HEIGHT / 2, 6)
    }
  )
})
