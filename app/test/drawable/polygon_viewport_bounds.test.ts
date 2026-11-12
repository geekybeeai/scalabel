/** @jest-environment node */

import * as action from "../../src/action/common"
import { configureStore } from "../../src/common/configure_store"
import Session, { dispatch } from "../../src/common/session"
import { Key, LabelTypeName } from "../../src/const/common"
import { Label2DHandler } from "../../src/drawable/2d/label2d_handler"
import { Label2DList } from "../../src/drawable/2d/label2d_list"
import { Context2D } from "../../src/drawable/util"
import { makeImageViewerConfig } from "../../src/functional/states"
import { Size2D } from "../../src/math/size2d"
import { Vector2D } from "../../src/math/vector2d"
import { testJson } from "../test_states/test_image_objects"

const CANVAS_SIZE = new Size2D(1000, 1000)
const VIEW_CONTEXT: Context2D = Object.create(null)
const CONTROL_CONTEXT: Context2D = Object.create(null)
let originalWindowDescriptor: PropertyDescriptor | undefined
let originalDocumentDescriptor: PropertyDescriptor | undefined

/**
 * Resolve a test fixture tool by name so fixture reordering stays safe.
 *
 * @param type label tool name
 */
function labelTypeIndex(type: LabelTypeName): number {
  const labelTypes = testJson.task?.config?.labelTypes
  if (labelTypes === undefined) {
    throw new Error("Test fixture does not define label types")
  }
  const index = labelTypes.indexOf(type)
  if (index < 0) {
    throw new Error(`Test fixture does not contain label type: ${type}`)
  }
  return index
}

const POLYGON_LABEL_TYPE = labelTypeIndex(LabelTypeName.POLYGON_2D)
const POLYLINE_LABEL_TYPE = labelTypeIndex(LabelTypeName.POLYLINE_2D)

interface BoundsCase {
  name: string
  labelType: number
  start: [number, number]
  cursor: [number, number]
  expected: [number, number, number, number]
}

const BOUNDS_CASES: BoundsCase[] = [
  {
    name: "polyline down-right",
    labelType: POLYLINE_LABEL_TYPE,
    start: [100, 120],
    cursor: [700, 620],
    expected: [100, 120, 600, 500]
  },
  {
    name: "polyline up-left",
    labelType: POLYLINE_LABEL_TYPE,
    start: [700, 620],
    cursor: [100, 120],
    expected: [100, 120, 600, 500]
  },
  {
    name: "polygon down-right",
    labelType: POLYGON_LABEL_TYPE,
    start: [100, 120],
    cursor: [700, 620],
    expected: [100, 120, 600, 500]
  }
]

/** Initialize a fresh store, label list, and handler for one test. */
function initializeTestingObjects(): Label2DHandler {
  Session.label2dList = new Label2DList()
  Session.store = configureStore({})
  dispatch(action.updateState(testJson))
  dispatch(action.initSessionAction())
  dispatch(action.addViewerConfig(1, makeImageViewerConfig(0)))

  const label2dHandler = new Label2DHandler(Session.label2dList)
  Session.subscribe(() => {
    const state = Session.getState()
    Session.label2dList.updateState(state)
    label2dHandler.updateState(state)
  })

  dispatch(action.loadItem(0, -1))
  dispatch(action.goToItem(0))
  return label2dHandler
}

/**
 * Forward a mouse move using the shared test canvas size.
 *
 * @param label2dHandler drawable event handler
 * @param x image-space x coordinate
 * @param y image-space y coordinate
 * @param labelIndex hit label index
 * @param handleIndex hit handle index
 */
function mouseMove(
  label2dHandler: Label2DHandler,
  x: number,
  y: number,
  labelIndex: number,
  handleIndex: number
): void {
  label2dHandler.onMouseMove(
    new Vector2D(x, y),
    CANVAS_SIZE,
    labelIndex,
    handleIndex
  )
}

/**
 * Forward a mouse down at an image-space coordinate.
 *
 * @param label2dHandler drawable event handler
 * @param x image-space x coordinate
 * @param y image-space y coordinate
 * @param labelIndex hit label index
 * @param handleIndex hit handle index
 */
function mouseDown(
  label2dHandler: Label2DHandler,
  x: number,
  y: number,
  labelIndex: number,
  handleIndex: number
): void {
  label2dHandler.onMouseDown(new Vector2D(x, y), labelIndex, handleIndex)
}

/**
 * Move to a coordinate and complete one click there.
 *
 * @param label2dHandler drawable event handler
 * @param x image-space x coordinate
 * @param y image-space y coordinate
 * @param labelIndex hit label index
 * @param handleIndex hit handle index
 */
function mouseMoveClick(
  label2dHandler: Label2DHandler,
  x: number,
  y: number,
  labelIndex: number,
  handleIndex: number
): void {
  mouseMove(label2dHandler, x, y, labelIndex, handleIndex)
  mouseDown(label2dHandler, x, y, labelIndex, handleIndex)
  label2dHandler.onMouseUp(new Vector2D(x, y), labelIndex, handleIndex)
}

/**
 * Redraw visible labels using the shared high-zoom culling test defaults.
 *
 * @param viewScale current viewer zoom
 * @param viewportBounds visible image-space viewport
 */
function redrawAt(
  viewScale: number,
  viewportBounds: [number, number, number, number]
): void {
  Session.label2dList.redraw(
    VIEW_CONTEXT,
    CONTROL_CONTEXT,
    1,
    false,
    false,
    undefined,
    viewScale,
    viewportBounds,
    undefined,
    undefined,
    false
  )
}

