import { isCutMode, setCutMode } from "../../src/common/cut_state"
import {
  addFreeformPoint,
  armFreeform,
  beginFreeformPath,
  endFreeformPath,
  getFreeformPath,
  isFreeformActive,
  isFreeformArmed,
  isFreeformDrawing,
  resetFreeform
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
