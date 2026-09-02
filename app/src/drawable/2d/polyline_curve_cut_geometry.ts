/**
 * Cut-site math for the curve-aware cut tool.
 *
 * The plain cut tool (`polyline_cut_geometry.ts`) refuses any span whose ends
 * are not both LINE anchors, because cutting a bezier by slicing the point
 * array would strand its control points and silently deform the line. This
 * module handles that case properly: it finds where on the curve the click
 * landed and subdivides the bezier there, so both halves keep exactly the
 * curvature they had.
 *
 * Which behaviour applies is decided per click, by what was actually hit:
 * a straight span cuts as a straight span, a curved span subdivides.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in a node
 * environment. All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"
import { curveGroupIndices } from "./curve_groups"

/** Where on a polyline a curve-aware cut would land. */
export interface CurveCutSite {
  /** whether the hit span is a bezier group or a plain straight span */
  kind: "curve" | "straight"
  /**
   * For "straight": index i, the cut lies on points[i] -> points[i + 1].
   * For "curve": the index of the group's opening LINE anchor.
   */
  index: number
  /** cut coordinate in the original image frame */
  point: Point2D
  /** bezier parameter of the cut, 0..1. Only meaningful for "curve". */
  t: number
  /** click-to-line distance in image px, comparable across polylines */
  distance: number
}

/** Result of searching one polyline for a curve-aware cut site. */
export type CurveCutResult =
  | { kind: "site"; site: CurveCutSite }
  | { kind: "near-endpoint"; distance: number }
  | { kind: "miss" }

/** Both halves of a cut polyline, as plain (id-less) path points. */
export interface CurveCutHalves {
  /** everything up to and including the cut point */
  first: SimplePathPoint2DType[]
  /** the cut point and everything after it */
  second: SimplePathPoint2DType[]
}

/** A plain 2D point in the original image frame. */
interface Point2D {
  /** x coordinate */
  x: number
  /** y coordinate */
  y: number
}

/** How finely a bezier is sampled when locating a click along it. */
const CURVE_SAMPLES = 24

/**
 * Evaluate a cubic bezier.
 *
 * @param p0 start anchor
 * @param p1 first control point
 * @param p2 second control point
 * @param p3 end anchor
 * @param t curve parameter, 0..1
 */
function cubicAt(
  p0: Point2D,
  p1: Point2D,
  p2: Point2D,
  p3: Point2D,
  t: number
): Point2D {
  const u = 1 - t
  const a = u * u * u
  const b = 3 * u * u * t
  const c = 3 * u * t * t
  const d = t * t * t
  return {
    x: a * p0.x + b * p1.x + c * p2.x + d * p3.x,
    y: a * p0.y + b * p1.y + c * p2.y + d * p3.y
  }
}

/**
 * Split a cubic bezier at `t` using De Casteljau's algorithm.
 *
 * Returns the control points of the two sub-curves. Both describe exactly the
 * same path as the original, so cutting a curve never changes its shape.
 *
 * @param p0 start anchor
 * @param p1 first control point
 * @param p2 second control point
 * @param p3 end anchor
 * @param t curve parameter to split at, 0..1
 */
export function splitCubic(
  p0: Point2D,
  p1: Point2D,
  p2: Point2D,
  p3: Point2D,
  t: number
): {
  /** control points of the sub-curve before t */
  left: Point2D[]
  /** control points of the sub-curve after t */
  right: Point2D[]
} {
  const lerp = (a: Point2D, b: Point2D): Point2D => ({
    x: a.x + (b.x - a.x) * t,
    y: a.y + (b.y - a.y) * t
  })

  const p01 = lerp(p0, p1)
  const p12 = lerp(p1, p2)
  const p23 = lerp(p2, p3)
  const p012 = lerp(p01, p12)
  const p123 = lerp(p12, p23)
  const mid = lerp(p012, p123)

  return {
    left: [p0, p01, p012, mid],
    right: [mid, p123, p23, p3]
  }
}

/**
 * Distance from a point to a segment, plus the closest point on it.
 *
 * @param px query x
 * @param py query y
 * @param ax segment start x
 * @param ay segment start y
 * @param bx segment end x
 * @param by segment end y
 */
function projectOntoSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): { dist: number; x: number; y: number; t: number } {
  const dx = bx - ax
  const dy = by - ay
  const lenSq = dx * dx + dy * dy
  let t = 0
  if (lenSq > 0) {
    t = ((px - ax) * dx + (py - ay) * dy) / lenSq
    t = Math.max(0, Math.min(1, t))
  }
  const x = ax + t * dx
  const y = ay + t * dy
  return { dist: Math.hypot(px - x, py - y), x, y, t }
}

/**
 * Find where a click would divide a polyline, curves included.
 *
 * Straight spans are measured directly. Bezier groups are sampled along the
 * curve so the click is matched against the drawn shape rather than the
 * control polygon, which would otherwise pull the site away from the visible
 * line. The nearest of all candidates wins.
 *
 * A click on top of either endpoint is rejected: it would only add a duplicate
 * anchor at a coordinate that already has one.
 *
 * @param points the polyline's stored vertices (never contains MID points)
 * @param click the click position
 * @param click.x click x in image px
 * @param click.y click y in image px
 * @param radius max click-to-line distance, in image px
 * @param endpointGuard reject a divide within this distance of either end.
 * Only meaningful for an open line: a closed ring has no free ends, so the
 * guard is skipped there.
 * @param closed whether the shape is a closed ring (allows a wrapping curve
 * group, and disables the endpoint guard)
 */
export function findCurveCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  endpointGuard: number,
  closed: boolean = false
): CurveCutResult {
  if (points.length < 2) {
    return { kind: "miss" }
  }

  const groups = curveGroupIndices(
    points.map((p) => p.pointType),
    closed
  )
  // Index of the group each control point belongs to, so straight-span
  // scanning can skip spans that are really part of a bezier.
  const inGroup = new Set<number>()
  for (const group of groups) {
    for (const index of group) {
      inGroup.add(index)
    }
  }

  let best: CurveCutSite | null = null

  // Straight spans: both ends LINE, and not inside a bezier group.
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]
    const b = points[i + 1]
    if (
      a.pointType !== PathPointType.LINE ||
      b.pointType !== PathPointType.LINE
    ) {
      continue
    }
    if (inGroup.has(i) && inGroup.has(i + 1)) {
      continue
    }
    const proj = projectOntoSegment(click.x, click.y, a.x, a.y, b.x, b.y)
    if (best === null || proj.dist < best.distance) {
      best = {
        kind: "straight",
        index: i,
        point: { x: proj.x, y: proj.y },
        t: proj.t,
        distance: proj.dist
      }
    }
  }

  // Bezier groups: sample the drawn curve and keep the closest sample.
  for (const group of groups) {
    const [ai, c1i, c2i, bi] = group
    const p0 = points[ai]
    const p1 = points[c1i]
    const p2 = points[c2i]
    const p3 = points[bi]
    let prev = cubicAt(p0, p1, p2, p3, 0)
    for (let s = 1; s <= CURVE_SAMPLES; s++) {
      const t1 = s / CURVE_SAMPLES
      const curr = cubicAt(p0, p1, p2, p3, t1)
      const proj = projectOntoSegment(
        click.x,
        click.y,
        prev.x,
        prev.y,
        curr.x,
        curr.y
      )
      if (best === null || proj.dist < best.distance) {
        // Convert the position along this sample chord into a curve parameter.
        const t0 = (s - 1) / CURVE_SAMPLES
        const t = t0 + (t1 - t0) * proj.t
        best = {
          kind: "curve",
          index: ai,
          point: { x: proj.x, y: proj.y },
          t,
          distance: proj.dist
        }
      }
      prev = curr
    }
  }

  if (best === null || best.distance > radius) {
    return { kind: "miss" }
  }

  // A closed ring has no free ends, so nothing to guard against.
  if (closed) {
    return { kind: "site", site: best }
  }

  // A divide right on top of an existing endpoint would add a duplicate
  // anchor at the same coordinate, which is a no-op the user cannot see.
  const first = points[0]
  const last = points[points.length - 1]
  const dFirst = Math.hypot(best.point.x - first.x, best.point.y - first.y)
  const dLast = Math.hypot(best.point.x - last.x, best.point.y - last.y)
  if (dFirst <= endpointGuard || dLast <= endpointGuard) {
    return { kind: "near-endpoint", distance: best.distance }
  }

  return { kind: "site", site: best }
}