beforeAll(() => {
  originalWindowDescriptor = Object.getOwnPropertyDescriptor(global, "window")
  originalDocumentDescriptor = Object.getOwnPropertyDescriptor(
    global,
    "document"
  )
  Object.defineProperty(global, "window", {
    configurable: true,
    value: { addEventListener: jest.fn() }
  })
  Object.defineProperty(global, "document", {
    configurable: true,
    value: { hidden: false }
  })
})

afterAll(() => {
  if (originalWindowDescriptor === undefined) {
    Reflect.deleteProperty(global, "window")
  } else {
    Object.defineProperty(global, "window", originalWindowDescriptor)
  }
  if (originalDocumentDescriptor === undefined) {
    Reflect.deleteProperty(global, "document")
  } else {
    Object.defineProperty(global, "document", originalDocumentDescriptor)
  }
})

afterEach(() => {
  jest.restoreAllMocks()
})

for (const { name, labelType, start, cursor, expected } of BOUNDS_CASES) {
  test(`in-progress ${name} bounds include the live cursor endpoint`, () => {
    const label2dHandler = initializeTestingObjects()
    dispatch(action.changeSelect({ labelType }))

    mouseMoveClick(label2dHandler, start[0], start[1], -1, 0)
    mouseMove(label2dHandler, cursor[0], cursor[1], -1, 0)

    expect(Session.label2dList.isDrawingInProgress()).toBe(true)
    expect(Session.label2dList.selectedLabels[0].bounds()).toEqual(expected)
  })
}

test("polygon bounds stay unknown until the first vertex is committed", () => {
  const label2dHandler = initializeTestingObjects()
  dispatch(action.changeSelect({ labelType: POLYGON_LABEL_TYPE }))

  mouseDown(label2dHandler, 100, 100, -1, 0)

  const active = Session.label2dList.selectedLabels[0]
  expect(active.type).toBe(LabelTypeName.POLYGON_2D)
  expect(active.bounds()).toBeNull()
})

test("high-zoom culling draws a live segment that crosses the viewport", () => {
  const label2dHandler = initializeTestingObjects()
  dispatch(action.changeSelect({ labelType: POLYLINE_LABEL_TYPE }))
  mouseMoveClick(label2dHandler, 100, 500, -1, 0)
  mouseMove(label2dHandler, 900, 500, -1, 0)

  const active = Session.label2dList.selectedLabels[0]
  const drawSpy = jest.spyOn(active, "draw").mockImplementation(() => undefined)

  redrawAt(3, [450, 450, 100, 100])

  expect(drawSpy).toHaveBeenCalledTimes(1)
})

test("high-zoom culling draws a multi-vertex polygon whose closing preview edge crosses the viewport", () => {
  const label2dHandler = initializeTestingObjects()
  dispatch(action.changeSelect({ labelType: POLYGON_LABEL_TYPE }))
  mouseMoveClick(label2dHandler, 100, 500, -1, 0)
  mouseMoveClick(label2dHandler, 100, 100, -1, 0)
  mouseMoveClick(label2dHandler, 200, 900, -1, 0)
  mouseMove(label2dHandler, 900, 500, -1, 0)

  const active = Session.label2dList.selectedLabels[0]
  expect(active.type).toBe(LabelTypeName.POLYGON_2D)
  expect(Session.label2dList.isDrawingInProgress()).toBe(true)
  const drawSpy = jest.spyOn(active, "draw").mockImplementation(() => undefined)

  redrawAt(3, [450, 450, 100, 100])

  expect(drawSpy).toHaveBeenCalledTimes(1)
})

test("finished off-screen polylines keep the existing zoom culling threshold", () => {
  const label2dHandler = initializeTestingObjects()
  dispatch(action.changeSelect({ labelType: POLYLINE_LABEL_TYPE }))
  mouseMoveClick(label2dHandler, 100, 100, -1, 0)
  mouseMoveClick(label2dHandler, 200, 200, -1, 0)
  mouseMove(label2dHandler, 900, 900, -1, 0)

  const finished = Session.label2dList.selectedLabels[0]
  finished.onKeyDown(Key.ENTER)

  expect(Session.label2dList.isDrawingInProgress()).toBe(false)
  expect(finished.bounds()).toEqual([100, 100, 100, 100])

  const drawSpy = jest
    .spyOn(finished, "draw")
    .mockImplementation(() => undefined)

  redrawAt(3, [800, 800, 100, 100])
  expect(drawSpy).not.toHaveBeenCalled()

  drawSpy.mockClear()
  redrawAt(2, [800, 800, 100, 100])
  expect(drawSpy).toHaveBeenCalledTimes(1)
})

test("editing alone does not bypass high-zoom culling for a committed off-screen polyline", () => {
  const label2dHandler = initializeTestingObjects()
  dispatch(action.changeSelect({ labelType: POLYLINE_LABEL_TYPE }))
  mouseMoveClick(label2dHandler, 100, 100, -1, 0)
  mouseMoveClick(label2dHandler, 200, 200, -1, 0)
  const finished = Session.label2dList.selectedLabels[0]
  finished.onKeyDown(Key.ENTER)

  const drawSpy = jest
    .spyOn(finished, "draw")
    .mockImplementation(() => undefined)
  finished.editing = true
  try {
    redrawAt(3, [800, 800, 100, 100])
    expect(drawSpy).not.toHaveBeenCalled()
  } finally {
    finished.editing = false
  }
})
