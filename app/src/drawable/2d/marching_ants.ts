/**
 * Shared marching-ants dash offset for the delete affordances — the
 * delete-segment preview overlay and the multi-select-delete line highlight.
 * A single canvas requestAnimationFrame loop advances it; both the overlay
 * (Label2dCanvas) and the marked-line stroke (Polygon2D.draw) read it, so every
 * delete dash animates in lockstep with the same motion.
 */
let offset = 0

/** The current marching-ants dash offset (canvas px). */
export function getAntsOffset(): number {
  return offset
}

/**
 * Advance the marching-ants dash offset.
 *
 * @param px pixels to advance by (per animation frame)
 */
export function advanceAnts(px: number): void {
  offset += px
}
