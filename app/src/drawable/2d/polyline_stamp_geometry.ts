/**
 * Geometry for the stamp-along-path tool: repeat a small mark at regular
 * intervals along a traced line.
 *
 * Lane markings are overwhelmingly repetitive — on a sample image 157 of 254
 * labels were short marks, all of them within 25px of a line the annotator had
 * already traced, spaced about every 99px. Drawing them one at a time is the
 * bulk of the work; generating them from the traced path removes it.
 *
 * Marks are placed by ARC LENGTH, so they follow curves rather than drifting
 * off a bend.
 *
 * Two things measured from real annotations shape the defaults.
 *
 * Marks are NOT parallel to their parent line: across 152 samples not one was
 * within 10 degrees of parallel, and the median was 53 degrees off, so the
 * angle is expressed RELATIVE to the local path direction.
 *
 * Chevrons are not reliably symmetric: the four found in real data had arm
 * ratios of 0.75-0.87, so the two arms are configured independently.
 *
 * Pure geometry — no Session or DOM imports, so it is unit-testable in a node
 * environment. All coordinates are in the original image frame.
 */

import { PathPointType, SimplePathPoint2DType } from "../../types/state"
import { curveGroupIndices } from "./curve_groups"

/** Shape stamped at each position along the path. */
export enum StampTemplate {
  /** a straight two-point tick */
  DASH = "dash",
  /** a three-point V */
  CHEVRON = "chevron",
  /** an arbitrary shape captured from a mark the user already drew */
  CUSTOM = "custom"
}

/**
 * A mark shape captured from an existing label.
 *
 * The built-in DASH and CHEVRON cover the two commonest shapes, but real data
 * has plenty of others: across this batch 422 marks have three vertices and 40
 * have four to six. Rather than guess at more templates, a custom one is
 * captured from a mark the user has already drawn.
 *
 * Points are stored NORMALISED: centred on the mark's midpoint and rotated so
 * its dominant axis lies along +x. Stamping then rotates them to the local path
 * direction, so a captured shape follows curves the same way a dash does.
 */
export interface CustomTemplate {
  /** what to call it in the picker */
  name: string
  /** normalised points, centred on the origin with the long axis along +x */
  points: SimplePathPoint2DType[]
}

/** How to stamp marks along a path. */
export interface StampOptions {
  /** which shape to stamp */
  template: StampTemplate
  /** distance between consecutive marks, in image px */
  period: number
  /** length of a DASH, in image px */
  length: number
  /** mark angle relative to the local path direction, in degrees */
  angle: number
  /** CHEVRON: length of the first arm, in image px */
  armA: number
  /** CHEVRON: length of the second arm, in image px */
  armB: number
  /** CHEVRON: angle between the arms, in degrees */
  apex: number
  /** skip this much path at each end before stamping, in image px */
  margin: number
  /**
   * Extra path skipped at the START only, in image px.
   *
   * Margin trims both ends together, so it cannot line the first mark up with
   * the paint without also cutting the run short at the far end. This shifts
   * only where the marks begin, which is what aligning a run to existing paint
   * actually needs.
   */
  start: number
  /**
   * Perpendicular offset from the guide line, in image px.
   *
   * 0 centres each mark on the line. Negative shifts to the left of the
   * direction of travel, positive to the right — real markings sit on one side
   * as often as they straddle the line, and which side varies per line.
   */
  offset: number
  /** the shape used when template is CUSTOM */
  custom?: CustomTemplate
  /** scale applied to a CUSTOM shape, 1 = as captured */
  scale: number
  /**
   * When false, marks are placed only where the user clicks, not at a fixed
   * period. Real paint is rarely evenly spaced — it breaks at intersections,
   * driveways and turn pockets — so an even run is a starting point, not an
   * answer.
   */
  evenSpacing: boolean
  /**
   * Arc-length positions along the guide, in image px, used when evenSpacing
   * is false. Stored as distances rather than points so the marks stay put if
   * the guide is reshaped.
   */
  positions: number[]
  /**
   * Category index for the marks, or null to use whatever is selected in the
   * sidebar. Marks are usually a different class from the line they run along
   * (dashes beside a curb edge, say), so relying on the sidebar selection means
   * changing it before every stamp.
   */
  category: number | null
}

