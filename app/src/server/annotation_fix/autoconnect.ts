/**
 * Batch endpoint connection for imported polylines.
 *
 * This is the offline equivalent of the editor's drag-an-endpoint-onto-another
 * gesture. In the app, `Label2DList.findNearestEndpoint` finds a target within
 * 15 screen px and `Polygon2D.mergeWith` splices the two lines into one label.
 * Here the same rules run over the export JSON before import, so a batch of
 * predictions arrives already joined.
 *
 * Two deliberate differences from the interactive path:
 *
 * Tolerance is in IMAGE pixels, not screen pixels. There is no zoom level
 * offline, and results must not depend on one.
 *
 * Only compatible-category pairs are merged. The editor also snaps endpoints
 * across categories (coordinates align, labels stay distinct), but that relies
 * on a visible indicator and one-step undo. Neither exists at import time, so
 * moving vertices with nobody watching is not worth it.
 *
 * The four splice orientations mirror `Polygon2D.mergeWith` exactly, including
 * dropping the duplicate junction vertex.
 */

import { LabelLike, PolyLike } from "./types"

/** Matches the editor's 15 px snap radius, reinterpreted in image space. */
export const DEFAULT_TOLERANCE = 15

/**
 * Optional straightness guard. Two lines meeting end-to-end at ~180 degrees are
 * a real continuation; two meeting sharply are more likely a genuine fork. Off
 * by default so the first runs are pure-distance and easy to reason about.
 */
export const DEFAULT_MIN_ANGLE = 0

/**
 * Category pairs that describe the SAME physical feature and may therefore be
 * merged across the class boundary.
 *
 * A road edge changes curb status partway along constantly, which splits one
 * continuous edge into two labels of different classes whose ends touch. Across
 * a 145-frame batch this accounted for 209 of the 266 near-touching pairs the
 * same-category rule refused.
 *
 * Paint markings are deliberately NOT in here: a yellow line meeting a white one
 * is usually a real class boundary, and merging would silently rewrite one of
 * their classes.
 */
export const DEFAULT_MERGEABLE_CATEGORIES: ReadonlyArray<ReadonlySet<string>> =
  [new Set(["curb_road_edge", "without_curb_road_edge"])]

/**
 * One merge of two polylines.
 */
export interface Connection {
  /** id of the label that survived */
  keptId: string
  /** id of the label that was folded into it */
  absorbedId: string
  /** category of the surviving label */
  category: string
  /** where the two lines met */
  junction: number[]
  /** how far apart the endpoints were */
  gap: number
  /** junction angle in degrees, when the guard was enabled */
  angle: number | null
}

/**
 * Outcome of auto-connecting one frame.
 */
export interface ConnectResult {
  /** every merge applied, in the order they were applied */
  connections: Connection[]
  /** how many labels the frame started with */
  labelsBefore: number
  /** how many it ended with */
  labelsAfter: number
}

/**
 * Whether two categories may be joined.
 *
 * Identical categories always may. Different ones may only when the pair is
 * listed as describing one physical feature.
 *
 * @param categoryA one label's category
 * @param categoryB the other label's category
 * @param mergeable category pairs that describe the same physical feature
 */
function categoriesCompatible(
  categoryA: string,
  categoryB: string,
  mergeable: ReadonlyArray<ReadonlySet<string>>
): boolean {
  if (categoryA === categoryB) {
    return true
  }
  return mergeable.some(
    (allowed) =>
      allowed.size === 2 && allowed.has(categoryA) && allowed.has(categoryB)
  )
}

/**
 * Return a label's single open `poly2d`, or null if it is not eligible.
 *
 * Closed rings never participate, matching the editor. Multi-polygon labels are
 * skipped: splicing one part of a compound shape has no clear meaning.
 *
 * @param label the label to inspect
 */
function openPolyline(label: LabelLike): PolyLike | null {
  const polys = label.poly2d ?? []
  if (polys.length !== 1) {
    return null
  }
  const poly = polys[0]
  if (poly.closed === true) {
    return null
  }
  const vertices = poly.vertices ?? []
  if (vertices.length < 2) {
    return null
  }
  return poly
}

/**
 * Coordinates of a polyline's start or end vertex.
 *
 * @param poly the polyline
 * @param isStart true for the first vertex, false for the last
 */
