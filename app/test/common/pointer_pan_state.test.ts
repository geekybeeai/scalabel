import {
  armEmptyDrag, isArmed, downPos, markPanned, didPan, reset,
  openPanWindow, inPanWindow, PAN_THRESHOLD, exceededThreshold
} from "../../src/common/pointer_pan_state"

describe("pointer_pan_state", () => {
  beforeEach(() => reset())

  test("arm/down/threshold/reset", () => {
    expect(isArmed()).toBe(false)
    armEmptyDrag(100, 100)
    expect(isArmed()).toBe(true)
    expect(downPos()).toEqual({ x: 100, y: 100 })
    expect(exceededThreshold(100 + PAN_THRESHOLD - 1, 100)).toBe(false)
    expect(exceededThreshold(100 + PAN_THRESHOLD + 1, 100)).toBe(true)
    reset()
    expect(isArmed()).toBe(false)
  })

  test("panned flag", () => {
    armEmptyDrag(0, 0)
    expect(didPan()).toBe(false)
    markPanned()
    expect(didPan()).toBe(true)
  })

  test("pan window", () => {
    expect(inPanWindow(1000)).toBe(false)
    openPanWindow(1000)
    expect(inPanWindow(1100)).toBe(true)
    expect(inPanWindow(1000 + 9999)).toBe(false)
  })
})
