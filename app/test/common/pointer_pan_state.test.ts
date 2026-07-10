import {
  armEmptyDrag, isArmed, downPos, markPanned, didPan, reset,
  openPanWindow, inPanWindow, PAN_THRESHOLD, exceededThreshold,
  shouldDeferPointerDown
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

  describe("shouldDeferPointerDown", () => {
    test("empty canvas always defers, in or out of the pan window", () => {
      expect(shouldDeferPointerDown(-1, 0, 1000)).toBe(true)
      openPanWindow(1000)
      expect(shouldDeferPointerDown(-1, 0, 1100)).toBe(true)
    })

    test("label body inside the pan window defers (double-click pan)", () => {
      openPanWindow(1000)
      expect(shouldDeferPointerDown(2, 0, 1100)).toBe(true)
    })

    test("label point inside the pan window does NOT defer (trackpad curve drag)", () => {
      openPanWindow(1000)
      expect(shouldDeferPointerDown(2, 3, 1100)).toBe(false)
    })

    test("label hit outside the pan window does not defer", () => {
      expect(shouldDeferPointerDown(2, 0, 999999)).toBe(false)
      expect(shouldDeferPointerDown(2, 3, 999999)).toBe(false)
    })

    test("body hit in the pan window with a live highlighted point does NOT defer", () => {
      // After C+tap converts a midpoint, the control points move to the 1/3
      // and 2/3 marks — the follow-up trackpad press lands on the BODY, but
      // the drawable still has the point handle highlighted from the tap.
      openPanWindow(1000)
      expect(shouldDeferPointerDown(2, 0, 1100, 3)).toBe(false)
    })

    test("body hit in the pan window with no live point still defers (pan)", () => {
      openPanWindow(1000)
      expect(shouldDeferPointerDown(2, 0, 1100, -1)).toBe(true)
      expect(shouldDeferPointerDown(2, 0, 1100, 0)).toBe(true)
    })
  })
})