/**
 * Defaults measured from real annotations on image 208023.
 *
 * period/length come from 152 dash marks (median spacing 99px, median length
 * 38px); angle from their median 53-degree offset to the parent line; the
 * chevron arms and apex from the four well-formed chevrons found there.
 */
export const DEFAULT_STAMP_OPTIONS: StampOptions = {
  template: StampTemplate.DASH,
  period: 99,
  length: 38,
  angle: 53,
  armA: 40,
  armB: 31,
  apex: 60,
  margin: 20,
  start: 0,
  offset: 0,
  scale: 1,
  evenSpacing: true,
  positions: [],
  category: null
}

/** A point on a path, with the direction of travel there. */
interface PathSample {
  /** x in image px */
  x: number
  /** y in image px */
  y: number
  /** unit direction x */
  ux: number
  /** unit direction y */
  uy: number
}

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
  p0: SimplePathPoint2DType,
  p1: SimplePathPoint2DType,
  p2: SimplePathPoint2DType,
  p3: SimplePathPoint2DType,
  t: number
): { x: number; y: number } {
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
 * How finely each bezier is subdivided when flattening a guide path.
 *
 * 16 chords keeps the flattened path within a fraction of a pixel of the drawn
 * curve at annotation scale, which is well below what the offset needs.
 */
const FLATTEN_STEPS = 16

/**
 * Flatten a guide path so curves become dense straight chords.
 *
 * Stored points mix LINE anchors with the CURVE control points of any bezier.
 * Walking them directly treats a control point as a vertex, which puts marks
 * off the drawn line and — because the offset is perpendicular to the local
 * chord — pushes them further astray the sharper the bend. Flattening first
 * means both the position and the perpendicular follow the curve the user sees.
 *
 * @param points the guide's stored vertices
 * @param closed whether the guide is a closed ring
 */
export function flattenPath(
  points: readonly SimplePathPoint2DType[],
  closed: boolean = false
): SimplePathPoint2DType[] {
  const groups = curveGroupIndices(
    points.map((p) => p.pointType),
    closed
  )
  if (groups.length === 0) {
    return points.map((p) => ({ x: p.x, y: p.y, pointType: p.pointType }))
  }
  // Where each bezier group starts, so the walk can substitute its curve.
  const startsAt = new Map<number, number[]>()
  const inside = new Set<number>()
  for (const group of groups) {
    startsAt.set(group[0], group)
    inside.add(group[1])
    inside.add(group[2])
  }

  const out: SimplePathPoint2DType[] = []
  const push = (x: number, y: number): void => {
    const last = out[out.length - 1]
    if (last === undefined || last.x !== x || last.y !== y) {
      out.push({ x, y, pointType: PathPointType.LINE })
    }
  }

  for (let i = 0; i < points.length; i++) {
    if (inside.has(i)) {
      continue
    }
    const group = startsAt.get(i)
    if (group === undefined) {
      push(points[i].x, points[i].y)
      continue
    }
    const [ai, c1i, c2i, bi] = group
    for (let step = 0; step <= FLATTEN_STEPS; step++) {
      const point = cubicAt(
        points[ai],
        points[c1i],
        points[c2i],
        points[bi],
        step / FLATTEN_STEPS
      )
      push(point.x, point.y)
    }
  }
  return out
}

/**
 * Walk a polyline by arc length, sampling position and direction.
 *
 * Control points are included in the walk: they pull the sampled path toward
 * the curve, which is closer to the drawn shape than ignoring them would be.
 *
 * @param points the path's vertices
 * @param period distance between samples, in image px
 * @param margin path length skipped at each end, in image px
 * @param start extra path skipped at the start only, in image px
 */
export function samplePath(
  points: readonly SimplePathPoint2DType[],
  period: number,
  margin: number,
  start: number = 0
): PathSample[] {
  const samples: PathSample[] = []
  if (points.length < 2 || period <= 0) {
    return samples
  }

  // Cumulative length of each span, so a distance maps to a position.
  const spans: Array<{ length: number; index: number }> = []
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    const length = Math.hypot(
      points[i + 1].x - points[i].x,
      points[i + 1].y - points[i].y
    )
    spans.push({ length, index: i })
    total += length
  }
  if (total <= 2 * margin) {
    return samples
  }

  // The start offset moves only the first mark; the run still ends at the far
  // margin, so aligning the start does not shorten the tail.
  const from = margin + Math.max(0, start)
  for (let d = from; d <= total - margin; d += period) {
    let remaining = d
    for (const span of spans) {
      if (remaining <= span.length || span === spans[spans.length - 1]) {
        const a = points[span.index]
        const b = points[span.index + 1]
        const t = span.length > 0 ? remaining / span.length : 0
        const dx = b.x - a.x
        const dy = b.y - a.y
        const norm = Math.hypot(dx, dy)
        if (norm === 0) {
          break
        }
        samples.push({
          x: a.x + dx * t,
          y: a.y + dy * t,
          ux: dx / norm,
          uy: dy / norm
        })
        break
      }
      remaining -= span.length
    }
  }

  return samples
}

