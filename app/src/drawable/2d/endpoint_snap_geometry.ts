import { PathPointType } from "../../types/state"

/** Minimal point shape needed by endpoint snap distance checks. */
export interface SnapPoint {
  x: number
  y: number
  type?: PathPointType
}

/**
 * Return every visible handle in a closed shape as a snap target.
 *
 * Midpoint handles are interactive points too, even though they are not
 * persisted as polygon vertices when a shape is exported.
 *
 * @param points points from the closed shape
 */
export function snapTargetIndices(points: readonly SnapPoint[]): number[] {
  return points.map((_point, index) => index)
}

/**
 * Find the nearest point within the snap radius.
 *
 * Closed polygons use this helper for every visible handle; open polylines
 * pass only their first and last points at the call site.
 *
 * @param points candidate handles
 * @param coord dragged coordinate
 * @param maxDistance maximum image-space distance
 */
export function nearestSnapPointIndex(
  points: readonly SnapPoint[],
  coord: SnapPoint,
  maxDistance: number
): number | null {
  let nearest: number | null = null
  let minDistance = maxDistance
  for (let index = 0; index < points.length; index++) {
    const dx = coord.x - points[index].x
    const dy = coord.y - points[index].y
    const distance = Math.hypot(dx, dy)
    if (distance < minDistance) {
      minDistance = distance
      nearest = index
    }
  }
  return nearest
}
