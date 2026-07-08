import { curveGroupIndices } from "../../src/drawable/2d/curve_groups"
import { PathPointType } from "../../src/types/state"

const L = PathPointType.LINE
const C = PathPointType.CURVE
const M = PathPointType.MID

describe("curveGroupIndices", () => {
  test("no curves -> no groups", () => {
    expect(curveGroupIndices([L, M, L, M, L])).toEqual([])
    expect(curveGroupIndices([])).toEqual([])
    expect(curveGroupIndices([L, L])).toEqual([])
  })

  test("one group", () => {
    expect(curveGroupIndices([L, C, C, L])).toEqual([[0, 1, 2, 3]])
  })

  test("group surrounded by straight spans with MID points", () => {
    expect(curveGroupIndices([L, M, L, C, C, L, M, L])).toEqual([[2, 3, 4, 5]])
  })

  test("two groups sharing an anchor", () => {
    expect(curveGroupIndices([L, C, C, L, C, C, L])).toEqual([
      [0, 1, 2, 3],
      [3, 4, 5, 6]
    ])
  })

  test("groups at the start and end of an open line", () => {
    expect(curveGroupIndices([L, C, C, L, M, L, C, C, L])).toEqual([
      [0, 1, 2, 3],
      [5, 6, 7, 8]
    ])
  })

  test("stray single CURVE point is ignored (defensive)", () => {
    expect(curveGroupIndices([L, C, L, M, L])).toEqual([])
  })

  test("closed shape: group wrapping the array end", () => {
    // C2 at 0 and C1 at 4 belong to a group anchored at 3 (A) and 1 (B).
    expect(curveGroupIndices([C, L, M, L, C], true)).toEqual([[3, 4, 0, 1]])
  })

  test("open line never wraps", () => {
    expect(curveGroupIndices([C, L, M, L, C], false)).toEqual([])
  })
})
