/** @jest-environment node */

import {
  getStampCanvasClickAction,
  StampCanvasClickAction
} from "../../src/common/stamp_interaction"

describe("stamp canvas interaction", () => {
  test("even-spacing previews ignore canvas clicks", () => {
    expect(
      getStampCanvasClickAction({
        previewing: true,
        evenSpacing: true,
        armed: false
      })
    ).toBe(StampCanvasClickAction.IGNORE_PREVIEW)
  })

  test("manual previews still use canvas clicks for placement", () => {
    expect(
      getStampCanvasClickAction({
        previewing: true,
        evenSpacing: false,
        armed: false
      })
    ).toBe(StampCanvasClickAction.PLACE_MANUAL)
  })

  test("armed stamp mode uses the click to pick a guide", () => {
    expect(
      getStampCanvasClickAction({
        previewing: false,
        evenSpacing: true,
        armed: true
      })
    ).toBe(StampCanvasClickAction.PICK_GUIDE)
  })
})