/**
 * Divide a curve in place, keeping the polyline as ONE line.
 *
 * The bezier under the click is subdivided at that point and BOTH sub-curves
 * are written back into the same point list, joined by a new LINE anchor at the
 * cut. The drawn shape is unchanged — De Casteljau is exact — but the curve is
 * now two adjustable groups instead of one, so each side can be reshaped
 * independently.
 *
 * A click on a straight span inserts a plain LINE vertex there, which is the
 * same idea: a new anchor, no change to the drawn shape.
 *
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param site a "site" result from findCurveCutSite for these points
 */
export function buildDividedCurve(
  points: readonly SimplePathPoint2DType[],
  site: CurveCutSite
): SimplePathPoint2DType[] {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })
  const line = (p: Point2D): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: PathPointType.LINE
  })
  const control = (p: Point2D): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: PathPointType.CURVE
  })

  if (site.kind === "straight") {
    const i = site.index
    return [
      ...points.slice(0, i + 1).map(copy),
      line(site.point),
      ...points.slice(i + 1).map(copy)
    ]
  }

  // Curve: replace [anchor, c1, c2, anchor] with
  // [anchor, c1a, c2a, newAnchor, c1b, c2b, anchor] — one line, two groups.
  const n = points.length
  const ai = site.index
  // Modular indices: on a closed ring the group can wrap past the array end.
  const { left, right } = splitCubic(
    points[ai],
    points[(ai + 1) % n],
    points[(ai + 2) % n],
    points[(ai + 3) % n],
    site.t
  )
  const replacement = [
    line(left[0]),
    control(left[1]),
    control(left[2]),
    line(left[3]),
    control(right[1]),
    control(right[2]),
    line(right[3])
  ]

  if (ai + 3 < n) {
    // Contiguous group: splice the replacement over [ai, ai + 3].
    return [
      ...points.slice(0, ai).map(copy),
      ...replacement,
      ...points.slice(ai + 4).map(copy)
    ]
  }

  // Wrapping group (closed ring only). Keep every point that is NOT part of
  // the group, then append the replacement. Rotating the array like this is
  // safe because the ring is cyclic, and it leaves points[0] a LINE anchor —
  // the invariant draw(), updateShapes and curveGroupIndices all rely on.
  const groupIndices = new Set<number>([
    ai,
    (ai + 1) % n,
    (ai + 2) % n,
    (ai + 3) % n
  ])
  const rest: SimplePathPoint2DType[] = []
  for (let i = 0; i < n; i++) {
    if (!groupIndices.has(i)) {
      rest.push(copy(points[i]))
    }
  }
  return [...replacement, ...rest]
}

/**
 * Build the two halves of a curve-aware cut.
 *
 * A straight-span cut inserts a coincident LINE vertex in both halves, exactly
 * as the plain cut tool does. A curve cut subdivides the bezier: each half
 * receives its own control points from De Casteljau, so the two pieces together
 * trace the original curve precisely.
 *
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param site a "site" result from findCurveCutSite for these points
 */
export function buildCurveCutHalves(
  points: readonly SimplePathPoint2DType[],
  site: CurveCutSite
): CurveCutHalves {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })
  const line = (p: Point2D): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: PathPointType.LINE
  })
  const control = (p: Point2D): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: PathPointType.CURVE
  })

  if (site.kind === "straight") {
    const i = site.index
    return {
      first: [...points.slice(0, i + 1).map(copy), line(site.point)],
      second: [line(site.point), ...points.slice(i + 1).map(copy)]
    }
  }

  // Curve: the group occupies [ai, ai+1, ai+2, ai+3].
  const ai = site.index
  const p0 = points[ai]
  const p1 = points[ai + 1]
  const p2 = points[ai + 2]
  const p3 = points[ai + 3]
  const { left, right } = splitCubic(p0, p1, p2, p3, site.t)

  return {
    first: [
      ...points.slice(0, ai).map(copy),
      line(left[0]),
      control(left[1]),
      control(left[2]),
      line(left[3])
    ],
    second: [
      line(right[0]),
      control(right[1]),
      control(right[2]),
      line(right[3]),
      ...points.slice(ai + 4).map(copy)
    ]
  }
}
