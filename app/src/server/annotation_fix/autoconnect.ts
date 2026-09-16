/**
 * Batch endpoint connection for imported polylines.
 *
 * The offline equivalent of the editor's drag-an-endpoint-onto-another
 * gesture: `Label2DList.findNearestEndpoint` finds a target within 15 screen
 * px and `Polygon2D.mergeWith` splices the two lines into one label. Here the
 * same rules run over export frames before import.
 *
 * Differences from the interactive path, kept from the Python original:
 * tolerance is in IMAGE pixels (no zoom offline), and only same-category
 * pairs merge — plus the one listed cross-category pair that describes the
 * same physical feature.
 */

import { LabelExport, PolygonExportType } from "../../types/export"

/** Matches the editor's 15 px snap radius, reinterpreted in image space. */
export const DEFAULT_TOLERANCE = 15.0

/** Straightness guard in degrees; 0 disables it. */
export const DEFAULT_MIN_ANGLE = 0.0

/**
 * Category pairs describing the SAME physical feature, allowed to merge
 * across the class boundary. A road edge changes curb status partway along
 * constantly, splitting one edge into two labels whose ends touch. Paint
 * markings are deliberately absent: a yellow line meeting a white one is a
 * real class boundary.
 */
export const DEFAULT_MERGEABLE_CATEGORIES: ReadonlyArray<ReadonlySet<string>> =
  [new Set(["curb_road_edge", "without_curb_road_edge"])]

/** One merge of two polylines. */
export interface Connection {
  /** id of the label that survived */
  keptId: string
  /** id of the label spliced into it */
  absorbedId: string
  /** category of the surviving label */
  category: string
  /** where the two lines were joined */
  junction: [number, number]
  /** distance between the two endpoints before the merge */
  gap: number
  /** junction angle in degrees, only when the guard was active */
  angle?: number
}

/** Outcome of auto-connecting one frame. */
export interface ConnectResult {
  /** merges applied, in order */
  connections: Connection[]
  /** label count before */
  labelsBefore: number
  /** label count after */
  labelsAfter: number
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
 * Serialise a connection for the run report, rounding like the Python
 * report does.
 *
 * @param c the connection
 */
export function connectionToDict(c: Connection): { [key: string]: unknown } {
  const payload: { [key: string]: unknown } = {
    keptId: c.keptId,
    absorbedId: c.absorbedId,
    category: c.category,
    junction: c.junction.map((v) => round3(v)),
    gap: round3(c.gap)
  }
  if (c.angle !== undefined) {
    payload.angle = Math.round(c.angle * 100) / 100
  }
  return payload
}

/**
 * Whether two categories may be joined: identical always, different only
 * when the pair is listed.
 *
 * @param a first category
 * @param b second category
 * @param mergeable allowed cross-category pairs
 */
function categoriesCompatible(
  a: string,
  b: string,
  mergeable: ReadonlyArray<ReadonlySet<string>>
): boolean {
  if (a === b) {
    return true
  }
  return mergeable.some((pair) => pair.size === 2 && pair.has(a) && pair.has(b))
}

/**
 * A label's single open poly2d, or null if it is not eligible. Closed rings
 * never participate; multi-polygon labels are skipped.
 *
 * @param label the label
 */
function openPolyline(label: LabelExport): PolygonExportType | null {
  const polys = label.poly2d ?? []
  if (polys.length !== 1) {
    return null
  }
  const poly = polys[0]
  if (poly.closed) {
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
 * @param isStart start (true) or end (false)
 */
function endpoint(poly: PolygonExportType, isStart: boolean): [number, number] {
  const v = isStart ? poly.vertices[0] : poly.vertices[poly.vertices.length - 1]
  return [Number(v[0]), Number(v[1])]
}

/**
 * Unit vector pointing outward from the given endpoint.
 *
 * @param poly the polyline
 * @param isStart which endpoint
 */
function direction(
  poly: PolygonExportType,
  isStart: boolean
): [number, number] | null {
  const vs = poly.vertices
  if (vs.length < 2) {
    return null
  }
  const tip = isStart ? vs[0] : vs[vs.length - 1]
  const step = isStart ? 1 : -1
  for (
    let index = isStart ? 1 : vs.length - 2;
    index >= 0 && index < vs.length;
    index += step
  ) {
    const neighbour = vs[index]
    const dx = Number(tip[0]) - Number(neighbour[0])
    const dy = Number(tip[1]) - Number(neighbour[1])
    const norm = Math.hypot(dx, dy)
    if (norm >= 1e-9) {
      return [dx / norm, dy / norm]
    }
  }
  return null
}

/**
 * Angle between two unit vectors in degrees.
 *
 * @param a first unit vector
 * @param b second unit vector
 */
function angleBetween(a: [number, number], b: [number, number]): number {
  const cosine = Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1]))
  return (Math.acos(cosine) * 180) / Math.PI
}

