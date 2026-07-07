import { PathPointType, SimplePathPoint2DType } from "../../types/state"

/** A validated location to cut a polyline. */
export interface CutSite {
  /** Segment start index: the cut lies on points[i] -> points[i + 1] */
  segmentIndex: number
  /** Cut coordinate (original image frame) */
  point: { x: number; y: number }
  /** When the cut snapped to an existing interior vertex, its index */
  snappedVertexIndex: number | null
  /** Click-to-polyline distance (image px), comparable across polylines */
  distance: number
}

/** Result of searching one polyline for a cut site. */
export type CutSiteResult =
  | { kind: "site"; site: CutSite }
  | { kind: "curve"; distance: number }
  | { kind: "near-endpoint"; distance: number }
  | { kind: "miss" }

/** Both halves of a cut polyline, as plain (id-less) path points. */
export interface CutHalves {
  /** points[0..i] plus the cut point (unless vertex-snapped) */
  first: SimplePathPoint2DType[]
  /** the cut point (unless vertex-snapped) plus points[i + 1..] */
  second: SimplePathPoint2DType[]
}

interface SegmentProjection {
  /** distance from the query point to the segment */
  dist: number
  /** closest point x */
  x: number
  /** closest point y */
  y: number
}

/**
 * Project a point onto a segment, clamped to the segment's extent.
 *
 * @param px query point x
 * @param py query point y
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
): SegmentProjection {
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
  return { dist: Math.hypot(px - x, py - y), x, y }
}

/**
 * Find where a click would cut a polyline.
 *
 * Measures the click against EVERY span (bezier control spans included, so a
 * click that genuinely lands on a curved part is reported as "curve" instead
 * of cutting a farther straight segment), takes the nearest span, and
 * validates it: within `radius`, both span ends plain LINE vertices, and not
 * within `snapRadius` of the polyline's first/last vertex (which would create
 * a zero-length stub). A cut within `snapRadius` of an interior vertex snaps
 * exactly to that vertex.
 *
 * All inputs are in original-image coordinates.
 *
 * @param points the polyline's stored vertices (never contains MID points)
 * @param click the click position
 * @param click.x click x (image px)
 * @param click.y click y (image px)
 * @param radius max click-to-line distance for a cut (image px)
 * @param snapRadius vertex snap / endpoint-guard distance (image px)
 */
export function findCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number
): CutSiteResult {
  if (points.length < 2) {
    return { kind: "miss" }
  }
  let bestIndex = -1
  let best: SegmentProjection | null = null
  for (let i = 0; i < points.length - 1; i++) {
    const proj = projectOntoSegment(
      click.x,
      click.y,
      points[i].x,
      points[i].y,
      points[i + 1].x,
      points[i + 1].y
    )
    if (best === null || proj.dist < best.dist) {
      best = proj
      bestIndex = i
    }
  }
  if (best === null || best.dist > radius) {
    return { kind: "miss" }
  }
  // A span is only cuttable when both ends are plain LINE vertices; CURVE
  // (or any other) point types mark bezier control spans.
  if (
    points[bestIndex].pointType !== PathPointType.LINE ||
    points[bestIndex + 1].pointType !== PathPointType.LINE
  ) {
    return { kind: "curve", distance: best.dist }
  }
  // Snap to a nearby vertex so cuts never create hair-thin sliver segments.
  const dStart = Math.hypot(
    best.x - points[bestIndex].x,
    best.y - points[bestIndex].y
  )
  const dEnd = Math.hypot(
    best.x - points[bestIndex + 1].x,
    best.y - points[bestIndex + 1].y
  )
  let snapped: number | null = null
  if (dStart <= snapRadius && dStart <= dEnd) {
    snapped = bestIndex
  } else if (dEnd <= snapRadius) {
    snapped = bestIndex + 1
  }
  if (snapped !== null && (snapped === 0 || snapped === points.length - 1)) {
    return { kind: "near-endpoint", distance: best.dist }
  }
  const point =
    snapped !== null
      ? { x: points[snapped].x, y: points[snapped].y }
      : { x: best.x, y: best.y }
  return {
    kind: "site",
    site: {
      segmentIndex: bestIndex,
      point,
      snappedVertexIndex: snapped,
      distance: best.dist
    }
  }
}
