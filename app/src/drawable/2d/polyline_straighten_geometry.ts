/**
 * Geometry for the straighten tool: turn one clicked bezier back into a
 * straight span.
 *
 * The inverse of `Polygon2D.lineToCurve`, which converts a MID point into the
 * pair of CURVE control points that make a bezier. Straightening drops that
 * pair, leaving the two LINE anchors joined directly — the curve information is
 * discarded, which is exactly the intent.
 *
 * Only the clicked curve is affected: a polyline with several curves keeps the
 * others, matching how the cut tools act on the spot you click.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in a node
 * environment. All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"
import { curveGroupIndices } from "./curve_groups"

/** A curve that a click would straighten. */
export interface StraightenSite {
  /** index of the group's opening LINE anchor */
  index: number
  /** click-to-curve distance in image px, comparable across polylines */
  distance: number
}

/** Result of searching one polyline for a curve to straighten. */
export type StraightenResult =
  | { kind: "site"; site: StraightenSite }
  | { kind: "miss" }

/** A plain 2D point in the original image frame. */
interface Point2D {
  /** x coordinate */
  x: number
  /** y coordinate */
  y: number
}

/** How finely a bezier is sampled when measuring a click against it. */
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
 * Distance from a point to a segment.
 *
 * @param px query x
 * @param py query y
 * @param ax segment start x
 * @param ay segment start y
 * @param bx segment end x
 * @param by segment end y
 */
function distanceToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): number {
  const dx = bx - ax
  const dy = by - ay
  const lenSq = dx * dx + dy * dy
  let t = 0
  if (lenSq > 0) {
    t = ((px - ax) * dx + (py - ay) * dy) / lenSq
    t = Math.max(0, Math.min(1, t))
  }
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

/**
 * Find which curve on a polyline a click would straighten.
 *
 * Only bezier groups are considered — clicking a straight span is a miss,
 * since there is nothing to straighten there. Each curve is sampled along its
 * drawn path so the click is matched against the visible line rather than the
 * control polygon, which sits well off it.
 *
 * @param points the polyline's stored vertices (never contains MID points)
 * @param click the click position
 * @param click.x click x in image px
 * @param click.y click y in image px
 * @param radius max click-to-curve distance, in image px
 * @param closed whether the shape is closed (allows a wrapping curve group)
 */
export function findStraightenSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  closed: boolean = false
): StraightenResult {
  if (points.length < 4) {
    return { kind: "miss" }
  }

  const groups = curveGroupIndices(
    points.map((p) => p.pointType),
    closed
  )
  let best: StraightenSite | null = null

  for (const group of groups) {
    const [ai, c1i, c2i, bi] = group
    const p0 = points[ai]
    const p1 = points[c1i]
    const p2 = points[c2i]
    const p3 = points[bi]
    let prev = cubicAt(p0, p1, p2, p3, 0)
    for (let s = 1; s <= CURVE_SAMPLES; s++) {
      const curr = cubicAt(p0, p1, p2, p3, s / CURVE_SAMPLES)
      const dist = distanceToSegment(
        click.x,
        click.y,
        prev.x,
        prev.y,
        curr.x,
        curr.y
      )
      if (best === null || dist < best.distance) {
        best = { index: ai, distance: dist }
      }
      prev = curr
    }
  }

  if (best === null || best.distance > radius) {
    return { kind: "miss" }
  }
  return { kind: "site", site: best }
}

/**
 * Remove a curve's control points, leaving its anchors joined by a straight
 * span.
 *
 * The two CURVE points immediately after the opening anchor are dropped. A
 * wrapping group on a closed shape (where the control points straddle the end
 * of the array) is handled by removing them from their actual positions rather
 * than assuming they are contiguous.
 *
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param site a "site" result from findStraightenSite for these points
 */
export function buildStraightened(
  points: readonly SimplePathPoint2DType[],
  site: StraightenSite
): SimplePathPoint2DType[] {
  const n = points.length
  // The control points always follow the opening anchor, modulo the array
  // length so a closed shape's wrapping group is handled too.
  const drop = new Set<number>([(site.index + 1) % n, (site.index + 2) % n])
  const out: SimplePathPoint2DType[] = []
  for (let i = 0; i < n; i++) {
    if (drop.has(i)) {
      continue
    }
    out.push({
      x: points[i].x,
      y: points[i].y,
      pointType: points[i].pointType
    })
  }
  return out
}

/**
 * Remove EVERY curve from a polyline, leaving only its anchors.
 *
 * Used by the whole-line straighten (the A key), where the target is the
 * selected line rather than one clicked curve. Every CURVE control point is
 * dropped; the LINE anchors keep their coordinates, so the line passes through
 * exactly the same vertices with straight spans between them.
 *
 * MID points are not stored on labels (they are reconstructed when a drawable
 * is built), so nothing else needs filtering here.
 *
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 */
export function buildFullyStraightened(
  points: readonly SimplePathPoint2DType[]
): SimplePathPoint2DType[] {
  return points
    .filter((p) => p.pointType !== PathPointType.CURVE)
    .map((p) => ({ x: p.x, y: p.y, pointType: p.pointType }))
}

/**
 * Whether a polyline contains at least one curve.
 *
 * @param points the polyline's stored vertices
 */
export function hasCurve(points: readonly SimplePathPoint2DType[]): boolean {
  return points.some((p) => p.pointType === PathPointType.CURVE)
}
