import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import {
  clearMarked,
  getMarked,
  toggleMarked
} from "../../src/common/multi_delete_state"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { commitMarkedDelete } from "../../src/drawable/2d/multi_delete"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id
 * @param vertices [x, y] pairs (all plain LINE vertices)
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

describe("multi delete", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("deletes every marked line and clears the set", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    seedLine("lineB", [
      [0, 50],
      [100, 50]
    ])

    toggleMarked("lineA")
    toggleMarked("lineB")
    expect(getMarked().sort()).toEqual(["lineA", "lineB"])

    expect(commitMarkedDelete()).toBe("deleted")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)
    expect(getMarked()).toHaveLength(0)
  })

  test("undo restores the deleted lines one at a time", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    seedLine("lineB", [
      [0, 50],
      [100, 50]
    ])
    toggleMarked("lineA")
    toggleMarked("lineB")

    expect(commitMarkedDelete()).toBe("deleted")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)

    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(2)
  })

  test("ignores an empty set and leaves labels untouched", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(commitMarkedDelete()).toBe("ignored")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })
})
