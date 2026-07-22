import { PathPointType, SimplePathPoint2DType } from "../../types/state"

import { curveGroupIndices } from "./curve_groups"

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
  /** Present for a mid-curve cut: the bezier group start (its a0 index)
   * and the split parameter for splitCubicBezier. */
  curveSplit?: { groupStart: number; t: number }
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

/** A plain 2D coordinate. */
export interface Point2D {
  x: number
  y: number
}

/**
 * Linear interpolation between two points.
 *
 * @param a start point
 * @param b end point
 * @param t interpolation parameter in [0, 1]
 */
function lerp(a: Point2D, b: Point2D, t: number): Point2D {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }
}

/**
 * Evaluate a cubic bezier at t (Bernstein form).
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t curve parameter in [0, 1]
 */
function evalCubic(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  t: number
): Point2D {
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

/** Result of splitting a cubic bezier at a parameter t. */
export interface CubicSplit {
  /** left half's control points (anchors: original a0 .. point) */
  left: { c1: Point2D; c2: Point2D }
  /** the on-curve split point */
  point: Point2D
  /** right half's control points (anchors: point .. original a1) */
  right: { c1: Point2D; c2: Point2D }
}

/**
 * Split a cubic bezier at parameter t (de Casteljau). The two halves
 * jointly trace exactly the original curve.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param t split parameter in [0, 1]
 */
export function splitCubicBezier(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  t: number
): CubicSplit {
  const p01 = lerp(a0, c1, t)
  const p12 = lerp(c1, c2, t)
  const p23 = lerp(c2, a1, t)
  const p012 = lerp(p01, p12, t)
  const p123 = lerp(p12, p23, t)
  const point = lerp(p012, p123, t)
  return {
    left: { c1: p01, c2: p012 },
    point,
    right: { c1: p123, c2: p23 }
  }
}

/** Nearest point on a cubic bezier to a query point. */
export interface NearestOnCubic {
  /** curve parameter of the nearest point */
  t: number
  /** the nearest point on the curve */
  point: Point2D
  /** distance from the query point (image px) */
  distance: number
}

/** Coarse samples for the nearest-t search. */
const NEAREST_T_SAMPLES = 32
/** Ternary-search refinement iterations around the best sample. */
const NEAREST_T_REFINEMENTS = 24

/**
 * Find the point on a cubic bezier nearest to a click: coarse sampling
 * followed by ternary-search refinement in the bracket around the best
 * sample. Sub-pixel accurate at annotation scales.
 *
 * @param a0 start anchor
 * @param c1 first control point
 * @param c2 second control point
 * @param a1 end anchor
 * @param click the query point
 */
export function nearestTOnCubic(
  a0: Point2D,
  c1: Point2D,
  c2: Point2D,
  a1: Point2D,
  click: Point2D
): NearestOnCubic {
  let bestT = 0
  let bestD = Number.POSITIVE_INFINITY
  for (let i = 0; i <= NEAREST_T_SAMPLES; i++) {
    const t = i / NEAREST_T_SAMPLES
    const p = evalCubic(a0, c1, c2, a1, t)
    const d = Math.hypot(click.x - p.x, click.y - p.y)
    if (d < bestD) {
      bestD = d
      bestT = t
    }
  }
  let lo = Math.max(0, bestT - 1 / NEAREST_T_SAMPLES)
  let hi = Math.min(1, bestT + 1 / NEAREST_T_SAMPLES)
  for (let i = 0; i < NEAREST_T_REFINEMENTS; i++) {
    const m1 = lo + (hi - lo) / 3
    const m2 = hi - (hi - lo) / 3
    const p1 = evalCubic(a0, c1, c2, a1, m1)
    const p2 = evalCubic(a0, c1, c2, a1, m2)
    const d1 = Math.hypot(click.x - p1.x, click.y - p1.y)
    const d2 = Math.hypot(click.x - p2.x, click.y - p2.y)
    if (d1 <= d2) {
      hi = m2
    } else {
      lo = m1
    }
  }
  const t = (lo + hi) / 2
  const point = evalCubic(a0, c1, c2, a1, t)
  return {
    t,
    point,
    distance: Math.hypot(click.x - point.x, click.y - point.y)
  }
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
/** Options for findCutSite. */
export interface FindCutSiteOptions {
  /** Resolve clicks on curved spans to bezier split sites (the cut tool).
   * Default false: curved spans reject as "curve" (delete-segment). */
  splitCurves?: boolean
}

export function findCutSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  options?: FindCutSiteOptions
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
  if (best === null) {
    return { kind: "miss" }
  }
  // A span is only directly cuttable when both ends are plain LINE
  // vertices; CURVE (or any other) point types mark bezier control spans.
  const curveSpan =
    points[bestIndex].pointType !== PathPointType.LINE ||
    points[bestIndex + 1].pointType !== PathPointType.LINE
  if (curveSpan && options?.splitCurves === true) {
    // The bezier itself, not its control polygon, is the click target —
    // findCurveSite re-checks radius/snap against true curve distance.
    return findCurveSite(
      points,
      click,
      radius,
      snapRadius,
      bestIndex,
      best.dist
    )
  }
  if (best.dist > radius) {
    return { kind: "miss" }
  }
  if (curveSpan) {
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
 * Resolve a click on a bezier control span to a cut site on the actual
 * curve. Guards mirror the straight-span rules but measure true distance
 * to the cubic: farther than `radius` → miss; nearest curve point within
 * `snapRadius` of a group anchor → snap to that anchor (or the
 * near-endpoint rejection at the line's first/last vertex); otherwise a
 * mid-curve site carrying `curveSplit` for buildCutHalves.
 *
 * @param points the polyline's stored vertices
 * @param click the click position
 * @param radius max click-to-curve distance (image px)
 * @param snapRadius anchor snap / endpoint-guard distance (image px)
 * @param spanIndex the nearest control-polygon span's start index
 * @param spanDist the click's distance to that span (for the fallback)
 */
function findCurveSite(
  points: readonly SimplePathPoint2DType[],
  click: { x: number; y: number },
  radius: number,
  snapRadius: number,
  spanIndex: number,
  spanDist: number
): CutSiteResult {
  const types = points.map((p) => p.pointType)
  const group = curveGroupIndices(types).find(
    (g) => spanIndex >= g[0] && spanIndex < g[3]
  )
  if (group === undefined) {
    // Malformed curve data (stray control points): keep the old rejection.
    return { kind: "curve", distance: spanDist }
  }
  const [a0, i1, i2, a1] = group
  const near = nearestTOnCubic(
    points[a0],
    points[i1],
    points[i2],
    points[a1],
    click
  )
  if (near.distance > radius) {
    return { kind: "miss" }
  }
  const dA0 = Math.hypot(
    near.point.x - points[a0].x,
    near.point.y - points[a0].y
  )
  const dA1 = Math.hypot(
    near.point.x - points[a1].x,
    near.point.y - points[a1].y
  )
  let snapped: number | null = null
  if (dA0 <= snapRadius && dA0 <= dA1) {
    snapped = a0
  } else if (dA1 <= snapRadius) {
    snapped = a1
  }
  if (snapped !== null && (snapped === 0 || snapped === points.length - 1)) {
    return {
      kind: "near-endpoint",
      distance: near.distance,
      endpointIndex: snapped
    }
  }
  if (snapped !== null) {
    return {
      kind: "site",
      site: {
        segmentIndex: a0,
        point: { x: points[snapped].x, y: points[snapped].y },
        snappedVertexIndex: snapped,
        distance: near.distance
      }
    }
  }
  return {
    kind: "site",
    site: {
      segmentIndex: a0,
      point: near.point,
      snappedVertexIndex: null,
      distance: near.distance,
      curveSplit: { groupStart: a0, t: near.t }
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
  if (site.curveSplit !== undefined) {
    const { groupStart, t } = site.curveSplit
    const split = splitCubicBezier(
      points[groupStart],
      points[groupStart + 1],
      points[groupStart + 2],
      points[groupStart + 3],
      t
    )
    const curvePoint = (p: Point2D): SimplePathPoint2DType => ({
      x: p.x,
      y: p.y,
      pointType: PathPointType.CURVE
    })
    const splitPoint: SimplePathPoint2DType = {
      x: split.point.x,
      y: split.point.y,
      pointType: PathPointType.LINE
    }
    return {
      first: [
        ...points.slice(0, groupStart + 1).map(copy),
        curvePoint(split.left.c1),
        curvePoint(split.left.c2),
        { ...splitPoint }
      ],
      second: [
        { ...splitPoint },
        curvePoint(split.right.c1),
        curvePoint(split.right.c2),
        ...points.slice(groupStart + 3).map(copy)
      ]
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

/** The pieces of a segment delete: optional survivors and the removed path. */
export interface SegmentDeletePieces {
  /** survivor before the first pick (absent on a start trim / whole-line) */
  left?: SimplePathPoint2DType[]
  /** survivor after the second pick (absent on an end trim / whole-line) */
  right?: SimplePathPoint2DType[]
  /** the removed path, including the pick coordinates (for the preview) */
  doomed: SimplePathPoint2DType[]
}

/**
 * Build the survivors and the doomed piece of a segment delete.
 *
 * `first`/`second` MUST already be ordered by normalizeDeletePicks. Interior
 * picks split exactly like the cut tool (projection point becomes a new LINE
 * vertex in both the survivor and the doomed piece; a vertex-snapped pick
 * shares the snapped vertex's coordinate without duplicating it within any
 * piece). End picks produce no survivor on their side. Returned points are
 * fresh copies; the input is never mutated.
 *
 * @param points the polyline's stored vertices
 * @param first the earlier pick along the line
 * @param second the later pick along the line
 */
export function buildSegmentDeletePieces(
  points: readonly SimplePathPoint2DType[],
  first: DeleteSitePick,
  second: DeleteSitePick
): SegmentDeletePieces {
  const copy = (p: SimplePathPoint2DType): SimplePathPoint2DType => ({
    x: p.x,
    y: p.y,
    pointType: p.pointType
  })

  let left: SimplePathPoint2DType[] | undefined
  let head: SimplePathPoint2DType[]
  let from: number
  if (first.kind === "end") {
    left = undefined
    head = []
    from = 0
  } else if (first.site.snappedVertexIndex !== null) {
    const s = first.site.snappedVertexIndex
    left = points.slice(0, s + 1).map(copy)
    head = [copy(points[s])]
    from = s + 1
  } else {
    const i = first.site.segmentIndex
    const p1: SimplePathPoint2DType = {
      x: first.site.point.x,
      y: first.site.point.y,
      pointType: PathPointType.LINE
    }
    left = [...points.slice(0, i + 1).map(copy), { ...p1 }]
    head = [{ ...p1 }]
    from = i + 1
  }

  let right: SimplePathPoint2DType[] | undefined
  let tail: SimplePathPoint2DType[]
  let to: number
  if (second.kind === "end") {
    right = undefined
    tail = []
    to = points.length - 1
  } else if (second.site.snappedVertexIndex !== null) {
    const u = second.site.snappedVertexIndex
    right = points.slice(u).map(copy)
    tail = []
    to = u
  } else {
    const j = second.site.segmentIndex
    const p2: SimplePathPoint2DType = {
      x: second.site.point.x,
      y: second.site.point.y,
      pointType: PathPointType.LINE
    }
    right = [{ ...p2 }, ...points.slice(j + 1).map(copy)]
    tail = [{ ...p2 }]
    to = j
  }

  const doomed = [...head, ...points.slice(from, to + 1).map(copy), ...tail]
  return { left, right, doomed }
}
