import { IdType } from "../types/state"

/**
 * Transient, non-Redux set of line labels (polylines/polygons) the user has
 * Ctrl+clicked to mark for batch deletion. Mirrors the cut_state.ts /
 * segment_delete_state.ts tool-state pattern: plain module state plus a
 * listener list so the canvas can repaint when the set changes. The set is
 * scoped to the current item and is cleared on Escape, on Delete, and on item
 * navigation.
 */
const marked = new Set<IdType>()
const listeners = new Set<() => void>()

/** Notify all listeners of a change. */
function notify(): void {
  listeners.forEach((listener) => listener())
}

/**
 * Whether a label id is currently marked for deletion.
 *
 * @param labelId the label id
 */
export function isMarked(labelId: IdType): boolean {
  return marked.has(labelId)
}

/**
 * Toggle a label id in or out of the marked set. Always notifies.
 *
 * @param labelId the label id
 */
export function toggleMarked(labelId: IdType): void {
  if (marked.has(labelId)) {
    marked.delete(labelId)
  } else {
    marked.add(labelId)
  }
  notify()
}

/**
 * Add every id to the marked set (union — never removes). Notifies once if any
 * id was newly added. Used by the freeform lasso, which only ever adds.
 *
 * @param ids the ids to mark
 */
export function markLabels(ids: IdType[]): void {
  let changed = false
  for (const id of ids) {
    if (!marked.has(id)) {
      marked.add(id)
      changed = true
    }
  }
  if (changed) {
    notify()
  }
}

/** A snapshot array of the currently marked ids. */
export function getMarked(): IdType[] {
  return [...marked]
}

/** How many ids are marked. */
export function markedCount(): number {
  return marked.size
}

/** Clear all marks. Notifies only when something was actually cleared. */
export function clearMarked(): void {
  if (marked.size === 0) {
    return
  }
  marked.clear()
  notify()
}

/**
 * Subscribe to marked-set changes.
 *
 * @param listener called after every change
 * @returns an unsubscribe function
 */
export function onMarkedChange(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
