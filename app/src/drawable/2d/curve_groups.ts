import { PathPointType } from "../../types/state"

/**
 * Find the bezier groups in a drawable point-type sequence.
 *
 * A group is the quadruple [anchorA, c1, c2, anchorB]: two consecutive
 * CURVE control points bounded by LINE anchors. MID points only occur on
 * straight spans and never neighbor a control point. Closed shapes may
 * contain one group that wraps the array end (returned with modular
 * indices); open lines never wrap. Malformed data (a stray single CURVE,
 * or a control pair without LINE anchors) yields no group — defensive,
 * never throws.
 *
 * @param types the point types of a drawable's points, in order
 * @param closed whether the shape is closed (allows wraparound)
 */
export function curveGroupIndices(
  types: readonly PathPointType[],
  closed: boolean = false
): number[][] {
  const n = types.length
  const groups: number[][] = []
  if (n < 4) {
    return groups
  }
  let i = 0
  while (i < n) {
    if (types[i] === PathPointType.CURVE) {
      const inBounds = closed || (i - 1 >= 0 && i + 2 < n)
      const a = (i - 1 + n) % n
      const c2 = (i + 1) % n
      const b = (i + 2) % n
      if (
        inBounds &&
        types[c2] === PathPointType.CURVE &&
        types[a] === PathPointType.LINE &&
        types[b] === PathPointType.LINE
      ) {
        groups.push([a, i, c2, b])
        i += 3
        continue
      }
    }
    i++
  }
  return groups
}