/**
 * Angle in degrees at a prospective junction: 180 means the lines continue
 * straight through each other.
 *
 * @param polyA first polyline
 * @param startA endpoint of A
 * @param polyB second polyline
 * @param startB endpoint of B
 */
function junctionAngle(
  polyA: PolygonExportType,
  startA: boolean,
  polyB: PolygonExportType,
  startB: boolean
): number | null {
  const dirA = direction(polyA, startA)
  const dirB = direction(polyB, startB)
  if (dirA === null || dirB === null) {
    return null
  }
  // Both point outward from their tips, so continuation shows as opposition.
  return 180 - angleBetween(dirA, [-dirB[0], -dirB[1]])
}

/**
 * Whether the endpoint-to-endpoint gap follows both outward tangents.
 *
 * This rejects offset parallel lines even when their endpoint orientation makes
 * them look like a straight continuation. Coincident endpoints have no gap
 * direction, so their junction angle alone decides the candidate.
 *
 * @param pointA first endpoint
 * @param dirA outward tangent at the first endpoint
 * @param pointB second endpoint
 * @param dirB outward tangent at the second endpoint
 * @param minAngle minimum junction angle in degrees
 */
function gapFollowsTangents(
  pointA: [number, number],
  dirA: [number, number],
  pointB: [number, number],
  dirB: [number, number],
  minAngle: number
): boolean {
  const dx = pointB[0] - pointA[0]
  const dy = pointB[1] - pointA[1]
  const gap = Math.hypot(dx, dy)
  if (gap < 1e-9) {
    return true
  }
  const connector: [number, number] = [dx / gap, dy / gap]
  const maxDeviation = 180 - minAngle
  return (
    angleBetween(dirA, connector) <= maxDeviation &&
    angleBetween(dirB, [-connector[0], -connector[1]]) <= maxDeviation
  )
}

/**
 * Whether the endpoint's first usable inward vertex is a Bezier control
 * point. Curve controls, rather than their anchor endpoints, determine the
 * tangent of a rendered Bezier span.
 *
 * @param poly polyline to inspect
 * @param isStart whether to inspect its start endpoint
 */
function endpointTouchesCurve(
  poly: PolygonExportType,
  isStart: boolean
): boolean {
  const vs = poly.vertices
  const tip = isStart ? vs[0] : vs[vs.length - 1]
  const step = isStart ? 1 : -1
  const types = String(poly.types ?? "")
  for (
    let index = isStart ? 1 : vs.length - 2;
    index >= 0 && index < vs.length;
    index += step
  ) {
    const neighbour = vs[index]
    if (
      Math.hypot(
        Number(tip[0]) - Number(neighbour[0]),
        Number(tip[1]) - Number(neighbour[1])
      ) >= 1e-9
    ) {
      return types[index] === "C"
    }
  }
  return false
}

/**
 * Unit vector from one vertex to its first distinct neighbour in a direction.
 *
 * @param vertices vertex run to inspect
 * @param index vertex index
 * @param step direction to search
 */
function vectorToNeighbour(
  vertices: Array<[number, number]>,
  index: number,
  step: number
): [number, number] | null {
  const tip = vertices[index]
  for (
    let neighbourIndex = index + step;
    neighbourIndex >= 0 && neighbourIndex < vertices.length;
    neighbourIndex += step
  ) {
    const neighbour = vertices[neighbourIndex]
    const dx = Number(neighbour[0]) - Number(tip[0])
    const dy = Number(neighbour[1]) - Number(tip[1])
    const norm = Math.hypot(dx, dy)
    if (norm >= 1e-9) {
      return [dx / norm, dy / norm]
    }
  }
  return null
}

