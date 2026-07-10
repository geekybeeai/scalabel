import { IdType } from "../../types/state"

/** A 2D point in the image frame. */
export interface Pt {
  x: number
  y: number
}

/** A polyline/polygon reduced to its vertices for lasso hit-testing. */
export interface LassoLine {
  /** the label id */
  id: IdType
  /** the line's vertices in the image frame (order matters) */
  pts: Pt[]
  /** whether the line's own path closes (polygon) */
  closed: boolean
}

/**
 * Point-in-polygon test by ray casting (even-odd rule). A point exactly on an
 * edge is ambiguous, which is harmless here: a boundary-crossing line is also
 * caught by the edge test in lineHitsLasso.
 *
 * @param pt the query point
 * @param polygon the polygon vertices (implicitly closed)
 */
export function pointInPolygon(pt: Pt, polygon: Pt[]): boolean {
  let inside = false
  const n = polygon.length
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const a = polygon[i]
    const b = polygon[j]
    const crosses =
      a.y > pt.y !== b.y > pt.y &&
      pt.x < ((b.x - a.x) * (pt.y - a.y)) / (b.y - a.y) + a.x
    if (crosses) {
      inside = !inside
    }
  }
  return inside
}

/**
 * Orientation of the ordered triplet (p, q, r):
 * 0 collinear, 1 clockwise, 2 counter-clockwise.
 *
 * @param p first point
 * @param q second point
 * @param r third point
 */
function orientation(p: Pt, q: Pt, r: Pt): number {
  const val = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
  if (val === 0) {
    return 0
  }
  return val > 0 ? 1 : 2
}

/**
 * Whether q lies on segment pr, assuming p, q, r are collinear.
 *
 * @param p segment start
 * @param q query point
 * @param r segment end
 */
function onSegment(p: Pt, q: Pt, r: Pt): boolean {
  return (
    q.x <= Math.max(p.x, r.x) &&
    q.x >= Math.min(p.x, r.x) &&
    q.y <= Math.max(p.y, r.y) &&
    q.y >= Math.min(p.y, r.y)
  )
}

/**
 * Whether segment ab intersects segment cd (including collinear touching).
 *
 * @param a first endpoint of segment 1
 * @param b second endpoint of segment 1
 * @param c first endpoint of segment 2
 * @param d second endpoint of segment 2
 */
export function segmentsIntersect(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const o1 = orientation(a, b, c)
  const o2 = orientation(a, b, d)
  const o3 = orientation(c, d, a)
  const o4 = orientation(c, d, b)
  if (o1 !== o2 && o3 !== o4) {
    return true
  }
  if (o1 === 0 && onSegment(a, c, b)) {
    return true
  }
  if (o2 === 0 && onSegment(a, d, b)) {
    return true
  }
  if (o3 === 0 && onSegment(c, a, d)) {
    return true
  }
  if (o4 === 0 && onSegment(c, b, d)) {
    return true
  }
  return false
}

/**
 * Crossing test: true if ANY line vertex is inside the lasso OR any line
 * segment crosses any lasso edge. Covers both "fully enclosed" and "crossing".
 *
 * @param line the candidate line
 * @param lasso the lasso polygon vertices (implicitly closed)
 */
export function lineHitsLasso(line: LassoLine, lasso: Pt[]): boolean {
  if (lasso.length < 3) {
    return false
  }
  for (const p of line.pts) {
    if (pointInPolygon(p, lasso)) {
      return true
    }
  }
  const segCount = line.closed ? line.pts.length : line.pts.length - 1
  for (let i = 0; i < segCount; i++) {
    const a = line.pts[i]
    const b = line.pts[(i + 1) % line.pts.length]
    for (let j = 0; j < lasso.length; j++) {
      const c = lasso[j]
      const d = lasso[(j + 1) % lasso.length]
      if (segmentsIntersect(a, b, c, d)) {
        return true
      }
    }
  }
  return false
}

/**
 * The ids of every line inside or crossing the lasso.
 *
 * @param lines the candidate lines
 * @param lasso the lasso polygon vertices
 */
export function findLassoHits(lines: LassoLine[], lasso: Pt[]): IdType[] {
  const hits: IdType[] = []
  for (const line of lines) {
    if (lineHitsLasso(line, lasso)) {
      hits.push(line.id)
    }
  }
  return hits
}
