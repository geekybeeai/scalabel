import { findCutSite } from "../../src/drawable/2d/polyline_cut_geometry"
import { PathPointType, SimplePathPoint2DType } from "../../src/types/state"

/**
 * Shorthand: build a path point.
 *
 * @param x x coordinate
 * @param y y coordinate
 * @param pointType point type (defaults to LINE)
 */
function pt(
  x: number,
  y: number,
  pointType: PathPointType = PathPointType.LINE
): SimplePathPoint2DType {
  return { x, y, pointType }
}

describe("findCutSite", () => {
  const RADIUS = 10
  const SNAP = 8

  test("projects a mid-segment click onto the segment", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 50, y: 5 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.segmentIndex).toBe(0)
      expect(result.site.point).toEqual({ x: 50, y: 0 })
      expect(result.site.snappedVertexIndex).toBeNull()
      expect(result.site.distance).toBeCloseTo(5)
    }
  })

  test("misses when the click is outside the radius", () => {
    const points = [pt(0, 0), pt(100, 0)]
    expect(findCutSite(points, { x: 50, y: 30 }, RADIUS, SNAP).kind).toBe(
      "miss"
    )
  })

  test("misses on fewer than 2 points", () => {
    expect(findCutSite([pt(0, 0)], { x: 0, y: 0 }, RADIUS, SNAP).kind).toBe(
      "miss"
    )
    expect(findCutSite([], { x: 0, y: 0 }, RADIUS, SNAP).kind).toBe("miss")
  })

  test("snaps to a nearby interior vertex", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const result = findCutSite(points, { x: 103, y: 4 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.snappedVertexIndex).toBe(1)
      expect(result.site.point).toEqual({ x: 100, y: 0 })
    }
  })

  test("rejects a cut too close to an endpoint", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 2, y: 3 }, RADIUS, SNAP)
    expect(result.kind).toBe("near-endpoint")
  })

  test("rejects a click nearest to a bezier span", () => {
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(points, { x: 45, y: 12 }, RADIUS, SNAP)
    expect(result.kind).toBe("curve")
  })

  test("cuts a straight span of a partially curved polyline", () => {
    // Straight span (100,0)-(200,0) after a bezier group.
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0)
    ]
    const result = findCutSite(points, { x: 150, y: 4 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.segmentIndex).toBe(3)
      expect(result.site.point).toEqual({ x: 150, y: 0 })
    }
  })
})
