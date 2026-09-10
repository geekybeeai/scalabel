/**
 * Geometry for the arc tool: build an open circular arc as bezier segments.
 *
 * An arc is NOT a new label type. It is an ordinary polyline whose points are
 * LCCLCCL..., which is exactly what the existing JSONs already hold, so an arc
 * exports, re-imports, cuts and — crucially — endpoint-merges with other lines
 * with no special handling anywhere.
 *
 * Arcs are always OPEN. The sweep decides how much of the circle is drawn, so a
 * semicircle and an almost-full ring are the same construction with a different
 * sweep; the gap is simply wherever the arc stops.
 *
 * Accuracy: one cubic per 90 degrees of sweep is visually exact — the worst
 * deviation from a true circle is 0.05px on a 330-degree arc of radius 300.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in node.
 * All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"

/** A point in the image frame. */
export interface ArcPoint {
  /** x in image px */
  x: number
  /** y in image px */
  y: number
}

/** The circle a three-point gesture defines. */
export interface ArcCircle {
  /** centre x in image px */
  cx: number
  /** centre y in image px */
  cy: number
  /** radius in image px */
  radius: number
}

/**
 * Largest sweep, in radians, that one cubic bezier may cover.
 *
 * At 90 degrees a cubic tracks a circle to well under a pixel; letting one
 * segment stretch further is what makes naive circle-from-bezier code visibly
 * lumpy.
 */
const MAX_SEGMENT_SWEEP = Math.PI / 2

/**
 * Points closer together than this cannot define a circle reliably.
 *
 * Three clicks within a few pixels of each other are a mis-click, not an arc,
 * and would otherwise produce an enormous radius from floating-point noise.
 */
export const MIN_POINT_SEPARATION = 2

/**
 * Fit the circle passing through three points.
 *
 * Used by the three-point gesture: the centre of a painted ring is not visible
 * on an orthomosaic, but three points along the paint always are.
 *
 * Returns null when the points are collinear (or too close together), which has
 * no finite circle through it.
 *
 * @param a first point on the arc
 * @param b a point along the arc, between the ends
 * @param c last point on the arc
 */
export function circleThroughPoints(
  a: ArcPoint,
  b: ArcPoint,
  c: ArcPoint
): ArcCircle | null {
  if (
    Math.hypot(b.x - a.x, b.y - a.y) < MIN_POINT_SEPARATION ||
    Math.hypot(c.x - b.x, c.y - b.y) < MIN_POINT_SEPARATION ||
    Math.hypot(c.x - a.x, c.y - a.y) < MIN_POINT_SEPARATION
  ) {
    return null
  }
  // Twice the signed area of the triangle: zero exactly when collinear.
  const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
  if (Math.abs(d) < 1e-9) {
    return null
  }
  const sa = a.x * a.x + a.y * a.y
  const sb = b.x * b.x + b.y * b.y
  const sc = c.x * c.x + c.y * c.y
  const cx = (sa * (b.y - c.y) + sb * (c.y - a.y) + sc * (a.y - b.y)) / d
  const cy = (sa * (c.x - b.x) + sb * (a.x - c.x) + sc * (b.x - a.x)) / d
  return { cx, cy, radius: Math.hypot(a.x - cx, a.y - cy) }
}

/**
 * The sweep from `from` to `to` that passes through `via`.
 *
 * A start and end angle alone are ambiguous — the arc could go either way round
 * — so the middle click is what picks the direction and whether the result is
 * the minor or the major arc. That is what lets one gesture draw both a
 * semicircle and an almost-full ring.
 *
 * @param from angle of the first point, in radians
 * @param via angle of the middle point, in radians
 * @param to angle of the last point, in radians
 */
export function sweepThrough(from: number, via: number, to: number): number {
  const norm = (t: number): number => {
    let v = t % (2 * Math.PI)
    if (v < 0) {
      v += 2 * Math.PI
    }
    return v
  }
  const ccwToVia = norm(via - from)
  const ccwToEnd = norm(to - from)
  // Going counter-clockwise, the middle point is reached before the end only
  // when that is the direction the user actually traced.
  return ccwToVia < ccwToEnd ? ccwToEnd : ccwToEnd - 2 * Math.PI
}

/**
 * Build an open arc as a run of cubic bezier segments.
 *
 * The run is emitted as LCCLCCL... — anchors are LINE points and the two
 * controls of each segment are CURVE points, matching how every other curved
 * polyline in the editor is stored. Both ends are plain anchors, so either can
 * be dragged onto another line to merge.
 *
 * @param circle the circle the arc lies on
 * @param startAngle where the arc begins, in radians
 * @param sweep how far it turns, in radians; negative sweeps clockwise
 */