/**
 * Whether a curve-adjacent candidate stays continuous after it is spliced.
 *
 * The editor merge drops B's endpoint and keeps its following vertices. For a
 * Bezier endpoint, the rendered seam therefore goes through B's control
 * point, not through the raw A-to-B endpoint gap. Validate that resulting
 * seam and ensure replacing B's endpoint does not turn B's own tangent by
 * more than the configured deviation.
 *
 * @param polyA surviving polyline
 * @param startA endpoint of A joined to B
 * @param polyB absorbed polyline
 * @param startB endpoint of B joined to A
 * @param minAngle minimum continuity angle in degrees
 */
function splicedSeamFollowsTangents(
  polyA: PolygonExportType,
  startA: boolean,
  polyB: PolygonExportType,
  startB: boolean,
  minAngle: number
): boolean {
  const [vertices] = splice(
    polyA.vertices,
    String(polyA.types ?? ""),
    startA,
    polyB.vertices,
    String(polyB.types ?? ""),
    startB
  )
  const junctionIndex =
    startA && !startB ? polyB.vertices.length - 1 : polyA.vertices.length - 1
  const before = vectorToNeighbour(vertices, junctionIndex, -1)
  const after = vectorToNeighbour(vertices, junctionIndex, 1)
  const dirB = direction(polyB, startB)
  if (before === null || after === null || dirB === null) {
    return false
  }

  const seamAngle = 180 - angleBetween(before, [-after[0], -after[1]])
  if (seamAngle < minAngle) {
    return false
  }

  // B's joined endpoint is removed by splice. Its remaining side must retain
  // the original direction into B, or an offset neighbour could be bent into
  // a false continuation.
  const bSide = startA && !startB ? before : after
  const maxDeviation = 180 - minAngle
  return angleBetween(bSide, [-dirB[0], -dirB[1]]) <= maxDeviation
}

/**
 * Join two vertex runs at the touching endpoints, mirroring
 * `Polygon2D.mergeWith`. B's duplicate junction vertex is dropped and the
 * per-vertex types string is spliced identically.
 *
 * @param verticesA A's vertices
 * @param typesA A's types
 * @param startA whether A joins at its start
 * @param verticesB B's vertices
 * @param typesB B's types
 * @param startB whether B joins at its start
 */
export function splice(
  verticesA: Array<[number, number]>,
  typesA: string,
  startA: boolean,
  verticesB: Array<[number, number]>,
  typesB: string,
  startB: boolean
): [Array<[number, number]>, string] {
  const a = verticesA.slice()
  const b = verticesB.slice()
  const reverse = (s: string): string => s.split("").reverse().join("")

  if (!startA && startB) {
    return [a.concat(b.slice(1)), typesA + typesB.slice(1)]
  }
  if (startA && !startB) {
    return [b.slice(0, -1).concat(a), typesB.slice(0, -1) + typesA]
  }
  if (startA && startB) {
    return [a.reverse().concat(b.slice(1)), reverse(typesA) + typesB.slice(1)]
  }
  return [a.concat(b.reverse().slice(1)), typesA + reverse(typesB).slice(1)]
}

/** A free endpoint of an eligible line. */
interface Endpoint {
  /** index into the working list */
  index: number
  /** start or end */
  isStart: boolean
  /** the polyline */
  poly: PolygonExportType
  /** endpoint coordinates */
  point: [number, number]
}

/** Best candidate pair found in one pass. */
interface Candidate {
  /** endpoint gap */
  gap: number
  /** index of A */
  idxA: number
  /** A joins at its start */
  startA: boolean
  /** index of B */
  idxB: number
  /** B joins at its start */
  startB: boolean
  /** junction angle when the guard was active */
  angle: number | null
}

