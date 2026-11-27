import * as action from "../../src/action/common"
import { setCutMode } from "../../src/common/cut_state"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { LabelTypeName } from "../../src/const/common"
import { performCurveCut } from "../../src/drawable/2d/polyline_curve_cut"
import {
  confirmClosedCut,
  performCut
} from "../../src/drawable/2d/polyline_cut"
import { getShapes } from "../../src/functional/state_util"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { convertLabelsToExport } from "../../src/server/export"
import { PathPoint2DType, PathPointType } from "../../src/types/state"
import { initializeTestingObjects } from "./util"

/**
 * Seed a polyline/polygon with a fixed label id into item 0.
 *
 * @param labelId the label id to use
 * @param vertices [x, y] pairs (all plain LINE vertices)
 * @param labelType label type name
 * @param category category indices (defaults to none)
 * @param closed whether the polyline is a closed outline
 */
function seedLine(
  labelId: string,
  vertices: number[][],
  labelType: string = LabelTypeName.POLYLINE_2D,
  category: number[] = [],
  closed?: boolean
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
      closed,
      shapes: shapes.map((s) => s.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

test("divides the implicit closing edge without opening the outline", () => {
  initializeTestingObjects()
  drawHistory.reset()
  seedLine(
    "median",
    [
      [0, 0],
      [100, 0],
      [100, 50],
      [0, 50]
    ],
    LabelTypeName.POLYLINE_2D,
    [1],
    true
  )

  expect(performCurveCut({ x: 0, y: 25 }, 5, 8)).toBe("cut")
  const item = getState().task.items[0]
  expect(Object.keys(item.labels)).toEqual(["median"])
  expect(item.labels.median.closed).toBe(true)
  const points = getShapes(getState(), 0, "median") as PathPoint2DType[]
  expect(points.map(({ x, y }) => [x, y])).toEqual([
    [0, 0],
    [100, 0],
    [100, 50],
    [0, 50],
    [0, 25]
  ])
})

describe("performCut", () => {
  afterEach(() => setCutMode(false))

  test("accepts distinct cuts on opposite sides of a narrow median", () => {
    initializeTestingObjects()
    drawHistory.reset()
    setCutMode(true)
    seedLine(
      "median",
      [
        [0, 0],
        [200, 0],
        [200, 4],
        [0, 4]
      ],
      LabelTypeName.POLYLINE_2D,
      [1],
      true
    )

    expect(performCut({ x: 100, y: 0 }, 20, 8)).toBe("first-picked")
    expect(performCut({ x: 100, y: 4 }, 20, 8)).toBe("cut")
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(2)
  })

  test("opens a closed polyline at one pick when confirmed", () => {
    initializeTestingObjects()
    drawHistory.reset()
    setCutMode(true)
    seedLine(
      "median",
      [
        [0, 0],
        [100, 0],
        [100, 50],
        [0, 50],
        [0, 0]
      ],
      LabelTypeName.POLYLINE_2D,
      [1],
      true
    )

    expect(performCut({ x: 0, y: 25 }, 20, 8, undefined, 1)).toBe(
      "first-picked"
    )
    expect(performCut({ x: 0, y: 25 }, 20, 8, undefined, 1)).toBe(
      "near-endpoint"
    )
    expect(
      confirmClosedCut(
        {
          hideLabels: true,
          hiddenLabelTypes: [],
          hiddenCategories: []
        },
        2
      )
    ).toBe("miss")
    expect(getState().task.items[0].labels.median.closed).toBe(true)
    expect(confirmClosedCut(undefined, 1)).toBe("cut")

    const { config, items } = getState().task
    const exported = convertLabelsToExport(
      items[0].labels,
      items[0].shapes,
      config
    )
    expect(exported).toHaveLength(1)
    expect(exported[0].poly2d).toEqual([
      {
        vertices: [
          [0, 25],
          [0, 0],
          [100, 0],
          [100, 50],
          [0, 50],
          [0, 25]
        ],
        types: "LLLLLL",
        closed: false
      }
    ])
    expect(drawHistory.undo()).toBe(true)
    expect(getState().task.items[0].labels.median.closed).toBe(true)
  })

  test("splits a closed median at two curved points and exports two open lines", () => {
    initializeTestingObjects()
    drawHistory.reset()
    setCutMode(true)
    const initial = [
      [0, 0],
      [100, 0],
      [130, 0],
      [130, 50],
      [100, 50],
      [0, 50],
      [-30, 50],
      [-30, 0]
    ]
    const shapes = initial.map(([x, y], index) =>
      makePathPoint2D({
        x,
        y,
        pointType:
          "LLCCLLCC"[index] === "C" ? PathPointType.CURVE : PathPointType.LINE,
        label: ["median"]
      })
    )
    const label = makeLabel(
      {
        id: "median",
        item: 0,
        type: LabelTypeName.POLYLINE_2D,
        category: [1],
        closed: true,
        shapes: shapes.map((shape) => shape.id)
      },
      false
    )
    Session.dispatch(action.addLabel(0, label, shapes))

    expect(performCut({ x: 122.5, y: 25 }, 20, 8)).toBe("first-picked")
    expect(getState().task.items[0].labels.median.closed).toBe(true)
    expect(performCut({ x: -22.5, y: 25 }, 20, 8)).toBe("cut")

    const { config, items } = getState().task
    const exported = convertLabelsToExport(
      items[0].labels,
      items[0].shapes,
      config
    )
    expect(exported).toHaveLength(2)
    for (const piece of exported) {
      expect(piece.category).toBe(config.categories[1])
      expect(piece.manualShape).toBe(true)
      expect(piece.poly2d?.[0]).toMatchObject({
        closed: false,
        types: "LCCLLCCL"
      })
    }
    const [first, second] = exported.map((piece) => piece.poly2d![0].vertices)
    expect(first[0]).toEqual(second[second.length - 1])
    expect(first[first.length - 1]).toEqual(second[0])
    expect([first[0], second[0]]).toEqual([
      [122.5, 25],
      [-22.5, 25]
    ])
    expect(drawHistory.undo()).toBe(true)
    expect(getState().task.items[0].labels.median.closed).toBe(true)
    const restored = getShapes(getState(), 0, "median") as PathPoint2DType[]
    expect(restored.map((p) => [p.x, p.y])).toEqual(initial)
    expect(drawHistory.redo()).toBe(true)
    expect(Object.keys(getState().task.items[0].labels)).toHaveLength(2)
  })

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
