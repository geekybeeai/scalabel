/**
 * Module-level record of the keys physically held right now, fed by
 * Label2dCanvas's document keydown/keyup listeners. Mirrors the
 * pointer_pan_state.ts module-state pattern.
 *
 * Drawables must read held keys from here at mouse-down time — NOT from their
 * own per-instance `_keyDownMap`: the select-on-click dispatch rebuilds
 * drawables mid-gesture with fresh (empty) key maps (and the handler's own
 * pressed-key set can be cleared by the same dispatch), which made the
 * C+click curve and D+click delete silently fail, reproducibly on trackpads.
 */
const held = new Set<string>()

/**
 * Laptop palm-rejection ("disable touchpad while typing") suppresses trackpad
 * taps while a key is held — key auto-repeat keeps resetting the suppression
 * timer, so users physically cannot hold C/D and click on a trackpad; the
 * click only lands after the key is released. The "arm window" makes those
 * gestures sequential: a plain (unmodified) key press arms the key for
 * KEY_ARM_WINDOW_MS, and click-time checks accept an armed key as if it were
 * still held. Arms are one-shot: consumed on use.
 */
export const KEY_ARM_WINDOW_MS = 3000

/** Per-key timestamp (ms) of the last plain press, for the arm window. */
const armedAtMs = new Map<string, number>()

/**
 * Record a key press. Call from the document keydown listener.
 *
 * @param key the KeyboardEvent.key value
 */
export function recordKeyDown(key: string): void {
  held.add(key)
}

/**
 * Arm a key for the sequential press-then-click window. Call from the
 * document keydown listener for plain presses only (no Ctrl/Meta chord, so
 * Ctrl+C copy does not arm a curve conversion). Key repeats refresh the arm.
 *
 * @param key the KeyboardEvent.key value
 * @param nowMs the current time — pass Date.now()
 */
export function armKey(key: string, nowMs: number): void {
  armedAtMs.set(key, nowMs)
}

/**
 * Whether the key was plainly pressed within the arm window.
 *
 * @param key the KeyboardEvent.key value
 * @param nowMs the current time — pass Date.now()
 */
export function isKeyArmed(key: string, nowMs: number): boolean {
  const at = armedAtMs.get(key)
  return at !== undefined && nowMs - at <= KEY_ARM_WINDOW_MS
}

/**
 * Consume a key's arm (one-shot semantics — using an armed gesture spends it).
 *
 * @param key the KeyboardEvent.key value
 */
export function consumeArmedKey(key: string): void {
  armedAtMs.delete(key)
}

/**
 * Record a key release. Call from the document keyup listener.
 *
 * @param key the KeyboardEvent.key value
 */
export function recordKeyUp(key: string): void {
  held.delete(key)
}

/**
 * Whether a key is currently held.
 *
 * @param key the KeyboardEvent.key value
 */
export function isKeyHeld(key: string): boolean {
  return held.has(key)
}

/** Drop all held keys and arms (focus loss can eat the matching keyups). */
export function resetHeldKeys(): void {
  held.clear()
  armedAtMs.clear()
}

// A key stuck "held" after focus loss would fire phantom C/D click actions
// later; clear on blur. Guarded so non-browser (jest node-env) imports work.
if (typeof window !== "undefined") {
  window.addEventListener("blur", resetHeldKeys)
}
