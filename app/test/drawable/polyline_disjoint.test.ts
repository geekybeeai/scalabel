import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { performDisjoint } from "../../src/drawable/2d/polyline_disjoint"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/** Seed a closed joined-polyline ring into item 0. */
function seedClosedPolyline(): void {
  const vertices = [
    [0, 10],
    [10, 0],
    [20, 10],
    [10, 20]
  ]
  const shapes = vertices.map(([x, y]) =>
    makePathPoint2D({
      x,
      y,
      pointType: PathPointType.LINE,
      label: ["ring"]
    })
  )
  const label = makeLabel(
    {
      id: "ring",
      item: 0,
      type: LabelTypeName.POLYLINE_2D,
      closed: true,
      shapes: shapes.map((shape) => shape.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

describe("performDisjoint", () => {
  test("disconnects a closed polyline at an interior join", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedClosedPolyline()

    expect(performDisjoint({ x: 10, y: 0 }, 5)).toBe("disjointed")

    const state = getState()
    const ids = Object.keys(state.task.items[0].labels)
    expect(ids).toHaveLength(2)
    const lines = ids.map((id) => getShapes(state, 0, id) as PathPoint2DType[])
    expect(
      lines.map((line) => line.map((point) => [point.x, point.y]))
    ).toEqual(
      expect.arrayContaining([
        [
          [10, 20],
          [0, 10],
          [10, 0]
        ],
        [
          [10, 0],
          [20, 10],
          [10, 20]
        ]
      ])
    )
    expect(
      ids.every((id) => state.task.items[0].labels[id].closed !== true)
    ).toBe(true)
  })

  test.each([
    ["first", { x: 0, y: 10 }],
    ["last", { x: 10, y: 20 }]
  ])("disconnects a closed polyline at its %s corner", (_name, click) => {
    initializeTestingObjects()
    drawHistory.reset()
    seedClosedPolyline()

    expect(performDisjoint(click, 5)).toBe("disjointed")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(2)
  })
})
