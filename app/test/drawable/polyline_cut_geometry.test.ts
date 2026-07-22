import {
  buildCutHalves,
  buildSegmentDeletePieces,
  DeleteSitePick,
  findCutSite,
  nearestTOnCubic,
  normalizeDeletePicks,
  resolvePickPoint,
  sitePositionKey,
  splitCubicBezier
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

/**
 * Evaluate a cubic bezier at t (Bernstein form) — test-side oracle.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t curve parameter in [0, 1]
 */
function evalAt(
  a0: { x: number; y: number },
  c1: { x: number; y: number },
  c2: { x: number; y: number },
  a1: { x: number; y: number },
  t: number
): { x: number; y: number } {
  const s = 1 - t
  const w0 = s * s * s
  const w1 = 3 * s * s * t
  const w2 = 3 * s * t * t
  const w3 = t * t * t
  return {
    x: w0 * a0.x + w1 * c1.x + w2 * c2.x + w3 * a1.x,
    y: w0 * a0.y + w1 * c1.y + w2 * c2.y + w3 * a1.y
  }
}

describe("splitCubicBezier", () => {
  const a0 = pt(0, 0)
  const c1 = pt(30, 60, PathPointType.CURVE)
  const c2 = pt(70, 60, PathPointType.CURVE)
  const a1 = pt(100, 0)

  test("t=0.5 splits a symmetric curve symmetrically", () => {
    const split = splitCubicBezier(a0, c1, c2, a1, 0.5)
    expect(split.point.x).toBeCloseTo(50)
    expect(split.point.y).toBeCloseTo(45)
    expect(split.left.c1).toEqual({ x: 15, y: 30 })
    expect(split.right.c2).toEqual({ x: 85, y: 30 })
  })

  test("halves jointly trace the original cubic", () => {
    const t0 = 0.3
    const split = splitCubicBezier(a0, c1, c2, a1, t0)
    for (const u of [0, 0.1, 0.2, 0.3]) {
      const orig = evalAt(a0, c1, c2, a1, u)
      const left = evalAt(a0, split.left.c1, split.left.c2, split.point, u / t0)
      expect(left.x).toBeCloseTo(orig.x, 6)
      expect(left.y).toBeCloseTo(orig.y, 6)
    }
    for (const u of [0.3, 0.5, 0.8, 1]) {
      const orig = evalAt(a0, c1, c2, a1, u)
      const right = evalAt(
        split.point,
        split.right.c1,
        split.right.c2,
        a1,
        (u - t0) / (1 - t0)
      )
      expect(right.x).toBeCloseTo(orig.x, 6)
      expect(right.y).toBeCloseTo(orig.y, 6)
    }
  })
})

describe("nearestTOnCubic", () => {
  const a0 = pt(0, 0)
  const c1 = pt(30, 60, PathPointType.CURVE)
  const c2 = pt(70, 60, PathPointType.CURVE)
  const a1 = pt(100, 0)

  test("recovers t for an on-curve click", () => {
    const target = evalAt(a0, c1, c2, a1, 0.4)
    const near = nearestTOnCubic(a0, c1, c2, a1, target)
    expect(near.distance).toBeLessThan(0.01)
    expect(near.t).toBeCloseTo(0.4, 2)
  })

  test("finds the nearest curve point for an off-curve click", () => {
    // The symmetric curve's apex is (50, 45); click straight above it.
    const near = nearestTOnCubic(a0, c1, c2, a1, { x: 50, y: 55 })
    expect(near.point.x).toBeCloseTo(50, 1)
    expect(near.point.y).toBeCloseTo(45, 1)
    expect(near.distance).toBeCloseTo(10, 1)
  })

  test("clamps to the ends for clicks beyond them", () => {
    const nearStart = nearestTOnCubic(a0, c1, c2, a1, { x: -20, y: -5 })
    expect(nearStart.t).toBeLessThan(0.05)
    const nearEnd = nearestTOnCubic(a0, c1, c2, a1, { x: 120, y: -5 })
    expect(nearEnd.t).toBeGreaterThan(0.95)
  })
})

describe("findCutSite on curved spans (splitCurves)", () => {
  const RADIUS = 10
  const SNAP = 8
  // Straight lead-in, one bezier group [1..4], straight tail. The group's
  // symmetric apex is at (150, 45).
  const points = [
    pt(0, 0),
    pt(100, 0),
    pt(130, 60, PathPointType.CURVE),
    pt(170, 60, PathPointType.CURVE),
    pt(200, 0),
    pt(300, 0)
  ]

  test("without the flag a curve click still rejects as curve", () => {
    // (150, 52): 8 px from the control-polygon span, within RADIUS.
    const result = findCutSite(points, { x: 150, y: 52 }, RADIUS, SNAP)
    expect(result.kind).toBe("curve")
  })

  test("mid-curve click yields a site carrying curveSplit", () => {
    const result = findCutSite(points, { x: 150, y: 52 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.curveSplit).toBeDefined()
      expect(result.site.curveSplit?.groupStart).toBe(1)
      expect(result.site.curveSplit?.t).toBeCloseTo(0.5, 1)
      expect(result.site.segmentIndex).toBe(1)
      expect(result.site.snappedVertexIndex).toBeNull()
      expect(result.site.point.x).toBeCloseTo(150, 0)
      expect(result.site.point.y).toBeCloseTo(45, 0)
      expect(result.site.distance).toBeCloseTo(7, 0)
    }
  })

  test("radius is measured against the true curve, not the polygon", () => {
    // (150, 58): only 2 px from the polygon span but 13 px from the curve.
    const result = findCutSite(points, { x: 150, y: 58 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("miss")
  })

  test("a click near a group anchor snaps to it (no curveSplit)", () => {
    const result = findCutSite(points, { x: 198, y: 6 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.snappedVertexIndex).toBe(4)
      expect(result.site.curveSplit).toBeUndefined()
      expect(result.site.point).toEqual({ x: 200, y: 0 })
    }
  })

  test("snap onto the line's end anchor rejects as near-endpoint", () => {
    // A fully-curved 2-anchor line; click near its start anchor.
    const curveOnly = [
      pt(0, 0),
      pt(30, 60, PathPointType.CURVE),
      pt(70, 60, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(curveOnly, { x: 2, y: 4 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("near-endpoint")
  })

  test("mid-curve cut on a fully-curved 2-anchor line works", () => {
    const curveOnly = [
      pt(0, 0),
      pt(30, 60, PathPointType.CURVE),
      pt(70, 60, PathPointType.CURVE),
      pt(100, 0)
    ]
    const result = findCutSite(curveOnly, { x: 50, y: 48 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("site")
    if (result.kind === "site") {
      expect(result.site.curveSplit?.groupStart).toBe(0)
    }
  })

  test("malformed curve data falls back to the curve rejection", () => {
    // A stray single CURVE point: no well-formed group.
    const malformed = [pt(0, 0), pt(50, 10, PathPointType.CURVE), pt(100, 0)]
    const result = findCutSite(malformed, { x: 25, y: 8 }, RADIUS, SNAP, {
      splitCurves: true
    })
    expect(result.kind).toBe("curve")
  })
})
