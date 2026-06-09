import * as action from "../../src/action/common"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import { getNumLabels } from "../../src/functional/state_util"
import { Size2D } from "../../src/math/size2d"
import { drawPolygon, initializeTestingObjects, mouseMoveClick } from "./util"

describe("Drawing-state helpers", () => {
  test("isDrawingInProgress and cancelDrawing", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    dispatchPolygonMode()

    // Place two vertices but do NOT finish the polygon
    mouseMoveClick(label2dHandler, 10, 10, canvasSize, -1, 0)
    mouseMoveClick(label2dHandler, 100, 100, canvasSize, -1, 0)

    expect(Session.label2dList.isDrawingInProgress()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    Session.label2dList.cancelDrawing()

    expect(Session.label2dList.isDrawingInProgress()).toBe(false)
    expect(Session.label2dList.labelList.length).toEqual(0)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
  })
})

describe("DrawHistory undo/redo", () => {
  const polyA: number[][] = [
    [10, 10],
    [100, 100],
    [200, 100],
    [100, 0]
  ]
  const polyB: number[][] = [
    [500, 500],
    [600, 400],
    [700, 700]
  ]

  test("undo removes last completed polyline, redo restores it", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    drawPolygon(label2dHandler, canvasSize, polyA)
    drawPolygon(label2dHandler, canvasSize, polyB)
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)

    expect(drawHistory.undo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)

    expect(drawHistory.redo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(2)
  })

  test("undo with nothing to undo returns false", () => {
    initializeTestingObjects()
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))
    expect(drawHistory.undo()).toBe(false)
  })

  test("redo with empty stack returns false", () => {
    initializeTestingObjects()
    drawHistory.clearRedo()
    expect(drawHistory.redo()).toBe(false)
  })

  test("undo cancels an in-progress drawing (not redoable)", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    // Start drawing but do not finish
    mouseMoveClick(label2dHandler, 10, 10, canvasSize, -1, 0)
    mouseMoveClick(label2dHandler, 100, 100, canvasSize, -1, 0)
    expect(Session.label2dList.isDrawingInProgress()).toBe(true)

    expect(drawHistory.undo()).toBe(true)
    expect(Session.label2dList.isDrawingInProgress()).toBe(false)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
    // Cancelling an unfinished shape is not redoable
    expect(drawHistory.redo()).toBe(false)
  })

  test("clearRedo invalidates a pending redo", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))

    drawPolygon(label2dHandler, canvasSize, polyA)
    expect(drawHistory.undo()).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    drawHistory.clearRedo()
    expect(drawHistory.redo()).toBe(false)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)
  })
})

describe("DrawHistory.handleKeyboard", () => {
  test("Ctrl+Z undoes, Ctrl+Y redoes, plain keys ignored", () => {
    const itemIndex = 0
    const [label2dHandler] = initializeTestingObjects()
    const canvasSize = new Size2D(1000, 1000)
    drawHistory.clearRedo()
    Session.dispatch(action.changeSelect({ labelType: 1 }))
    drawPolygon(label2dHandler, canvasSize, [
      [10, 10],
      [100, 100],
      [200, 100],
      [100, 0]
    ])
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)

    // Plain "z" without modifier is ignored
    expect(
      drawHistory.handleKeyboard(new KeyboardEvent("keydown", { key: "z" }))
    ).toBe(false)

    // Ctrl+Z undoes
    expect(
      drawHistory.handleKeyboard(
        new KeyboardEvent("keydown", { key: "z", ctrlKey: true })
      )
    ).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(0)

    // Ctrl+Y redoes
    expect(
      drawHistory.handleKeyboard(
        new KeyboardEvent("keydown", { key: "y", ctrlKey: true })
      )
    ).toBe(true)
    expect(getNumLabels(getState(), itemIndex)).toEqual(1)
  })
})

/** Select the polygon label type (index 1 in the test config) */
function dispatchPolygonMode(): void {
  Session.dispatch(action.changeSelect({ labelType: 1 }))
}
