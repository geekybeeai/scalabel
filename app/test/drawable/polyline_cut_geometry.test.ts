import {
  buildCutHalves,
  buildSegmentDeletePieces,
  DeleteSitePick,
  findCutSite,
  normalizeDeletePicks,
  resolvePickPoint,
  sitePositionKey
} from "../../src/drawable/2d/polyline_cut_geometry"
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

/**
 * Shorthand: an interior projection pick on segment i at (x, y).
 *
 * @param segmentIndex the segment start index
 * @param x cut x
 * @param y cut y
 */
function interior(segmentIndex: number, x: number, y: number): DeleteSitePick {
  return {
    kind: "interior",
    site: {
      segmentIndex,
      point: { x, y },
      snappedVertexIndex: null,
      distance: 0
    }
  }
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

  test("rejects a cut too close to the last endpoint", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 98, y: 3 }, RADIUS, SNAP)
    expect(result.kind).toBe("near-endpoint")
  })

  test("snaps via the segment-end vertex when the click is nearer to it", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const result = findCutSite(points, { x: 97, y: 4 }, RADIUS, SNAP)
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.snappedVertexIndex).toBe(1)
      expect(result.site.point).toEqual({ x: 100, y: 0 })
    }
  })
})

describe("buildCutHalves", () => {
  test("splits mid-segment, both halves sharing the cut coordinate", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const site = {
      segmentIndex: 0,
      point: { x: 50, y: 0 },
      snappedVertexIndex: null,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([pt(0, 0), pt(50, 0)])
    expect(second).toEqual([pt(50, 0), pt(100, 0), pt(200, 0)])
    // Input is not mutated
    expect(points).toHaveLength(3)
  })

  test("splits at a snapped interior vertex without duplicating it in a half", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const site = {
      segmentIndex: 0,
      point: { x: 100, y: 0 },
      snappedVertexIndex: 1,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([pt(0, 0), pt(100, 0)])
    expect(second).toEqual([pt(100, 0), pt(200, 0)])
  })

  test("preserves bezier spans away from the cut", () => {
    const points = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0)
    ]
    const site = {
      segmentIndex: 3,
      point: { x: 150, y: 0 },
      snappedVertexIndex: null,
      distance: 0
    }
    const { first, second } = buildCutHalves(points, site)
    expect(first).toEqual([
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(150, 0)
    ])
    expect(second).toEqual([pt(150, 0), pt(200, 0)])
  })
})

describe("near-endpoint endpointIndex", () => {
  test("identifies the start endpoint", () => {
    const points = [pt(0, 0), pt(100, 0)]
    const result = findCutSite(points, { x: 2, y: 3 }, 10, 8)
    expect(result.kind).toBe("near-endpoint")
    if (result.kind === "near-endpoint") {
      expect(result.endpointIndex).toBe(0)
    }
  })

  test("identifies the last endpoint", () => {
    const points = [pt(0, 0), pt(100, 0), pt(200, 0)]
    const result = findCutSite(points, { x: 198, y: 3 }, 10, 8)
    expect(result.kind).toBe("near-endpoint")
    if (result.kind === "near-endpoint") {
      expect(result.endpointIndex).toBe(2)
    }
  })
})

