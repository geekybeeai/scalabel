import {
  notifyGesture,
  isInteracting,
  onIdle,
  _resetForTest,
  IDLE_MS
} from "../../src/common/interaction_state"

describe("interaction_state", () => {
  beforeEach(() => {
    jest.useFakeTimers()
    _resetForTest()
  })
  afterEach(() => jest.useRealTimers())

  test("notifyGesture marks interacting, clears after IDLE_MS", () => {
    expect(isInteracting()).toBe(false)
    notifyGesture()
    expect(isInteracting()).toBe(true)
    jest.advanceTimersByTime(IDLE_MS - 1)
    expect(isInteracting()).toBe(true)
    jest.advanceTimersByTime(1)
    expect(isInteracting()).toBe(false)
  })

  test("repeated gestures keep it active (debounced)", () => {
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS - 10)
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS - 10)
    expect(isInteracting()).toBe(true)
  })

  test("onIdle fires once when the gesture settles", () => {
    const cb = jest.fn()
    onIdle(cb)
    notifyGesture()
    expect(cb).not.toBeCalled()
    jest.advanceTimersByTime(IDLE_MS)
    expect(cb).toBeCalledTimes(1)
  })

  test("onIdle returns an unsubscribe", () => {
    const cb = jest.fn()
    const off = onIdle(cb)
    off()
    notifyGesture()
    jest.advanceTimersByTime(IDLE_MS)
    expect(cb).not.toBeCalled()
  })
})