function endpoint(poly: PolyLike, isStart: boolean): number[] {
  const vertices = poly.vertices as number[][]
  const vertex = isStart ? vertices[0] : vertices[vertices.length - 1]
  return [Number(vertex[0]), Number(vertex[1])]
}

/**
 * Unit vector pointing outward from the given endpoint.
 *
 * Uses the neighbouring vertex, so it reflects the line's local heading where it
 * terminates.
 *
 * @param poly the polyline
 * @param isStart true for the first vertex, false for the last
 */
function direction(poly: PolyLike, isStart: boolean): number[] | null {
  const vertices = poly.vertices as number[][]
  if (vertices.length < 2) {
    return null
  }
  const tip = isStart ? vertices[0] : vertices[vertices.length - 1]
  const neighbour = isStart ? vertices[1] : vertices[vertices.length - 2]
  const dx = Number(tip[0]) - Number(neighbour[0])
  const dy = Number(tip[1]) - Number(neighbour[1])
  const norm = Math.sqrt(dx * dx + dy * dy)
  if (norm < 1e-9) {
    return null
  }
  return [dx / norm, dy / norm]
}

/**
 * Angle in degrees at a prospective junction.
 *
 * 180 means the two lines continue straight through each other; small values
 * mean they double back sharply.
 *
 * @param polyA first polyline
 * @param isStartA which end of the first is involved
 * @param polyB second polyline
 * @param isStartB which end of the second is involved
 */
function junctionAngle(
  polyA: PolyLike,
  isStartA: boolean,
  polyB: PolyLike,
  isStartB: boolean
): number | null {
  const dirA = direction(polyA, isStartA)
  const dirB = direction(polyB, isStartB)
  if (dirA === null || dirB === null) {
    return null
  }
  // Each direction points outward from its own tip, so continuation shows up as
  // the vectors being opposed.
  const dot = dirA[0] * -dirB[0] + dirA[1] * -dirB[1]
  const cosine = Math.min(Math.max(dot, -1), 1)
  return 180 - (Math.acos(cosine) * 180) / Math.PI
}

/**
 * Join two vertex runs at the touching endpoints.
 *
 * The four cases mirror `Polygon2D.mergeWith`. In each, B's duplicate junction
 * vertex is dropped so the seam carries a single vertex, and the per-vertex
 * `types` string is spliced identically so curve flags follow their vertices.
 *
 * @param verticesA first polyline's vertices
 * @param typesA first polyline's per-vertex flags
 * @param isStartA whether the first is joined at its start
 * @param verticesB second polyline's vertices
 * @param typesB second polyline's per-vertex flags
 * @param isStartB whether the second is joined at its start
 */
function splice(
  verticesA: number[][],
  typesA: string,
  isStartA: boolean,
  verticesB: number[][],
  typesB: string,
  isStartB: boolean
): {
  /** the joined vertices */ vertices: number[][]
  /** the joined per-vertex flags */ types: string
} {
  const listA = verticesA.slice()
  const listB = verticesB.slice()
  const seqA = typesA.split("")
  const seqB = typesB.split("")

  if (!isStartA && isStartB) {
    // A.end -> B.start
    return {
      vertices: listA.concat(listB.slice(1)),
      types: seqA.concat(seqB.slice(1)).join("")
    }
  }
  if (isStartA && !isStartB) {
    // A.start -> B.end
    return {
      vertices: listB.slice(0, -1).concat(listA),
      types: seqB.slice(0, -1).concat(seqA).join("")
    }
  }
  if (isStartA && isStartB) {
    // A.start -> B.start: reverse A so its tail meets B's head
    return {
      vertices: listA.slice().reverse().concat(listB.slice(1)),
      types: seqA.slice().reverse().concat(seqB.slice(1)).join("")
    }
  }
  // A.end -> B.end: reverse B so its head meets A's tail
  return {
    vertices: listA.concat(listB.slice().reverse().slice(1)),
    types: seqA.concat(seqB.slice().reverse().slice(1)).join("")
  }
}

/**
 * A free endpoint available for connection.
 */
interface FreeEndpoint {
  /** index of the owning label in the working list */
  index: number
  /** true for the polyline's first vertex, false for its last */
  isStart: boolean
  /** the owning polyline */
  poly: PolyLike
  /** the endpoint's coordinates */
  point: number[]
}

