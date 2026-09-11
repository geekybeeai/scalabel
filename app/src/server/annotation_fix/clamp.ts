/**
 * Pull out-of-ROI annotation vertices back inside the imagery.
 *
 * Measured on 12 frames of a real batch: 97 of 2479 vertices (3.9%) fall outside
 * the ROI, and every frame is affected. The spill distribution decides the fix —
 * median 8 px, p90 16 px, max 198 px, against images 5000-20000 px wide. A median
 * spill of ~0.2% of image width is an annotator tracing a curb a hair past where
 * imagery ends, not misplaced geometry.
 *
 * So: clamp, don't clip. There is no meaningful segment to truncate at 8 px, and
 * clipping would rebuild lines and change vertex counts for what is effectively
 * rounding error. Clamping preserves vertex count, curve types and identity —
 * only coordinates move. Corrections beyond `flagDistance` are still applied but
 * reported, so genuine mistakes (the 198 px outlier) surface rather than being
 * silently absorbed.
 */

import { RoiMask } from "./mask"
import { LabelLike, PolyLike, Vertex } from "./types"

/**
 * Corrections beyond this many pixels are reported for review. Chosen at ~6x the
 * measured p90 (16 px), so ordinary trace overshoot stays quiet.
 */
export const DEFAULT_FLAG_DISTANCE = 50

/**
 * Nudge clamped vertices this far inside the boundary. Landing exactly on the
 * edge leaves them ambiguous for any later inside/outside test.
 */
export const DEFAULT_INSET = 1.5

/**
 * One vertex moved back inside the ROI.
 */
export interface VertexCorrection {
  /** id of the label the vertex belongs to */
  labelId: string
  /** category of that label */
  category: string
  /** position of the vertex within its polyline */
  vertexIndex: number
  /** where the vertex was */
  original: Vertex
  /** where it ended up */
  corrected: Vertex
  /** how far it moved */
  distance: number
}

/**
 * Outcome of clamping one frame.
 */
export interface ClampResult {
  /** every poly2d vertex seen, whether it moved or not */
  totalVertices: number
  /** the vertices that moved */
  corrections: VertexCorrection[]
}

/**
 * Serialise a correction for the run report.
 *
 * @param correction the correction to render
 */
export function correctionToJson(correction: VertexCorrection): {
  [key: string]: unknown
} {
  return {
    labelId: correction.labelId,
    category: correction.category,
    vertexIndex: correction.vertexIndex,
    original: correction.original.map((v) => round(v, 3)),
    corrected: correction.corrected.map((v) => round(v, 3)),
    distance: round(correction.distance, 3)
  }
}

/**
 * Round to a fixed number of decimal places.
 *
 * @param value the number to round
 * @param places how many decimal places to keep
 */
function round(value: number, places: number): number {
  const factor = Math.pow(10, places)
  return Math.round(value * factor) / factor
}

/**
 * Corrections large enough to warrant a human look.
 *
 * @param result the clamp outcome to filter
 * @param flagDistance report corrections that moved further than this
 */
export function flagged(
  result: ClampResult,
  flagDistance: number
): VertexCorrection[] {
  return result.corrections.filter((c) => c.distance > flagDistance)
}

/**
 * Step a boundary point slightly inward, along the incoming direction.
 *
 * Moves from the original outside point toward the nearest inside pixel and
 * keeps going by `inset`. If that overshoots into a thin part of the region and
 * lands back outside, the un-inset boundary point is kept instead.
 *
 * @param roi the region the point must end up inside
 * @param nx nearest inside pixel x
 * @param ny nearest inside pixel y
 * @param ox the original, outside x
 * @param oy the original, outside y
 * @param inset how far past the boundary to step
 */
function insetPoint(
  roi: RoiMask,
  nx: number,
  ny: number,
  ox: number,
  oy: number,
  inset: number
): Vertex {
  const dx = nx - ox
  const dy = ny - oy
  const norm = Math.sqrt(dx * dx + dy * dy)
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
 * Clamp one polyline's vertices into the ROI.
 *
 * Vertices already inside are returned untouched, preserving their exact
 * original values.
 *
 * @param roi the region to clamp into
 * @param vertices the polyline's vertices
 * @param labelId id of the owning label, recorded on each correction
 * @param category category of the owning label, recorded on each correction
 * @param inset how far past the boundary to nudge a corrected vertex
 */
export function clampVertices(
  roi: RoiMask,
  vertices: Vertex[],
  labelId: string,
  category: string,
  inset: number = DEFAULT_INSET
): {
  /** the clamped vertices */ vertices: Vertex[]
  /** what moved */ corrections: VertexCorrection[]
} {
  const out: Vertex[] = []
  const corrections: VertexCorrection[] = []

  for (let index = 0; index < vertices.length; index++) {
    const vertex = vertices[index]
    const x = Number(vertex[0])
    const y = Number(vertex[1])

    if (roi.contains(x, y)) {
      out.push([x, y])
      continue
    }

    const nearest = roi.nearestInside(x, y)
    if (nearest === null) {
      // Nothing to clamp to: leave the vertex where it is rather than inventing
      // a position.
      out.push([x, y])
      continue
    }

    const [cx, cy] = insetPoint(roi, nearest.nx, nearest.ny, x, y, inset)
    out.push([cx, cy])
    corrections.push({
      labelId,
      category,
      vertexIndex: index,
      original: [x, y],
      corrected: [cx, cy],
      distance: nearest.distance
    })
  }

  return { vertices: out, corrections }
}

/**
 * Clamp every poly2d vertex across a frame's labels, in place.
 *
 * Only coordinates change: vertex count, `types`, `closed`, category and id are
 * all preserved.
 *
 * @param roi the region to clamp into
 * @param labels the frame's labels, mutated in place
 * @param inset how far past the boundary to nudge a corrected vertex
 */
export function clampLabels(
  roi: RoiMask,
  labels: LabelLike[],
  inset: number = DEFAULT_INSET
): ClampResult {
  const result: ClampResult = { totalVertices: 0, corrections: [] }

  for (const label of labels) {
    const polys: PolyLike[] = label.poly2d ?? []
    for (const poly of polys) {
      const vertices: Vertex[] = poly.vertices ?? []
      result.totalVertices += vertices.length
      const clamped = clampVertices(
        roi,
        vertices,
        String(label.id ?? ""),
        String(label.category ?? ""),
        inset
      )
      if (clamped.corrections.length > 0) {
        poly.vertices = clamped.vertices
        result.corrections.push(...clamped.corrections)
      }
    }
  }

  return result
}
