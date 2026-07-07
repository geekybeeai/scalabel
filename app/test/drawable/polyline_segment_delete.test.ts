import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import {
  armSegmentDelete,
  getSegmentDeletePhase,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import {
  commitPendingSegmentDelete,
  handleSegmentDeletePick
} from "../../src/drawable/2d/polyline_segment_delete"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline with a fixed label id into item 0.
 *
 * @param labelId the label id to use
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

/**
 * Read a label's vertices as [x, y] pairs.
 *
 * @param labelId the label id
 */
function coords(labelId: string): number[][] {
  const pts = getShapes(getState(), 0, labelId) as PathPoint2DType[]
  return pts.map((p) => [p.x, p.y])
}

describe("segment delete", () => {
  beforeEach(() => {
    resetSegmentDelete()
  })

  test("middle delete: two survivors, atomic undo restores the original", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)).toBe("first-picked")
    expect(handleSegmentDeletePick({ x: 250, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")
    expect(getSegmentDeletePhase()).toBe("inactive")

    const ids = Object.keys(getState().task.items[0].labels)
    expect(ids).toHaveLength(2)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [50, 0]
    ])
    const rightId = ids.filter((id) => id !== "lineA")[0]
    expect(coords(rightId)).toEqual([
      [250, 0],
      [300, 0]
    ])
    expect(getState().task.items[0].labels.lineA.manual).toBe(true)
    expect(getState().task.items[0].labels[rightId].manual).toBe(true)

    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])
  })

  test("end trim: one survivor keeps the id; atomic undo restores", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])

    armSegmentDelete()
    // First pick near the LAST endpoint (a trim pick), second mid-line:
    expect(handleSegmentDeletePick({ x: 198, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")

    expect(Object.keys(getState().task.items[0].labels)).toEqual(["lineA"])
    expect(coords("lineA")).toEqual([
      [0, 0],
      [50, 0]
    ])

    expect(drawHistory.undo()).toBe(true)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
  })

  test("both ends: whole line deleted; undo restores it", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 2, y: 3 }, 20, 8)).toBe("first-picked")
    expect(handleSegmentDeletePick({ x: 98, y: 3 }, 20, 8)).toBe(
      "preview-started"
    )
    expect(commitPendingSegmentDelete()).toBe("deleted")

    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(0)
    expect(drawHistory.undo()).toBe(true)
    expect(coords("lineA")).toEqual([
      [0, 0],
      [100, 0]
    ])
  })

  test("second pick on a different polyline is rejected", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [200, 0]
    ])
    seedLine("lineB", [
      [0, 300],
      [200, 300]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 100, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 100, y: 303 }, 20, 8)).toBe(
      "wrong-line"
    )
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
  })

  test("coincident picks are rejected as too-close", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [200, 0]
    ])

    armSegmentDelete()
    expect(handleSegmentDeletePick({ x: 100, y: 3 }, 20, 8)).toBe(
      "first-picked"
    )
    expect(handleSegmentDeletePick({ x: 103, y: 2 }, 20, 8)).toBe("too-close")
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
  })

  test("commit is cancelled when the line changed since pick 1", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])

    armSegmentDelete()
    handleSegmentDeletePick({ x: 50, y: 3 }, 20, 8)
    handleSegmentDeletePick({ x: 250, y: 3 }, 20, 8)

    // The line is replaced behind the tool's back (like an undo would).
    Session.dispatch(action.deleteLabel(0, "lineA"))
    seedLine("lineA", [
      [0, 0],
      [500, 0]
    ])

    expect(commitPendingSegmentDelete()).toBe("stale")
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(coords("lineA")).toEqual([
      [0, 0],
      [500, 0]
    ])
  })
})