/**
 * Merge compatible-category polylines whose endpoints nearly touch.
 *
 * Pairs are considered shortest-gap first and each endpoint is consumed at most
 * once, so three lines converging on one point cannot all claim it. After each
 * merge the surviving line's endpoints have moved, so candidates are re-derived
 * from scratch; chains (A-B-C) therefore resolve across passes.
 *
 * @param labels the frame's labels
 * @param tolerance largest endpoint gap that still counts as touching
 * @param minAngle reject junctions sharper than this many degrees; 0 disables
 * @param mergeableCategories category pairs describing one physical feature
 */
export function connectLabels(
  labels: LabelLike[],
  tolerance: number = DEFAULT_TOLERANCE,
  minAngle: number = DEFAULT_MIN_ANGLE,
  mergeableCategories: ReadonlyArray<
    ReadonlySet<string>
  > = DEFAULT_MERGEABLE_CATEGORIES
): {
  /** the labels that survived */ labels: LabelLike[]
  /** what was merged */ result: ConnectResult
} {
  const result: ConnectResult = {
    connections: [],
    labelsBefore: labels.length,
    labelsAfter: labels.length
  }
  const working = labels.slice()
  const absorbed = new Set<LabelLike>()

  for (;;) {
    const endpoints: FreeEndpoint[] = []
    for (let index = 0; index < working.length; index++) {
      const label = working[index]
      if (absorbed.has(label)) {
        continue
      }
      const poly = openPolyline(label)
      if (poly === null) {
        continue
      }
      for (const isStart of [true, false]) {
        endpoints.push({
          index,
          isStart,
          poly,
          point: endpoint(poly, isStart)
        })
      }
    }

    let best: {
      /** the endpoint gap */ gap: number
      /** first label index */ indexA: number
      /** which end of the first */ startA: boolean
      /** second label index */ indexB: number
      /** which end of the second */ startB: boolean
      /** junction angle, when measured */ angle: number | null
    } | null = null

    for (let i = 0; i < endpoints.length; i++) {
      const a = endpoints[i]
      const labelA = working[a.index]
      for (let j = i + 1; j < endpoints.length; j++) {
        const b = endpoints[j]
        if (a.index === b.index) {
          // Self-closing is an editor gesture, not a batch one.
          continue
        }
        const labelB = working[b.index]
        if (
          !categoriesCompatible(
            String(labelA.category ?? ""),
            String(labelB.category ?? ""),
            mergeableCategories
          )
        ) {
          continue
        }

        const dx = a.point[0] - b.point[0]
        const dy = a.point[1] - b.point[1]
        const gap = Math.sqrt(dx * dx + dy * dy)
        if (gap > tolerance) {
          continue
        }

        let angle: number | null = null
        if (minAngle > 0) {
          angle = junctionAngle(a.poly, a.isStart, b.poly, b.isStart)
          if (angle !== null && angle < minAngle) {
            continue
          }
        }

        if (best === null || gap < best.gap) {
          best = {
            gap,
            indexA: a.index,
            startA: a.isStart,
            indexB: b.index,
            startB: b.isStart,
            angle
          }
        }
      }
    }

    if (best === null) {
      break
    }

    const labelA = working[best.indexA]
    const labelB = working[best.indexB]
    const polyA = openPolyline(labelA)
    const polyB = openPolyline(labelB)
    if (polyA === null || polyB === null) {
      break
    }

    const junction = endpoint(polyA, best.startA)
    const joined = splice(
      polyA.vertices as number[][],
      String(polyA.types ?? ""),
      best.startA,
      polyB.vertices as number[][],
      String(polyB.types ?? ""),
      best.startB
    )
    polyA.vertices = joined.vertices
    polyA.types = joined.types

    absorbed.add(labelB)
    result.connections.push({
      keptId: String(labelA.id ?? ""),
      absorbedId: String(labelB.id ?? ""),
      category: String(labelA.category ?? ""),
      junction,
      gap: best.gap,
      angle: best.angle
    })
  }

  const survivors = working.filter((label) => !absorbed.has(label))
  result.labelsAfter = survivors.length
  return { labels: survivors, result }
}
