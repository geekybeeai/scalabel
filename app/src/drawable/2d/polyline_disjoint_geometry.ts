/**
 * Geometry for the disjoint tool: break a polyline at one of its own anchors.
 *
 * This is the counterpart to the endpoint merge. When two lines are joined —
 * by dragging one endpoint onto another, or by the batch auto-connect run at
 * project creation — the seam survives as an ordinary LINE anchor in the middle
 * of the merged run. Disjoint cuts exactly there, so the two halves are the
 * lines that went in, with every coordinate unchanged.
 *
 * Splitting AT an existing anchor is what makes that exact: no bezier is
 * subdivided and no vertex moves, unlike a cut at an arbitrary click point.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in node.
 * All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"

/** The two lines a disjoint produces. */
export interface DisjointHalves {
  /** the run up to and including the anchor */
  first: SimplePathPoint2DType[]
  /** the run from the anchor onwards */
  second: SimplePathPoint2DType[]
}

/**
 * Indices of the anchors a line can be broken at.
 *
 * Only interior LINE anchors qualify: the two ends are already free, and a
 * CURVE point is a bezier control handle rather than a point on the path.
 *
 * @param points the line's vertices
 */
export function disjointableAnchors(
  points: readonly SimplePathPoint2DType[]
): number[] {
  const out: number[] = []
  for (let i = 1; i < points.length - 1; i++) {
    if (points[i].pointType === PathPointType.LINE) {
      out.push(i)
    }
  }
  return out
}

/**
 * Find the interior anchor nearest a click.
 *
 * @param points the line's vertices
 * @param click the click position, in image px
 * @param click.x
 * @param radius the greatest distance that still counts as hitting an anchor
 * @param click.y
 */
export function findDisjointAnchor(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number
): { index: number; distance: number } | null {
  let best: { index: number; distance: number } | null = null
  for (const i of disjointableAnchors(points)) {
    const d = Math.hypot(points[i].x - click.x, points[i].y - click.y)
    if (d <= radius && (best === null || d < best.distance)) {
      best = { index: i, distance: d }
    }
  }
  return best
}

/**
 * Break a line into two at one of its anchors.
 *
 * The anchor is duplicated: it becomes the last point of the first half and the
 * first point of the second, so the two halves still meet exactly where they
 * did and either end can be dragged away or re-merged.
 *
 * Returns null when the split would leave a half with fewer than two points,
 * which happens only for an index that is not an interior anchor.
 *
 * @param points the line's vertices
 * @param index the anchor index to break at
 */
export function buildDisjointHalves(
  points: readonly SimplePathPoint2DType[],
  index: number
): DisjointHalves | null {
  if (
    index <= 0 ||
    index >= points.length - 1 ||
    points[index].pointType !== PathPointType.LINE
  ) {
    return null
  }
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })
  const first = points.slice(0, index + 1).map(copy)
  const second = points.slice(index).map(copy)
  if (first.length < 2 || second.length < 2) {
    return null
  }
  return { first, second }
}
