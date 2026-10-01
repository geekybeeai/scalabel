import {
  buildDisjointHalves,
  disjointableAnchors
} from "../../src/drawable/2d/polyline_disjoint_geometry"
import { PathPointType, SimplePathPoint2DType } from "../../src/types/state"

/**
 * Create a line anchor for a geometry fixture.
 *
 * @param x horizontal coordinate
 * @param y vertical coordinate
 */
function point(x: number, y: number): SimplePathPoint2DType {
  return { x, y, pointType: PathPointType.LINE }
}

describe("closed polyline disjoint geometry", () => {
  test("splits a closed ring at an interior join while preserving the wrap", () => {
    const ring = [point(0, 10), point(10, 0), point(20, 10), point(10, 20)]

    expect(buildDisjointHalves(ring, 1, true)).toEqual({
      first: [point(10, 20), point(0, 10), point(10, 0)],
      second: [point(10, 0), point(20, 10), point(10, 20)]
    })
  })

  test("open rings still expose only interior vertices as joins", () => {
    const ring = [point(0, 0), point(10, 0), point(10, 10), point(0, 10)]

    expect(disjointableAnchors(ring)).toEqual([1, 2])
  })

  test("closed rings expose every corner as a join", () => {
    const ring = [point(0, 0), point(10, 0), point(10, 10), point(0, 10)]

    expect(disjointableAnchors(ring, true)).toEqual([0, 1, 2, 3])
  })

  test("splits a closed ring at its first corner", () => {
    const ring = [point(0, 0), point(10, 0), point(10, 10), point(0, 10)]

    expect(buildDisjointHalves(ring, 0, true)).toEqual({
      first: [point(0, 10), point(0, 0)],
      second: [point(0, 0), point(10, 0), point(10, 10), point(0, 10)]
    })
  })

  test("splits a closed ring at its last corner", () => {
    const ring = [point(0, 0), point(10, 0), point(10, 10), point(0, 10)]

    expect(buildDisjointHalves(ring, 3, true)).toEqual({
      first: [point(0, 0), point(10, 0), point(10, 10), point(0, 10)],
      second: [point(0, 10), point(0, 0)]
    })
  })
})