/**
 * Total arc length of a path.
 *
 * @param points the path's vertices
 */
export function pathLength(points: readonly SimplePathPoint2DType[]): number {
  // Flatten first, so a curved guide's length is its drawn length rather than
  // the control polygon's — the same measure stampAlongPath walks.
  points = flattenPath(points)
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    total += Math.hypot(
      points[i + 1].x - points[i].x,
      points[i + 1].y - points[i].y
    )
  }
  return total
}

/**
 * Sample a path at one arc-length distance.
 *
 * @param points the path's vertices
 * @param distance how far along the path, in image px
 */
function sampleAt(
  points: readonly SimplePathPoint2DType[],
  distance: number
): PathSample | null {
  let remaining = distance
  for (let i = 0; i < points.length - 1; i++) {
    const dx = points[i + 1].x - points[i].x
    const dy = points[i + 1].y - points[i].y
    const span = Math.hypot(dx, dy)
    if (span === 0) {
      continue
    }
    if (remaining <= span || i === points.length - 2) {
      const t = Math.max(0, Math.min(1, remaining / span))
      return {
        x: points[i].x + dx * t,
        y: points[i].y + dy * t,
        ux: dx / span,
        uy: dy / span
      }
    }
    remaining -= span
  }
  return null
}

/**
 * Nearest arc-length position on a path to an arbitrary point.
 *
 * Lets a click be recorded as a distance along the guide, so a manually placed
 * mark keeps its spot even if the guide is later reshaped.
 *
 * @param points the path's vertices
 * @param query the clicked point
 * @param query.x click x in image px
 * @param query.y click y in image px
 */
export function projectToPath(
  points: readonly SimplePathPoint2DType[],
  query: { x: number; y: number }
): { distance: number; offset: number } | null {
  // Must flatten to match stampAlongPath: a click recorded against the control
  // polygon would place its mark somewhere else entirely on a curved guide.
  points = flattenPath(points)
  let best: { distance: number; offset: number } | null = null
  let bestDist = Infinity
  let acc = 0
  for (let i = 0; i < points.length - 1; i++) {
    const ax = points[i].x
    const ay = points[i].y
    const dx = points[i + 1].x - ax
    const dy = points[i + 1].y - ay
    const span = Math.hypot(dx, dy)
    if (span === 0) {
      continue
    }
    let t = ((query.x - ax) * dx + (query.y - ay) * dy) / (span * span)
    t = Math.max(0, Math.min(1, t))
    const px = ax + dx * t
    const py = ay + dy * t
    const d = Math.hypot(query.x - px, query.y - py)
    if (d < bestDist) {
      bestDist = d
      best = {
        distance: acc + t * span,
        // Signed side of the line, so a click off to one side is recorded as
        // an offset rather than snapped onto the centre. Sign matches the
        // (-uy, +ux) convention buildMark uses, so a click round-trips to the
        // same side it was made on.
        offset: ((query.y - ay) * dx - (query.x - ax) * dy) / span
      }
    }
    acc += span
  }
  return best
}

/**
 * Rotate a unit vector by an angle in degrees.
 *
 * @param ux unit x
 * @param uy unit y
 * @param degrees angle to rotate by
 */
function rotate(
  ux: number,
  uy: number,
  degrees: number
): { x: number; y: number } {
  const r = (degrees * Math.PI) / 180
  const cos = Math.cos(r)
  const sin = Math.sin(r)
  return { x: ux * cos - uy * sin, y: ux * sin + uy * cos }
}

