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
  | { kind: "near-endpoint"; distance: number; endpointIndex: number }
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
    return {
      kind: "near-endpoint",
      distance: best.dist,
      endpointIndex: snapped
    }
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

/**
 * Build the two halves of a cut polyline.
 *
 * Mid-segment cut: the first half ends with a new vertex at the cut point and
 * the second half starts with its own vertex at the same coordinate
 * (coincident, no gap). Vertex-snapped cut: no new coordinate is introduced
 * within a half — the halves share only the snapped vertex's coordinate.
 * Returned points are fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param site a "site" result from findCutSite for these points
 */
export function buildCutHalves(
  points: readonly SimplePathPoint2DType[],
  site: CutSite
): CutHalves {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })
  if (site.snappedVertexIndex !== null) {
    const j = site.snappedVertexIndex
    return {
      first: points.slice(0, j + 1).map(copy),
      second: points.slice(j).map(copy)
    }
  }
  const i = site.segmentIndex
  const cutPoint: SimplePathPoint2DType = {
    x: site.point.x,
    y: site.point.y,
    pointType: PathPointType.LINE
  }
  return {
    first: [...points.slice(0, i + 1).map(copy), { ...cutPoint }],
    second: [{ ...cutPoint }, ...points.slice(i + 1).map(copy)]
  }
}

/**
 * One of the two user picks for a segment delete: an interior cut site or a
 * pick near the line's first/last vertex (an end trim).
 */
export type DeleteSitePick =
  | { kind: "interior"; site: CutSite }
  | { kind: "end"; endpointIndex: number }

/** Result of ordering/validating a pair of delete picks. */
export type NormalizedDeletePicks =
  | { kind: "ok"; first: DeleteSitePick; second: DeleteSitePick }
  | { kind: "too-close" }

/**
 * Resolve a pick to its coordinate on the polyline (image frame).
 *
 * @param points the polyline's stored vertices
 * @param pick the pick to resolve
 */
export function resolvePickPoint(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): { x: number; y: number } {
  if (pick.kind === "end") {
    const p = points[pick.endpointIndex]
    return { x: p.x, y: p.y }
  }
  return { x: pick.site.point.x, y: pick.site.point.y }
}

/**
 * Monotonic position of a pick along the polyline, for ordering the two
 * delete picks: start end < vertex/segment positions < last end.
 *
 * @param points the polyline's stored vertices
 * @param pick the pick to position
 */
export function sitePositionKey(
  points: readonly SimplePathPoint2DType[],
  pick: DeleteSitePick
): number {
  if (pick.kind === "end") {
    return pick.endpointIndex === 0 ? -1 : points.length
  }
  if (pick.site.snappedVertexIndex !== null) {
    return pick.site.snappedVertexIndex
  }
  const i = pick.site.segmentIndex
  const a = points[i]
  const b = points[i + 1]
  const segLen = Math.hypot(b.x - a.x, b.y - a.y)
  const t =
    segLen > 0
      ? Math.hypot(pick.site.point.x - a.x, pick.site.point.y - a.y) / segLen
      : 0
  return i + t
}

/**
 * Validate and order the two picks of a segment delete. Picks whose resolved
 * coordinates are within `snapRadius` of each other (including two trims at
 * the same end) are rejected as "too-close"; otherwise the picks are returned
 * ordered by position along the line, so the caller never cares which one
 * the user clicked first.
 *
 * @param points the polyline's stored vertices
 * @param a one pick
 * @param b the other pick
 * @param snapRadius minimum separation (image px)
 */
export function normalizeDeletePicks(
  points: readonly SimplePathPoint2DType[],
  a: DeleteSitePick,
  b: DeleteSitePick,
  snapRadius: number
): NormalizedDeletePicks {
  const pa = resolvePickPoint(points, a)
  const pb = resolvePickPoint(points, b)
  if (Math.hypot(pa.x - pb.x, pa.y - pb.y) <= snapRadius) {
    return { kind: "too-close" }
  }
  return sitePositionKey(points, a) <= sitePositionKey(points, b)
    ? { kind: "ok", first: a, second: b }
    : { kind: "ok", first: b, second: a }
}
