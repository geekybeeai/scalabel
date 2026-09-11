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

/**
 * Clamp one polyline's vertices into the ROI. Vertices already inside are
 * returned untouched with their exact original values.
 *
 * @param roi the mask
 * @param vertices the polyline's vertices
 * @param labelId owning label id, for the report
 * @param category owning label category, for the report
 * @param inset inward nudge in pixels
 */
export function clampVertices(
  roi: RoiMask,
  vertices: Array<[number, number]>,
  labelId: string = "",
  category: string = "",
  inset: number = DEFAULT_INSET
): [Array<[number, number]>, VertexCorrection[]] {
  const out: Array<[number, number]> = []
  const corrections: VertexCorrection[] = []
  vertices.forEach((vertex, index) => {
    const x = Number(vertex[0])
    const y = Number(vertex[1])
    if (roi.contains(x, y)) {
      out.push([x, y])
      return
    }
    const [nx, ny, distance] = roi.nearestInside(x, y)
    const corrected = insetPoint(roi, nx, ny, x, y, inset)
    out.push(corrected)
    corrections.push({
      labelId,
      category,
      vertexIndex: index,
      original: [x, y],
      corrected,
      distance
    })
  })
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
        inset
      )
      if (corrections.length > 0) {
        poly.vertices = corrected
        result.corrections.push(...corrections)
      }
    }
  }
  return result
}
