import * as action from "../../src/action/common"
import { clearMarked, getMarked } from "../../src/common/multi_delete_state"
import Session from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { runFreeformSelect } from "../../src/drawable/2d/freeform_select"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id
 * @param vertices [x, y] pairs (plain LINE vertices)
 */
function seedLine(labelId: string, vertices: number[][]): void {
  const shapes = vertices.map(([x, y]) =>
    makePathPoint2D({ x, y, pointType: PathPointType.LINE, label: [labelId] })
  )
  const label = makeLabel(
    {
      id: labelId,
      item: 0,
      type: LabelTypeName.POLYLINE_2D,
      shapes: shapes.map((s) => s.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

describe("freeform select orchestrator", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("marks only fully-enclosed lines, ignores crossing and outside", () => {
    initializeTestingObjects()
    seedLine("inside", [
      [10, 10],
      [20, 20]
    ])
    seedLine("crossing", [
      [30, 30],
      [200, 200]
    ])
    seedLine("outside", [
      [500, 500],
      [600, 600]
    ])
    const lasso = [
      { x: 0, y: 0 },
      { x: 50, y: 0 },
      { x: 50, y: 50 },
      { x: 0, y: 50 }
    ]
    const count = runFreeformSelect(lasso, {
      hideLabels: false,
      hiddenLabelTypes: [],
      hiddenCategories: []
    })
    expect(count).toBe(1)
    expect(getMarked()).toEqual(["inside"])
  })
})
