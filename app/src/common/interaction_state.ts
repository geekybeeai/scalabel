/**
 * Transient, non-Redux signal for "is the user mid zoom/scroll/pan gesture".
 * Kept out of Redux so per-frame gesture updates don't churn the store.
 * Auto-clears IDLE_MS after the last gesture and notifies idle listeners then.
 */
type Listener = () => void

export const IDLE_MS = 150

let active = false
let timer: ReturnType<typeof setTimeout> | null = null
const idleListeners: Set<Listener> = new Set()

/** Mark a gesture frame; (re)starts the idle countdown. */
export function notifyGesture(): void {
  active = true
  if (timer !== null) {
    clearTimeout(timer)
  }
  timer = setTimeout(() => {
    active = false
    timer = null
    idleListeners.forEach((l) => l())
  }, IDLE_MS)
}

/** True while a gesture is in progress. */
export function isInteracting(): boolean {
  return active
}

/** Subscribe to "gesture settled"; returns an unsubscribe fn. */
export function onIdle(listener: Listener): () => void {
  idleListeners.add(listener)
  return () => {
    idleListeners.delete(listener)
  }
}

/** Test-only reset. */
export function _resetForTest(): void {
  active = false
  if (timer !== null) {
    clearTimeout(timer)
  }
  timer = null
  idleListeners.clear()
}
