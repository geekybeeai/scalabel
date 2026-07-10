import { isCutMode, setCutMode } from "../../src/common/cut_state"
import {
  addFreeformPoint,
  armFreeform,
  beginFreeformPath,
  completeRect,
  endFreeformPath,
  getFreeformPath,
  getRectPreview,
  getSelectionOverlay,
  getSelectMode,
  isFreeformActive,
  isFreeformArmed,
  isFreeformDrawing,
  isRectSizing,
  resetFreeform,
  setRectFirstCorner,
  setSelectMode,
  updateRectCursor
} from "../../src/common/freeform_select_state"
import {
  armSegmentDelete,
  isSegmentDeleteActive,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"

describe("freeform_select_state", () => {
  beforeEach(() => {
    resetFreeform()
    setCutMode(false)
    resetSegmentDelete()
  })

  test("arming sets armed and active", () => {
    expect(isFreeformArmed()).toBe(false)
    armFreeform()
    expect(isFreeformArmed()).toBe(true)
    expect(isFreeformActive()).toBe(true)
  })

  test("path lifecycle with merge-near", () => {
    beginFreeformPath({ x: 0, y: 0 })
    expect(isFreeformDrawing()).toBe(true)
    addFreeformPoint({ x: 10, y: 0 })
    addFreeformPoint({ x: 10.1, y: 0 }) // within merge distance -> dropped
    addFreeformPoint({ x: 20, y: 0 })
    expect(getFreeformPath()).toHaveLength(3)
    const finished = endFreeformPath()
    expect(finished).toHaveLength(3)
    expect(isFreeformDrawing()).toBe(false)
    expect(getFreeformPath()).toHaveLength(0)
  })

  test("end keeps armed (sticky mode)", () => {
    armFreeform()
    beginFreeformPath({ x: 0, y: 0 })
    endFreeformPath()
    expect(isFreeformArmed()).toBe(true)
  })

  test("reset clears everything", () => {
    armFreeform()
    beginFreeformPath({ x: 0, y: 0 })
    resetFreeform()
    expect(isFreeformArmed()).toBe(false)
    expect(isFreeformActive()).toBe(false)
    expect(getFreeformPath()).toHaveLength(0)
  })

  test("arming the cut tool disarms freeform", () => {
    armFreeform()
    setCutMode(true)
    expect(isFreeformArmed()).toBe(false)
  })

  test("arming freeform disarms the cut tool", () => {
    setCutMode(true)
    armFreeform()
    expect(isCutMode()).toBe(false)
  })

  test("arming delete-segment disarms freeform", () => {
    armFreeform()
    armSegmentDelete()
    expect(isFreeformArmed()).toBe(false)
  })

  test("arming freeform disarms delete-segment", () => {
    armSegmentDelete()
    armFreeform()
    expect(isSegmentDeleteActive()).toBe(false)
  })
})

describe("freeform_select_state rectangle mode", () => {
  beforeEach(() => {
    resetFreeform()
    setSelectMode("freeform")
  })

  test("mode defaults to freeform and can switch", () => {
    expect(getSelectMode()).toBe("freeform")
    setSelectMode("rectangle")
    expect(getSelectMode()).toBe("rectangle")
  })

  test("two-click lifecycle builds four ordered corners", () => {
    setSelectMode("rectangle")
    expect(isRectSizing()).toBe(false)
    setRectFirstCorner({ x: 10, y: 20 })
    expect(isRectSizing()).toBe(true)
    updateRectCursor({ x: 40, y: 60 })
    expect(getRectPreview()).toEqual([
      { x: 10, y: 20 },
      { x: 40, y: 20 },
      { x: 40, y: 60 },
      { x: 10, y: 60 }
    ])
    const corners = completeRect({ x: 40, y: 60 })
    expect(corners).toEqual([
      { x: 10, y: 20 },
      { x: 40, y: 20 },
      { x: 40, y: 60 },
      { x: 10, y: 60 }
    ])
    expect(isRectSizing()).toBe(false)
    expect(getRectPreview()).toBeNull()
  })

  test("too-small rectangle completes to null and clears sizing", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 10, y: 10 })
    expect(completeRect({ x: 11, y: 11 })).toBeNull()
    expect(isRectSizing()).toBe(false)
  })

  test("getSelectionOverlay returns the rectangle preview in rectangle mode", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 0, y: 0 })
    updateRectCursor({ x: 30, y: 30 })
    expect(getSelectionOverlay()).toHaveLength(4)
  })

  test("resetFreeform clears rectangle state but keeps the mode", () => {
    setSelectMode("rectangle")
    setRectFirstCorner({ x: 0, y: 0 })
    resetFreeform()
    expect(isRectSizing()).toBe(false)
    expect(getSelectMode()).toBe("rectangle")
  })
})