describe("delete-pick normalization", () => {
  const line = [pt(0, 0), pt(100, 0), pt(200, 0)]

  test("resolvePickPoint resolves interior and end picks", () => {
    expect(resolvePickPoint(line, interior(0, 50, 0))).toEqual({ x: 50, y: 0 })
    expect(resolvePickPoint(line, { kind: "end", endpointIndex: 2 })).toEqual({
      x: 200,
      y: 0
    })
  })

  test("orders picks along the line regardless of click order", () => {
    const a = interior(1, 150, 0)
    const b = interior(0, 50, 0)
    const result = normalizeDeletePicks(line, a, b, 8)
    expect(result.kind).toBe("ok")
    if (result.kind === "ok") {
      expect(result.first).toBe(b)
      expect(result.second).toBe(a)
    }
  })

  test("orders end picks before/after interior picks", () => {
    const start: DeleteSitePick = { kind: "end", endpointIndex: 0 }
    const mid = interior(1, 150, 0)
    expect(sitePositionKey(line, start)).toBeLessThan(
      sitePositionKey(line, mid)
    )
    expect(
      sitePositionKey(line, { kind: "end", endpointIndex: 2 })
    ).toBeGreaterThan(sitePositionKey(line, mid))
  })

  test("orders two picks on the same segment by projection position", () => {
    const nearer = interior(0, 30, 0)
    const farther = interior(0, 80, 0)
    const result = normalizeDeletePicks(line, farther, nearer, 8)
    expect(result.kind).toBe("ok")
    if (result.kind === "ok") {
      expect(result.first).toBe(nearer)
      expect(result.second).toBe(farther)
    }
  })

  test("rejects picks closer than the snap radius", () => {
    const a = interior(0, 50, 0)
    const b = interior(0, 55, 0)
    expect(normalizeDeletePicks(line, a, b, 8).kind).toBe("too-close")
  })

  test("rejects two trims at the same end", () => {
    const a: DeleteSitePick = { kind: "end", endpointIndex: 0 }
    const b: DeleteSitePick = { kind: "end", endpointIndex: 0 }
    expect(normalizeDeletePicks(line, a, b, 8).kind).toBe("too-close")
  })
})

describe("buildSegmentDeletePieces", () => {
  const line = [pt(0, 0), pt(100, 0), pt(200, 0), pt(300, 0)]

  test("middle delete: left keeps head, right keeps tail, doomed spans picks", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(0, 50, 0),
      interior(2, 250, 0)
    )
    expect(left).toEqual([pt(0, 0), pt(50, 0)])
    expect(right).toEqual([pt(250, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(50, 0), pt(100, 0), pt(200, 0), pt(250, 0)])
    expect(line).toHaveLength(4) // input not mutated
  })

  test("both picks on the same segment", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(1, 130, 0),
      interior(1, 170, 0)
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0), pt(130, 0)])
    expect(right).toEqual([pt(170, 0), pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(130, 0), pt(170, 0)])
  })

  test("vertex-snapped picks share the vertex without duplicating it", () => {
    const snapped = (v: number): DeleteSitePick => ({
      kind: "interior",
      site: {
        segmentIndex: v - 1,
        point: { x: line[v].x, y: line[v].y },
        snappedVertexIndex: v,
        distance: 0
      }
    })
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      snapped(1),
      snapped(2)
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0)])
    expect(right).toEqual([pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(100, 0), pt(200, 0)])
  })

  test("start trim: no left piece", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      { kind: "end", endpointIndex: 0 },
      interior(1, 150, 0)
    )
    expect(left).toBeUndefined()
    expect(right).toEqual([pt(150, 0), pt(200, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(0, 0), pt(100, 0), pt(150, 0)])
  })

  test("end trim: no right piece", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      interior(1, 150, 0),
      { kind: "end", endpointIndex: 3 }
    )
    expect(left).toEqual([pt(0, 0), pt(100, 0), pt(150, 0)])
    expect(right).toBeUndefined()
    expect(doomed).toEqual([pt(150, 0), pt(200, 0), pt(300, 0)])
  })

  test("both ends: whole line doomed, no survivors", () => {
    const { left, right, doomed } = buildSegmentDeletePieces(
      line,
      { kind: "end", endpointIndex: 0 },
      { kind: "end", endpointIndex: 3 }
    )
    expect(left).toBeUndefined()
    expect(right).toBeUndefined()
    expect(doomed).toEqual(line)
  })

  test("curve spans in survivors are preserved verbatim", () => {
    const curvy = [
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(200, 0),
      pt(300, 0)
    ]
    const { left, right, doomed } = buildSegmentDeletePieces(
      curvy,
      interior(3, 150, 0),
      interior(4, 250, 0)
    )
    expect(left).toEqual([
      pt(0, 0),
      pt(30, 10, PathPointType.CURVE),
      pt(60, 10, PathPointType.CURVE),
      pt(100, 0),
      pt(150, 0)
    ])
    expect(right).toEqual([pt(250, 0), pt(300, 0)])
    expect(doomed).toEqual([pt(150, 0), pt(200, 0), pt(250, 0)])
  })
})
