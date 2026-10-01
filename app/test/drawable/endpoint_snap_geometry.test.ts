/** @jest-environment node */

import {
  nearestSnapPointIndex,
  snapTargetIndices
} from "../../src/drawable/2d/endpoint_snap_geometry"
import { PathPointType } from "../../src/types/state"

describe("endpoint snap target geometry", () => {
  test("closed polygon vertices can be searched as snap targets", () => {
    expect(
      nearestSnapPointIndex(
        [
          { x: 0, y: 0 },
          { x: 100, y: 0 },
          { x: 100, y: 100 },
          { x: 0, y: 100 }
        ],
        { x: 101, y: 99 },
        15
      )
    ).toBe(2)
  })

  test("returns null when every vertex is outside the snap radius", () => {
    expect(
      nearestSnapPointIndex(
        [
          { x: 0, y: 0 },
          { x: 100, y: 0 }
        ],
        { x: 50, y: 20 },
        15
      )
    ).toBeNull()
  })

  test("includes midpoint handles when collecting closed-shape snap targets", () => {
    expect(
      snapTargetIndices([
        { x: 0, y: 0, type: PathPointType.LINE },
        { x: 50, y: 0, type: PathPointType.MID },
        { x: 100, y: 0, type: PathPointType.LINE }
      ])
    ).toEqual([0, 1, 2])
  })
})
