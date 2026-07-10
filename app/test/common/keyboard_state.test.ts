import {
  isKeyHeld,
  recordKeyDown,
  recordKeyUp,
  resetHeldKeys
} from "../../src/common/keyboard_state"

describe("keyboard_state", () => {
  beforeEach(() => {
    resetHeldKeys()
  })

  test("tracks held keys across down/up", () => {
    expect(isKeyHeld("c")).toBe(false)
    recordKeyDown("c")
    expect(isKeyHeld("c")).toBe(true)
    // OS key-repeat re-fires keydown; recording must stay idempotent.
    recordKeyDown("c")
    expect(isKeyHeld("c")).toBe(true)
    recordKeyUp("c")
    expect(isKeyHeld("c")).toBe(false)
  })

  test("keys are tracked independently", () => {
    recordKeyDown("c")
    recordKeyDown("d")
    recordKeyUp("c")
    expect(isKeyHeld("c")).toBe(false)
    expect(isKeyHeld("d")).toBe(true)
  })

  test("reset drops everything", () => {
    recordKeyDown("c")
    recordKeyDown("d")
    resetHeldKeys()
    expect(isKeyHeld("c")).toBe(false)
    expect(isKeyHeld("d")).toBe(false)
  })
})
