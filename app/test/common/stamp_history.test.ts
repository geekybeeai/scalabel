/** @jest-environment node */

import {
  getStampHistoryTarget,
  StampHistoryTarget,
  runStampHistoryAction
} from "../../src/common/stamp_history"

test("applied manual stamp previews route undo to grouped history", () => {
  expect(
    getStampHistoryTarget({
      previewing: true,
      evenSpacing: false,
      hasApplied: true
    })
  ).toBe(StampHistoryTarget.HISTORY)
})

test("pending manual stamp previews keep undo local", () => {
  expect(
    getStampHistoryTarget({
      previewing: true,
      evenSpacing: false,
      hasApplied: false
    })
  ).toBe(StampHistoryTarget.POSITIONS)
})

describe("stamp-aware history actions", () => {
  test("clears an applied preview after history changes the document", () => {
    const clearPreview = jest.fn()

    expect(runStampHistoryAction(() => true, true, clearPreview)).toBe(true)
    expect(clearPreview).toHaveBeenCalledTimes(1)
  })

  test("does not clear a preview when history did nothing", () => {
    const clearPreview = jest.fn()

    expect(runStampHistoryAction(() => false, true, clearPreview)).toBe(false)
    expect(clearPreview).not.toHaveBeenCalled()
  })
})
