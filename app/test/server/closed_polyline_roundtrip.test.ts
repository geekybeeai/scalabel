/** @jest-environment node */
import { LabelTypeName } from "../../src/const/common"
import { makePathPoint2D } from "../../src/functional/states"
import { convertPolygonToExport } from "../../src/server/export"
import { convertItemToImport } from "../../src/server/import"
import { ItemExport } from "../../src/types/export"
import { PathPointType } from "../../src/types/state"

const square = [
  [0, 0],
  [100, 0],
  [100, 100],
  [0, 100]
].map(([x, y]) => makePathPoint2D({ x, y, pointType: PathPointType.LINE }))

/**
 * Import one frame holding a single poly2d label.
 *
 * @param closed the exported closed flag
 * @param labelTypes the project's label types
 */
function importOne(
  closed: boolean,
  labelTypes: string[]
): { type: string; closed?: boolean; points: number } {
  const frame: Partial<ItemExport> = {
    name: "f.png",
    url: "f.png",
    videoName: "",
    timestamp: 0,
    attributes: {},
    sensor: -1,
    labels: [
      {
        id: "sq",
        category: "lane",
        attributes: {},
        manualShape: true,
        box2d: null,
        box3d: null,
        poly2d: [
          {
            vertices: [
              [0, 0],
              [100, 0],
              [100, 100],
              [0, 100]
            ],
            types: "LLLL",
            closed
          }
        ]
      }
    ]
  }
  const item = convertItemToImport(
    "",
    0,
    { [-1]: frame },
    0,
    0,
    {},
    {},
    { lane: 0 },
    false,
    labelTypes
  )
  const label = Object.values(item.labels)[0]
  return { type: label.type, closed: label.closed, points: label.shapes.length }
}

describe("closed polylines survive export and re-import", () => {
  test("export marks a user-closed polyline as closed", () => {
    const [open] = convertPolygonToExport(square, LabelTypeName.POLYLINE_2D)
    expect(open.closed).toBe(false)
    const [shut] = convertPolygonToExport(
      square,
      LabelTypeName.POLYLINE_2D,
      true
    )
    expect(shut.closed).toBe(true)
    const [poly] = convertPolygonToExport(square, LabelTypeName.POLYGON_2D)
    expect(poly.closed).toBe(true)
  })

  test("a polyline project imports a closed shape as a closed polyline", () => {
    expect(importOne(true, [LabelTypeName.POLYLINE_2D])).toEqual({
      type: LabelTypeName.POLYLINE_2D,
      closed: true,
      points: 4
    })
  })

  test("open shapes stay open polylines", () => {
    const got = importOne(false, [LabelTypeName.POLYLINE_2D])
    expect(got.type).toBe(LabelTypeName.POLYLINE_2D)
    expect(got.closed).toBeUndefined()
  })

  test("a polygon project still imports closed shapes as polygons", () => {
    const got = importOne(true, [LabelTypeName.POLYGON_2D])
    expect(got.type).toBe(LabelTypeName.POLYGON_2D)
    expect(got.closed).toBeUndefined()
  })
})
