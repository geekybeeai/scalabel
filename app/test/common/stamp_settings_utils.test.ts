/** @jest-environment node */

import { clampStampNumber } from "../../src/common/stamp_settings_utils"

describe("stamp numeric settings", () => {
  test("clamps manually entered values to the slider range", () => {
    expect(clampStampNumber(-10, 4, 200)).toBe(4)
    expect(clampStampNumber(38, 4, 200)).toBe(38)
    expect(clampStampNumber(999, 4, 200)).toBe(200)
  })
})
