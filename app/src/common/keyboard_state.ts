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
 * Record a key press. Call from the document keydown listener.
 *
 * @param key the KeyboardEvent.key value
 */
export function recordKeyDown(key: string): void {
  held.add(key)
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

/** Drop all held keys (focus loss can eat the matching keyup events). */
export function resetHeldKeys(): void {
  held.clear()
}

// A key stuck "held" after focus loss would fire phantom C/D click actions
// later; clear on blur. Guarded so non-browser (jest node-env) imports work.
if (typeof window !== "undefined") {
  window.addEventListener("blur", resetHeldKeys)
}
