import _ from "lodash"

import * as action from "../../src/action/common"
import { drawHistory, LineSnapshot } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id to use
 * @param vertices [x, y] pairs
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

/**
 * Snapshot a line from the current state.
 *
 * @param labelId the label id to snapshot
 */
function snapshot(labelId: string): LineSnapshot {
  const state = getState()
  return {
    label: _.cloneDeep(state.task.items[0].labels[labelId]),
    shapes: _.cloneDeep(getShapes(state, 0, labelId))
  }
}

describe("DrawHistory cut command", () => {
  test("one undo restores the original; one redo re-applies the cut", () => {
    initializeTestingObjects()
    drawHistory.reset()

    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])
    const before = snapshot("lineA")

    // Apply the cut by hand (exactly what performCut dispatches):
    // truncate A to its first half, add B as the second half.
    const shapesA = [
      makePathPoint2D({
        x: 0,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineA"]
      }),
      makePathPoint2D({
        x: 50,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineA"]
      })
    ]
    const labelA = _.cloneDeep(before.label)
    labelA.shapes = shapesA.map((s) => s.id)
    const shapesB = [
      makePathPoint2D({
        x: 50,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      }),
      makePathPoint2D({
        x: 100,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      }),
      makePathPoint2D({
        x: 200,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["lineB"]
      })
    ]
    const labelB = _.cloneDeep(before.label)
    labelB.id = "lineB"
    labelB.shapes = shapesB.map((s) => s.id)
    Session.dispatch(
      action.makeSequential([
        action.deleteLabel(0, "lineA"),
        action.addLabel(0, labelA, shapesA),
        action.addLabel(0, labelB, shapesB)
      ])
    )
    drawHistory.recordCut(
      0,
      "lineA",
      before,
      snapshot("lineA"),
      snapshot("lineB")
    )

    expect(getShapes(getState(), 0, "lineA")).toHaveLength(2)
    expect(getShapes(getState(), 0, "lineB")).toHaveLength(3)

    // One undo: B gone, A back to its 3 original vertices.
    expect(drawHistory.undo()).toBe(true)
    expect(getState().task.items[0].labels.lineB).toBeUndefined()
    const restored = getShapes(getState(), 0, "lineA") as PathPoint2DType[]
    expect(restored).toHaveLength(3)
    expect(restored.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
    expect(drawHistory.canRedo()).toBe(true)

    // One redo: the cut is back.
    expect(drawHistory.redo()).toBe(true)
    expect(getShapes(getState(), 0, "lineA")).toHaveLength(2)
    expect(getShapes(getState(), 0, "lineB")).toHaveLength(3)
  })
})
