/**
 * @jest-environment node
 */
import {
  bboxToViewport,
  getLabelBBox,
  parseDeepLink,
  pointToViewport
} from "../../src/common/deep_link"
import { ItemType } from "../../src/types/state"

describe("parseDeepLink", () => {
  test("item is 1-based and converted to an index", () => {
    expect(parseDeepLink("?project_name=p&item=6&label=abc")).toEqual({
      item: 5,
      label: "abc",
      point: null
    })
  })
  test("both params are optional / invalid ignored", () => {
    expect(parseDeepLink("?project_name=p")).toEqual({
      item: null,
      label: null,
      point: null
    })
    expect(parseDeepLink("?item=0&label=")).toEqual({
      item: null,
      label: null,
      point: null
    })
    expect(parseDeepLink("?item=x")).toEqual({
      item: null,
      label: null,
      point: null
    })
    expect(parseDeepLink("?item=3")).toEqual({
      item: 2,
      label: null,
      point: null
    })
  })
})

describe("getLabelBBox", () => {
  const item = {
    labels: {
      a: { shapes: ["s1", "s2"] },
      b: { shapes: ["r"] },
      c: { shapes: ["poly"] }
    },
    shapes: {
      s1: { x: 10, y: 50 },
      s2: { x: 30, y: 20 },
      r: { x1: 1, y1: 2, x2: 11, y2: 12 },
      poly: {
        points: [
          { x: 5, y: 6 },
          { x: 7, y: 1 }
        ]
      }
    }
  } as unknown as ItemType
  test("points, rects, polygons; unknown id -> null", () => {
    expect(getLabelBBox(item, "a")).toEqual([10, 20, 30, 50])
    expect(getLabelBBox(item, "b")).toEqual([1, 2, 11, 12])
    expect(getLabelBBox(item, "c")).toEqual([5, 1, 7, 6])
    expect(getLabelBBox(item, "zzz")).toBeNull()
  })
})

describe("bboxToViewport", () => {
  test("never zooms out below fit image", () => {
    const vp = bboxToViewport([0, 0, 1000, 800], 1000, 800, 500, 400)
    expect(vp.viewScale).toBe(1)
  })
  test("small bbox zooms so padded 7x box fills the limiting axis", () => {
    // fit = 0.5; 100x100 bbox -> 700 image px wanted; height (400) limits
    const vp = bboxToViewport([450, 350, 550, 450], 1000, 800, 500, 400)
    expect(vp.viewScale).toBeCloseTo(400 / 700 / 0.5)
  })
  test("bbox centre lands at container centre (unclamped case)", () => {
    const rectW = 1000
    const rectH = 800
    const bbox: [number, number, number, number] = [4000, 3000, 4100, 3040]
    const vp = bboxToViewport(bbox, 8000, 6000, rectW, rectH)
    expect(vp.viewScale).toBeGreaterThan(1)
    const ratio = Math.min(rectW / 8000, rectH / 6000) * vp.viewScale
    // image is bigger than the container: padX = 0
    expect(vp.displayLeft + 4050 * ratio).toBeCloseTo(rectW / 2)
    expect(vp.displayTop + 3020 * ratio).toBeCloseTo(rectH / 2)
  })
  test("bbox at the image edge is clamped inside the viewport", () => {
    const vp = bboxToViewport([0, 0, 10, 10], 8000, 6000, 1000, 800)
    expect(vp.displayLeft).toBeCloseTo(0)
    expect(vp.displayTop).toBeCloseTo(0)
  })
})

describe("x/y point", () => {
  test("parse valid and invalid", () => {
    expect(parseDeepLink("?item=2&x=10.5&y=300").point).toEqual({
      x: 10.5,
      y: 300
    })
    expect(parseDeepLink("?x=10").point).toBeNull()
    expect(parseDeepLink("?x=a&y=3").point).toBeNull()
  })
  test("pointToViewport shows ~400px window, centred, clamped", () => {
    const vp = pointToViewport(1000, 500, 2000, 1000, 1000, 500)
    // fit = 0.5 css px / img px; wanted = 500/400 = 1.25 -> scale 2.5
    expect(vp.viewScale).toBeCloseTo(2.5)
    // centre: rectW/2 - cx*ratio = 500 - 1000*1.25
    expect(vp.displayLeft).toBeCloseTo(-750)
    expect(vp.displayTop).toBeCloseTo(-375)
    const far = pointToViewport(1000, 500, 100000, 50000, 1000, 500)
    expect(far.viewScale).toBe(60)
    const wide = pointToViewport(1000, 500, 200, 100, 1000, 500, 4000)
    expect(wide.viewScale).toBe(1)
  })
})
