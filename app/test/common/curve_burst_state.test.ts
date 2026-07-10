import {
  CURVE_BURST_WINDOW_MS,
  isRecentCurveConversion,
  markCurveConversion,
  resetCurveBurst
} from "../../src/common/curve_burst_state"

describe("curve_burst_state", () => {
  beforeEach(() => resetCurveBurst())

  test("no conversion recorded -> not recent", () => {
    expect(isRecentCurveConversion("label-a", 1000)).toBe(false)
  })

  test("within the burst window after marking -> recent", () => {
    markCurveConversion("label-a", 1000)
    expect(
      isRecentCurveConversion("label-a", 1000 + CURVE_BURST_WINDOW_MS - 1)
    ).toBe(true)
  })

  test("after the window expires -> not recent (straighten toggle works)", () => {
    markCurveConversion("label-a", 1000)
    expect(
      isRecentCurveConversion("label-a", 1000 + CURVE_BURST_WINDOW_MS + 1)
    ).toBe(false)
  })

  test("a different label is not part of the burst", () => {
    markCurveConversion("label-a", 1000)
    expect(isRecentCurveConversion("label-b", 1100)).toBe(false)
  })

  test("reset clears the burst", () => {
    markCurveConversion("label-a", 1000)
    resetCurveBurst()
    expect(isRecentCurveConversion("label-a", 1100)).toBe(false)
  })
})