/**
 * Merge polylines whose endpoints nearly touch.
 *
 * Pairs are taken shortest-gap first and each endpoint is consumed at most
 * once. After every merge the candidates are rebuilt from scratch, so chains
 * resolve across passes. Survivors are returned in their original order;
 * labels are mutated in place (vertices/types of the kept label).
 *
 * @param labels the frame's labels
 * @param tolerance maximum endpoint gap in image pixels
 * @param minAngle minimum junction angle in degrees, 0 disables
 * @param mergeable allowed cross-category pairs
 */
export function connectLabels(
  labels: LabelExport[],
  tolerance: number = DEFAULT_TOLERANCE,
  minAngle: number = DEFAULT_MIN_ANGLE,
  mergeable: ReadonlyArray<ReadonlySet<string>> = DEFAULT_MERGEABLE_CATEGORIES
): [LabelExport[], ConnectResult] {
  const result: ConnectResult = {
    connections: [],
    labelsBefore: labels.length,
    labelsAfter: labels.length
  }
  const working = labels.slice()
  const absorbed = new Set<LabelExport>()

  for (;;) {
    const endpoints: Endpoint[] = []
    working.forEach((label, index) => {
      if (absorbed.has(label)) {
        return
      }
      const poly = openPolyline(label)
      if (poly === null) {
        return
      }
      for (const isStart of [true, false]) {
        endpoints.push({
          index,
          isStart,
          poly,
          point: endpoint(poly, isStart)
        })
      }
    })

    let best: Candidate | null = null
    for (let i = 0; i < endpoints.length; i++) {
      const ea = endpoints[i]
      const labelA = working[ea.index]
      for (let j = i + 1; j < endpoints.length; j++) {
        const eb = endpoints[j]
        if (ea.index === eb.index) {
          continue // Self-closing is an editor gesture, not a batch one.
        }
        const labelB = working[eb.index]
        if (
          !categoriesCompatible(
            String(labelA.category ?? ""),
            String(labelB.category ?? ""),
            mergeable
          )
        ) {
          continue
        }
        const gap = Math.hypot(
          ea.point[0] - eb.point[0],
          ea.point[1] - eb.point[1]
        )
        if (gap > tolerance) {
          continue
        }
        let angle: number | null = null
        if (minAngle > 0) {
          const dirA = direction(ea.poly, ea.isStart)
          const dirB = direction(eb.poly, eb.isStart)
          if (dirA === null || dirB === null) {
            continue
          }
          angle = junctionAngle(ea.poly, ea.isStart, eb.poly, eb.isStart)
          const followsTangents =
            endpointTouchesCurve(ea.poly, ea.isStart) ||
            endpointTouchesCurve(eb.poly, eb.isStart)
              ? splicedSeamFollowsTangents(
                  ea.poly,
                  ea.isStart,
                  eb.poly,
                  eb.isStart,
                  minAngle
                )
              : gapFollowsTangents(ea.point, dirA, eb.point, dirB, minAngle)
          if (angle === null || angle < minAngle || !followsTangents) {
            continue
          }
        }
        if (best === null || gap < best.gap) {
          best = {
            gap,
            idxA: ea.index,
            startA: ea.isStart,
            idxB: eb.index,
            startB: eb.isStart,
            angle
          }
        }
      }
    }

    if (best === null) {
      break
    }

    const labelA = working[best.idxA]
    const labelB = working[best.idxB]
    const polyA = openPolyline(labelA)
    const polyB = openPolyline(labelB)
    if (polyA === null || polyB === null) {
      break
    }

    const junction = endpoint(polyA, best.startA)
    const [vertices, types] = splice(
      polyA.vertices,
      String(polyA.types ?? ""),
      best.startA,
      polyB.vertices,
      String(polyB.types ?? ""),
      best.startB
    )
    polyA.vertices = vertices
    polyA.types = types

    absorbed.add(labelB)
    const connection: Connection = {
      keptId: String(labelA.id ?? ""),
      absorbedId: String(labelB.id ?? ""),
      category: String(labelA.category ?? ""),
      junction,
      gap: best.gap
    }
    if (best.angle !== null) {
      connection.angle = best.angle
    }
    result.connections.push(connection)
  }

  const survivors = working.filter((label) => !absorbed.has(label))
  result.labelsAfter = survivors.length
  return [survivors, result]
}
