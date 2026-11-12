/**
 * Backing pixels per CSS pixel used by the annotation style constants.
 *
 * The visible style constants were authored for the label canvas's normal 2x
 * backing resolution. Keep that reference stable even when the live canvas
 * switches resolution or is capped below 1 backing pixel per CSS pixel.
 */
export const REFERENCE_CANVAS_PIXEL_RATIO = 2

/** Ignore harmless integer rounding around the normal 1x backing ratio. */
const CAPPED_BACKING_RATIO_THRESHOLD = 0.99

/**
 * Convert a zoom-aware visible size into pixels for the current backing canvas.
 *
 * @param baseSize size in reference-backing pixels at 1x zoom
 * @param minimumSize minimum size in reference-backing pixels; this is also
 * the backing-store raster floor when canvas resolution is materially capped
 * @param viewScale current viewer zoom
 * @param effectiveUpResRatio actual backing pixels per CSS pixel
 */
export function zoomAwareBackingSize(
  baseSize: number,
  minimumSize: number,
  viewScale: number,
  effectiveUpResRatio: number
): number {
  const zoomScale = Math.max(1, viewScale)
  const baseCssSize = baseSize / REFERENCE_CANVAS_PIXEL_RATIO
  const minimumCssSize = minimumSize / REFERENCE_CANVAS_PIXEL_RATIO
  const visibleCssSize = Math.max(
    minimumCssSize,
    baseCssSize / Math.sqrt(zoomScale)
  )
  const backingSize = visibleCssSize * effectiveUpResRatio
  return effectiveUpResRatio < CAPPED_BACKING_RATIO_THRESHOLD
    ? Math.max(minimumSize, backingSize)
    : backingSize
}
