import {
  clearMarked,
  getMarked,
  isMarked,
  markedCount,
  onMarkedChange,
  toggleMarked
} from "../../src/common/multi_delete_state"

describe("multi_delete_state", () => {
  beforeEach(() => {
    clearMarked()
  })

  test("toggles ids in and out of the marked set", () => {
    expect(isMarked("lineA")).toBe(false)
    expect(markedCount()).toBe(0)

    toggleMarked("lineA")
    expect(isMarked("lineA")).toBe(true)
    expect(markedCount()).toBe(1)

    toggleMarked("lineB")
    expect(getMarked().sort()).toEqual(["lineA", "lineB"])

    toggleMarked("lineA") // toggle off
    expect(isMarked("lineA")).toBe(false)
    expect(getMarked()).toEqual(["lineB"])
  })

  test("clearMarked empties the set", () => {
    toggleMarked("lineA")
    toggleMarked("lineB")
    clearMarked()
    expect(markedCount()).toBe(0)
    expect(getMarked()).toEqual([])
  })

  test("notifies listeners on change, not on no-op clears, and after unsubscribe", () => {
    let calls = 0
    const off = onMarkedChange(() => {
      calls += 1
    })
    toggleMarked("lineA") // add
    expect(calls).toBe(1)
    toggleMarked("lineA") // remove
    expect(calls).toBe(2)
    clearMarked() // set is empty now: no-op, no notification
    expect(calls).toBe(2)
    toggleMarked("lineB")
    clearMarked() // had one: notifies
    expect(calls).toBe(4)
    off()
    toggleMarked("lineC")
    expect(calls).toBe(4)
  })
})
