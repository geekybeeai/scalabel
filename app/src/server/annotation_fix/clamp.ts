/**
 * Pull out-of-ROI annotation vertices back inside the imagery.
 *
 * Measured on real batches the spill is tiny (median 8 px against images
 * 5000-20000 px wide): an annotator tracing a curb a hair past where imagery
 * ends. So: clamp, don't clip. Vertex count, curve types, closed state and
 * identity are preserved; only coordinates move. Corrections beyond
 * `flagDistance` are still applied but reported for review.
 */

import { LabelExport } from "../../types/export"
import { RoiMask } from "./mask"

/** Corrections beyond this many pixels are reported for review. */
export const DEFAULT_FLAG_DISTANCE = 50.0

/** Nudge clamped vertices this far inside the boundary. */
export const DEFAULT_INSET = 1.5

/** One vertex moved back inside the ROI. */
export interface VertexCorrection {
  /** owning label id */
  labelId: string
  /** owning label category */
  category: string
  /** index within the polyline */
  vertexIndex: number
  /** where it was */
  original: [number, number]
  /** where it went */
  corrected: [number, number]
  /** distance from the original to the nearest inside pixel */
  distance: number
}

/** Outcome of clamping one frame. */
export interface ClampResult {
  /** vertices examined */
  totalVertices: number
  /** vertices moved */
  corrections: VertexCorrection[]
}

/**
 * Round to three decimals.
 *
 * @param v value
 */
function round3(v: number): number {
  return Math.round(v * 1000) / 1000
}

/**
 * Serialise a correction for the run report.
 *
 * @param c the correction
 */
export function correctionToDict(c: VertexCorrection): {
  [key: string]: unknown
} {
  return {
    labelId: c.labelId,
    category: c.category,
    vertexIndex: c.vertexIndex,
    original: c.original.map(round3),
    corrected: c.corrected.map(round3),
    distance: round3(c.distance)
  }
}

/**
 * Corrections large enough to warrant a human look.
 *
 * @param result clamp result
 * @param flagDistance threshold in pixels
 */
export function flaggedCorrections(
  result: ClampResult,
  flagDistance: number
): VertexCorrection[] {
  return result.corrections.filter((c) => c.distance > flagDistance)
}

/**
 * Step a boundary point slightly inward along the incoming direction. If the
 * inset lands back outside (thin region), keep the boundary point.
 *
 * @param roi the mask
 * @param nx nearest inside x
 * @param ny nearest inside y
 * @param ox original x
 * @param oy original y
 * @param inset distance to step
 */
function insetPoint(
  roi: RoiMask,
  nx: number,
  ny: number,
  ox: number,
  oy: number,
  inset: number
): [number, number] {
  const dx = nx - ox
  const dy = ny - oy
  const norm = Math.hypot(dx, dy)
  if (norm < 1e-9) {
    return [nx, ny]
  }
  const cx = nx + (dx / norm) * inset
  const cy = ny + (dy / norm) * inset
  if (roi.contains(cx, cy)) {
    return [cx, cy]
  }
  return [nx, ny]
}

/** Per-vertex type marking a bezier control point ("handle"). */
const HANDLE = "C"

/**
 * The anchor a curve handle belongs to, or -1 if there is none.
 *
 * A curved segment is stored as anchor, handle, handle, anchor ("LCCL"): each
 * handle steers the curve where it leaves the nearer anchor. The owner is
 * therefore the closest anchor along the vertex list, the earlier one on a
 * tie. Closed shapes wrap around the end of the list.
 *
 * @param index index of the handle
 * @param types per-vertex types
 * @param count number of vertices
 * @param closed whether the shape wraps
 */
function handleOwner(
  index: number,
  types: string,
  count: number,
  closed: boolean
): number {
  const at = (i: number): number => (closed ? ((i % count) + count) % count : i)
  let back = -1
  let forward = -1
  let backSteps = 0
  let forwardSteps = 0
  for (let step = 1; step < count; step++) {
    const i = at(index - step)
    if (i < 0) {
      break
    }
    if (types[i] !== HANDLE) {
      back = i
      backSteps = step
      break
    }
  }
  for (let step = 1; step < count; step++) {
    const i = at(index + step)
    if (i >= count) {
      break
    }
    if (types[i] !== HANDLE) {
      forward = i
      forwardSteps = step
      break
    }
  }
  if (back === -1) {
    return forward
  }
  if (forward === -1) {
    return back
  }
  return backSteps <= forwardSteps ? back : forward
}

/**
 * Clamp one polyline's vertices into the ROI. Vertices already inside are
 * returned untouched with their exact original values.
 *
 * Curve handles (type "C") are never clamped on their own: they do not lie on
 * the line, so a handle out in the padding is normal for a curve that hugs
 * the image edge, and dragging it inside would reshape the curve. Only
 * anchors are tested. When an anchor does have to move, its handles move by
 * the same offset, so the curve keeps its shape and simply shifts with the
 * anchor rather than bending around a handle left behind.
 *
 * @param roi the mask
 * @param vertices the polyline's vertices
 * @param labelId owning label id, for the report
 * @param category owning label category, for the report
 * @param inset inward nudge in pixels
 * @param types per-vertex types: "L" anchor, "C" handle; empty = all anchors
 * @param closed whether the shape is closed, so handles wrap around
 */
export function clampVertices(
  roi: RoiMask,
  vertices: Array<[number, number]>,
  labelId: string = "",
  category: string = "",
  inset: number = DEFAULT_INSET,
  types: string = "",
  closed: boolean = false
): [Array<[number, number]>, VertexCorrection[]] {
  const out: Array<[number, number]> = vertices.map((vertex) => [
    Number(vertex[0]),
    Number(vertex[1])
  ])
  const corrections: VertexCorrection[] = []
  // anchor index -> how far it moved
  const moved = new Map<number, [number, number]>()

  out.forEach(([x, y], index) => {
    if (types[index] === HANDLE || roi.contains(x, y)) {
      return
    }
    const [nx, ny, distance] = roi.nearestInside(x, y)
    const corrected = insetPoint(roi, nx, ny, x, y, inset)
    out[index] = corrected
    moved.set(index, [corrected[0] - x, corrected[1] - y])
    corrections.push({
      labelId,
      category,
      vertexIndex: index,
      original: [x, y],
      corrected,
      distance
    })
  })

  if (moved.size > 0) {
    out.forEach(([x, y], index) => {
      if (types[index] !== HANDLE) {
        return
      }
      const offset = moved.get(handleOwner(index, types, out.length, closed))
      if (offset !== undefined) {
        out[index] = [x + offset[0], y + offset[1]]
      }
    })
  }
  return [out, corrections]
}

/**
 * Clamp every poly2d vertex across a frame's labels, in place. Only
 * coordinates change, and only on polylines that had a correction.
 *
 * @param roi the mask
 * @param labels the frame's labels
 * @param inset inward nudge in pixels
 */
export function clampLabels(
  roi: RoiMask,
  labels: LabelExport[],
  inset: number = DEFAULT_INSET
): ClampResult {
  const result: ClampResult = { totalVertices: 0, corrections: [] }
  for (const label of labels) {
    for (const poly of label.poly2d ?? []) {
      const vertices = poly.vertices ?? []
      result.totalVertices += vertices.length
      const [corrected, corrections] = clampVertices(
        roi,
        vertices,
        String(label.id ?? ""),
        String(label.category ?? ""),
        inset,
        String(poly.types ?? ""),
        poly.closed
      )
      if (corrections.length > 0) {
        poly.vertices = corrected
        result.corrections.push(...corrections)
      }
    }
  }
  return result
}
