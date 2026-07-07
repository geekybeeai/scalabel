import {
  isCutMode,
  onCutModeChange,
  setCutMode
} from "../../src/common/cut_state"

describe("cut_state", () => {
  beforeEach(() => {
    setCutMode(false)
  })

  test("arms and disarms", () => {
    expect(isCutMode()).toBe(false)
    setCutMode(true)
    expect(isCutMode()).toBe(true)
    setCutMode(false)
    expect(isCutMode()).toBe(false)
  })

  test("notifies listeners only on change", () => {
    let calls = 0
    const off = onCutModeChange(() => {
      calls += 1
    })
    setCutMode(true)
    expect(calls).toBe(1)
    setCutMode(true) // same value: no notification
    expect(calls).toBe(1)
    setCutMode(false)
    expect(calls).toBe(2)
    off()
    setCutMode(true)
    expect(calls).toBe(2)
  })
})