export function arcToBeziers(
  circle: ArcCircle,
  startAngle: number,
  sweep: number
): SimplePathPoint2DType[] {
  const { cx, cy, radius } = circle
  const segments = Math.max(1, Math.ceil(Math.abs(sweep) / MAX_SEGMENT_SWEEP))
  const step = sweep / segments
  // The control-point distance for a cubic spanning `step` radians. The
  // familiar 0.552 constant is just this expression evaluated at 90 degrees;
  // using the general form is what keeps a semicircle and a 330-degree arc
  // equally exact.
  const k = (4 / 3) * Math.tan(step / 4)

  const points: SimplePathPoint2DType[] = []
  for (let i = 0; i < segments; i++) {
    const s = startAngle + i * step
    const e = s + step
    const cosS = Math.cos(s)
    const sinS = Math.sin(s)
    const cosE = Math.cos(e)
    const sinE = Math.sin(e)
    const p0 = { x: cx + radius * cosS, y: cy + radius * sinS }
    const p3 = { x: cx + radius * cosE, y: cy + radius * sinE }
    if (i === 0) {
      points.push({ x: p0.x, y: p0.y, pointType: PathPointType.LINE })
    }
    points.push(
      {
        x: p0.x - k * radius * sinS,
        y: p0.y + k * radius * cosS,
        pointType: PathPointType.CURVE
      },
      {
        x: p3.x + k * radius * sinE,
        y: p3.y - k * radius * cosE,
        pointType: PathPointType.CURVE
      },
      { x: p3.x, y: p3.y, pointType: PathPointType.LINE }
    )
  }
  return points
}

/**
 * Build the arc passing through three clicked points.
 *
 * Returns null when the three points do not define an arc — collinear clicks or
 * a mis-click with two points on top of each other.
 *
 * @param a first click: where the arc starts
 * @param b second click: any point along the arc
 * @param c third click: where the arc ends
 */
export function arcThroughPoints(
  a: ArcPoint,
  b: ArcPoint,
  c: ArcPoint
): SimplePathPoint2DType[] | null {
  const circle = circleThroughPoints(a, b, c)
  if (circle === null) {
    return null
  }
  const angle = (p: ArcPoint): number =>
    Math.atan2(p.y - circle.cy, p.x - circle.cx)
  const start = angle(a)
  const sweep = sweepThrough(start, angle(b), angle(c))
  return arcToBeziers(circle, start, sweep)
}

/**
 * Tension of the Catmull-Rom spline used for a multi-point curve.
 *
 * 1/6 is the standard conversion from Catmull-Rom to cubic bezier control
 * points: it makes the curve pass exactly through every clicked point with
 * continuous tangents, which is what "trace these points smoothly" means.
 */
const SPLINE_TENSION = 1 / 6

/**
 * Build a smooth open curve through any number of points.
 *
 * Three points have exactly one circle through them, but four or more generally
 * do not lie on a circle at all, so a curve through N points is a spline rather
 * than an arc. Each span becomes one cubic whose controls come from the
 * neighbouring points, giving a curve that passes through every click with no
 * corners at the joins.
 *
 * The output is the same LCCLCCL... polyline an arc produces, so it merges,
 * cuts and exports identically.
 *
 * @param points the clicked points, in order along the curve
 */
export function splineThroughPoints(
  points: readonly ArcPoint[]
): SimplePathPoint2DType[] | null {
  if (points.length < 2) {
    return null
  }
  // Drop repeated clicks: a zero-length span has no tangent and would emit a
  // degenerate segment.
  const pts: ArcPoint[] = []
  for (const p of points) {
    const last = pts[pts.length - 1]
    if (
      last === undefined ||
      Math.hypot(p.x - last.x, p.y - last.y) >= MIN_POINT_SEPARATION
    ) {
      pts.push(p)
    }
  }
  if (pts.length < 2) {
    return null
  }

  const out: SimplePathPoint2DType[] = [
    { x: pts[0].x, y: pts[0].y, pointType: PathPointType.LINE }
  ]
  for (let i = 0; i + 1 < pts.length; i++) {
    const p0 = pts[i - 1] ?? pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] ?? pts[i + 1]
    out.push(
      {
        x: p1.x + (p2.x - p0.x) * SPLINE_TENSION,
        y: p1.y + (p2.y - p0.y) * SPLINE_TENSION,
        pointType: PathPointType.CURVE
      },
      {
        x: p2.x - (p3.x - p1.x) * SPLINE_TENSION,
        y: p2.y - (p3.y - p1.y) * SPLINE_TENSION,
        pointType: PathPointType.CURVE
      },
      { x: p2.x, y: p2.y, pointType: PathPointType.LINE }
    )
  }
  return out
}

/**
 * Build a curve through however many points were clicked.
 *
 * Three points get the exact circular arc, since that is what a ring-shaped
 * marking actually is. Two points get a straight span, and four or more get a
 * smooth spline, because points beyond the third generally do not lie on any
 * one circle.
 *
 * @param points the clicked points, in order along the curve
 */
export function curveThroughPoints(
  points: readonly ArcPoint[]
): SimplePathPoint2DType[] | null {
  if (points.length === 3) {
    const arc = arcThroughPoints(points[0], points[1], points[2])
    if (arc !== null) {
      return arc
    }
    // Collinear: fall through to the spline, which draws them as a line.
  }
  return splineThroughPoints(points)
}
