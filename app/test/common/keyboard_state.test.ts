import {
  armKey,
  consumeArmedKey,
  isKeyArmed,
  isKeyHeld,
  KEY_ARM_WINDOW_MS,
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

  test("armed key stays armed within the window and expires after", () => {
    armKey("c", 1000)
    expect(isKeyArmed("c", 1000)).toBe(true)
    expect(isKeyArmed("c", 1000 + KEY_ARM_WINDOW_MS)).toBe(true)
    expect(isKeyArmed("c", 1001 + KEY_ARM_WINDOW_MS)).toBe(false)
    expect(isKeyArmed("d", 1000)).toBe(false)
  })

  test("re-arming refreshes the window (key repeat)", () => {
    armKey("c", 1000)
    armKey("c", 5000)
    expect(isKeyArmed("c", 5000 + KEY_ARM_WINDOW_MS)).toBe(true)
  })

  test("consuming an arm is one-shot", () => {
    armKey("c", 1000)
    consumeArmedKey("c")
    expect(isKeyArmed("c", 1001)).toBe(false)
  })

  test("reset clears arms too", () => {
    armKey("c", 1000)
    resetHeldKeys()
    expect(isKeyArmed("c", 1001)).toBe(false)
  })
})