/**
 * Build one mark at a sampled point.
 *
 * A DASH is centred on the path. A CHEVRON puts its apex on the path with the
 * arms opening backwards, which is how the ones in real data are drawn.
 *
 * @param sample the path position and direction
 * @param options stamp settings
 */
function buildMark(
  sample: PathSample,
  options: StampOptions
): SimplePathPoint2DType[] {
  const line = (x: number, y: number): SimplePathPoint2DType => ({
    x,
    y,
    pointType: PathPointType.LINE
  })

  // Shift the whole mark perpendicular to the path before shaping it, so an
  // offset mark keeps its geometry and simply sits beside the line.
  const anchor = {
    x: sample.x - sample.uy * options.offset,
    y: sample.y + sample.ux * options.offset
  }

  if (options.template === StampTemplate.DASH) {
    const dir = rotate(sample.ux, sample.uy, options.angle)
    const half = options.length / 2
    return [
      line(anchor.x - dir.x * half, anchor.y - dir.y * half),
      line(anchor.x + dir.x * half, anchor.y + dir.y * half)
    ]
  }

  if (options.template === StampTemplate.CUSTOM) {
    const custom = options.custom
    if (custom === undefined || custom.points.length < 2) {
      return []
    }
    // Rotate the captured shape onto the path direction, then place it.
    const theta =
      Math.atan2(sample.uy, sample.ux) + (options.angle * Math.PI) / 180
    const cos = Math.cos(theta)
    const sin = Math.sin(theta)
    return custom.points.map((p) => ({
      x: anchor.x + (p.x * cos - p.y * sin) * options.scale,
      y: anchor.y + (p.x * sin + p.y * cos) * options.scale,
      pointType: p.pointType
    }))
  }

  // Chevron: arms are independent, since real ones are not symmetric.
  const half = options.apex / 2
  const dirA = rotate(sample.ux, sample.uy, options.angle - half)
  const dirB = rotate(sample.ux, sample.uy, options.angle + half)
  return [
    line(anchor.x + dirA.x * options.armA, anchor.y + dirA.y * options.armA),
    line(anchor.x, anchor.y),
    line(anchor.x + dirB.x * options.armB, anchor.y + dirB.y * options.armB)
  ]
}

/**
 * Capture a drawn mark as a reusable template.
 *
 * The shape is centred on its own midpoint and rotated so its dominant axis
 * (first vertex to last) lies along +x. Stamping re-rotates it to the path, so
 * the captured mark keeps its shape while following the line's direction.
 *
 * @param points the drawn mark's vertices
 * @param name what to call the template
 */
export function captureTemplate(
  points: readonly SimplePathPoint2DType[],
  name: string
): CustomTemplate | null {
  if (points.length < 2) {
    return null
  }
  const cx = points.reduce((sum, p) => sum + p.x, 0) / points.length
  const cy = points.reduce((sum, p) => sum + p.y, 0) / points.length
  const axis = Math.atan2(
    points[points.length - 1].y - points[0].y,
    points[points.length - 1].x - points[0].x
  )
  const cos = Math.cos(-axis)
  const sin = Math.sin(-axis)
  return {
    name,
    points: points.map((p) => {
      const dx = p.x - cx
      const dy = p.y - cy
      return {
        x: dx * cos - dy * sin,
        y: dx * sin + dy * cos,
        pointType: p.pointType
      }
    })
  }
}

/**
 * Stamp marks along a path.
 *
 * Returns one point list per mark. The caller decides whether each becomes its
 * own label or they are combined into one.
 *
 * @param points the traced path's vertices
 * @param options stamp settings
 */
export function stampAlongPath(
  points: readonly SimplePathPoint2DType[],
  options: StampOptions = DEFAULT_STAMP_OPTIONS
): SimplePathPoint2DType[][] {
  // Flatten first: the walk and the perpendicular offset must both follow the
  // drawn curve, not the control polygon.
  const path = flattenPath(points)
  const samples = options.evenSpacing
    ? samplePath(path, options.period, options.margin, options.start)
    : options.positions
        .map((d) => sampleAt(path, d))
        .filter((s): s is PathSample => s !== null)
  return samples
    .map((sample) => buildMark(sample, options))
    .filter((mark) => mark.length >= 2)
}
