/**
 * Geometry for the simplify tool: merge consecutive straight spans that run in
 * nearly the same direction.
 *
 * Traced lines often carry vertices that add no shape — three points in a row
 * that are all but collinear describe one straight span, not two. Dropping the
 * middle vertex leaves the drawn line effectively unchanged while making it
 * cheaper to edit and export.
 *
 * Curves are never touched. A vertex is only removable when it is a LINE anchor
 * whose neighbours are also LINE anchors and none of the three belongs to a
 * bezier group — removing an anchor that bounds a curve would strand its
 * control points.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in a node
 * environment. All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"
import { curveGroupIndices } from "./curve_groups"

/**
 * Maximum turn, in degrees, for a vertex to count as redundant.
 *
 * At 5 degrees a dropped vertex moves the line by well under a pixel over a
 * typical span, so the simplification is invisible at annotation scale.
 */
export const DEFAULT_MAX_TURN_DEGREES = 5

/** What a simplify pass changed. */
export interface SimplifyResult {
  /** the surviving vertices */
  points: SimplePathPoint2DType[]
  /** how many vertices were dropped */
  removed: number
}

/**
 * Turn angle at a vertex, in degrees.
 *
 * 0 means the two spans continue in exactly the same direction; 180 means the
 * line doubles straight back on itself.
 *
 * @param prev the preceding vertex
 * @param mid the vertex being measured
 * @param next the following vertex
 */
function turnDegrees(
  prev: SimplePathPoint2DType,
  mid: SimplePathPoint2DType,
  next: SimplePathPoint2DType
): number {
  const ax = mid.x - prev.x
  const ay = mid.y - prev.y
  const bx = next.x - mid.x
  const by = next.y - mid.y
  const na = Math.hypot(ax, ay)
  const nb = Math.hypot(bx, by)
  if (na === 0 || nb === 0) {
    // A duplicate point has no direction of its own and is always redundant.
    return 0
  }
  const cos = Math.max(-1, Math.min(1, (ax * bx + ay * by) / (na * nb)))
  return (Math.acos(cos) * 180) / Math.PI
}

/**
 * Drop vertices that lie on a nearly straight run.
 *
 * Sweeps left to right, measuring each candidate against the last KEPT vertex
 * rather than its original neighbour. That prevents a long shallow arc from
 * being flattened one vertex at a time: each removal has to stay within
 * tolerance of the span actually being formed.
 *
 * Endpoints are always kept, so the line still starts and ends where it did.
 * A closed ring keeps its first vertex as the anchor for the same reason plus
 * the ring invariant (`points[0]` must be a LINE anchor).
 *
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param maxTurnDegrees how much a vertex may turn and still count as redundant
 * @param closed whether the shape is a closed ring
 */
export function simplifyPolyline(
  points: readonly SimplePathPoint2DType[],
  maxTurnDegrees: number = DEFAULT_MAX_TURN_DEGREES,
  closed: boolean = false
): SimplifyResult {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })

  if (points.length < 3) {
    return { points: points.map(copy), removed: 0 }
  }

  // Any index touched by a bezier group is off limits: removing an anchor that
  // bounds a curve would strand its control points.
  const locked = new Set<number>()
  for (const group of curveGroupIndices(
    points.map((p) => p.pointType),
    closed
  )) {
    for (const index of group) {
      locked.add(index)
    }
  }

  const kept: SimplePathPoint2DType[] = [copy(points[0])]
  let removed = 0

  for (let i = 1; i < points.length - 1; i++) {
    const point = points[i]
    const next = points[i + 1]
    const previous = kept[kept.length - 1]

    const removable =
      !locked.has(i) &&
      point.pointType === PathPointType.LINE &&
      previous.pointType === PathPointType.LINE &&
      next.pointType === PathPointType.LINE &&
      turnDegrees(previous, point, next) <= maxTurnDegrees

    if (removable) {
      removed += 1
      continue
    }
    kept.push(copy(point))
  }

  kept.push(copy(points[points.length - 1]))
  return { points: kept, removed }
}

/**
 * How many vertices a simplify pass would drop, without building the result.
 *
 * @param points the polyline's stored vertices
 * @param maxTurnDegrees how much a vertex may turn and still count as redundant
 * @param closed whether the shape is a closed ring
 */
export function countRemovable(
  points: readonly SimplePathPoint2DType[],
  maxTurnDegrees: number = DEFAULT_MAX_TURN_DEGREES,
  closed: boolean = false
): number {
  return simplifyPolyline(points, maxTurnDegrees, closed).removed
}
