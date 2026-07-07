import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { performCut } from "../../src/drawable/2d/polyline_cut"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline/polygon with a fixed label id into item 0.
 *
 * @param labelId the label id to use
 * @param vertices [x, y] pairs (all plain LINE vertices)
 * @param labelType label type name
 * @param category category indices (defaults to none)
 */
function seedLine(
  labelId: string,
  vertices: number[][],
  labelType: string = LabelTypeName.POLYLINE_2D,
  category: number[] = []
): void {
  const shapes = vertices.map(([x, y]) =>
    makePathPoint2D({ x, y, pointType: PathPointType.LINE, label: [labelId] })
  )
  const label = makeLabel(
    {
      id: labelId,
      item: 0,
      type: labelType,
      category,
      shapes: shapes.map((s) => s.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

describe("performCut", () => {
  test("cuts the nearest open polyline into two, undo restores it", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0],
      [200, 0]
    ])

    expect(performCut({ x: 50, y: 3 }, 20, 8)).toBe("cut")

    const state = getState()
    const ids = Object.keys(state.task.items[0].labels)
    expect(ids).toHaveLength(2)
    const halfA = getShapes(state, 0, "lineA") as PathPoint2DType[]
    expect(halfA.map((p) => [p.x, p.y])).toEqual([
      [0, 0],
      [50, 0]
    ])
    const newId = ids.filter((id) => id !== "lineA")[0]
    const halfB = getShapes(state, 0, newId) as PathPoint2DType[]
    expect(halfB.map((p) => [p.x, p.y])).toEqual([
      [50, 0],
      [100, 0],
      [200, 0]
    ])
    // Category/type inherited
    expect(state.task.items[0].labels[newId].type).toBe(
      LabelTypeName.POLYLINE_2D
    )
    expect(state.task.items[0].labels[newId].manual).toBe(true)

    // Atomic undo
    expect(drawHistory.undo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(getShapes(getState(), 0, "lineA")).toHaveLength(3)
  })

  test("returns miss when nothing is within the radius", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(performCut({ x: 50, y: 500 }, 20, 8)).toBe("miss")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns near-endpoint next to a line end", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine("lineA", [
      [0, 0],
      [100, 0]
    ])
    expect(performCut({ x: 2, y: 3 }, 20, 8)).toBe("near-endpoint")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns closed when the nearest shape is a polygon", () => {
    initializeTestingObjects()
    drawHistory.reset()
    seedLine(
      "polyA",
      [
        [0, 0],
        [100, 0],
        [100, 100],
        [0, 100]
      ],
      LabelTypeName.POLYGON_2D
    )
    expect(performCut({ x: 50, y: 3 }, 20, 8)).toBe("closed")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("returns curve when the nearest span is bezier", () => {
    initializeTestingObjects()
    drawHistory.reset()
    const shapes = [
      makePathPoint2D({
        x: 0,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 30,
        y: 10,
        pointType: PathPointType.CURVE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 60,
        y: 10,
        pointType: PathPointType.CURVE,
        label: ["curvy"]
      }),
      makePathPoint2D({
        x: 100,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["curvy"]
      })
    ]
    const label = makeLabel(
      {
        id: "curvy",
        item: 0,
        type: LabelTypeName.POLYLINE_2D,
        shapes: shapes.map((s) => s.id)
      },
      false
    )
    Session.dispatch(action.addLabel(0, label, shapes))
    expect(performCut({ x: 45, y: 12 }, 20, 8)).toBe("curve")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
  })

  test("marks both halves manual after cutting a prediction", () => {
    initializeTestingObjects()
    drawHistory.reset()
    const shapes = [
      makePathPoint2D({
        x: 0,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["predLine"]
      }),
      makePathPoint2D({
        x: 100,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["predLine"]
      }),
      makePathPoint2D({
        x: 200,
        y: 0,
        pointType: PathPointType.LINE,
        label: ["predLine"]
      })
    ]
    const label = makeLabel(
      {
        id: "predLine",
        item: 0,
        type: LabelTypeName.POLYLINE_2D,
        shapes: shapes.map((s) => s.id),
        manual: false
      },
      false
    )
    Session.dispatch(action.addLabel(0, label, shapes))

    expect(performCut({ x: 50, y: 3 }, 20, 8)).toBe("cut")

    const state = getState()
    const ids = Object.keys(state.task.items[0].labels)
    expect(ids).toHaveLength(2)
    expect(state.task.items[0].labels.predLine.manual).toBe(true)
    const newId = ids.filter((id) => id !== "predLine")[0]
    expect(state.task.items[0].labels[newId].manual).toBe(true)
  })

  test("does not cut a hidden polyline", () => {
    initializeTestingObjects()
    drawHistory.reset()
    // Category 1 stands in for a sidebar-hidden category; the visibility
    // filter below mirrors a user toggling it off.
    seedLine(
      "lineA",
      [
        [0, 0],
        [100, 0],
        [200, 0]
      ],
      LabelTypeName.POLYLINE_2D,
      [1]
    )
    expect(
      performCut({ x: 50, y: 3 }, 20, 8, {
        hideLabels: false,
        hiddenLabelTypes: [],
        hiddenCategories: [1]
      })
    ).toBe("miss")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(1)
    expect(getShapes(getState(), 0, "lineA")).toHaveLength(3)
  })
})
