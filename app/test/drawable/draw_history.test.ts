import * as action from "../../src/action/common"
import Session, { getState } from "../../src/common/session"
import { getNumLabels } from "../../src/functional/state_util"
import { Size2D } from "../../src/math/size2d"
import { initializeTestingObjects, mouseMoveClick } from "./util"

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

/** Select the polygon label type (index 1 in the test config) */
function dispatchPolygonMode(): void {
  Session.dispatch(action.changeSelect({ labelType: 1 }))
}
