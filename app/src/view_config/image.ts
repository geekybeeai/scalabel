import Session from "../common/session"
import { decodeControlIndex, rgbToIndex } from "../drawable/util"
import { getCurrentItem } from "../functional/state_util"
import { Size2D } from "../math/size2d"
import { Vector2D } from "../math/vector2d"
import { ImageViewerConfigType, State } from "../types/state"

// Display export constants
/** The maximum scale */
export const MAX_SCALE = 10.0
/** The minimum scale */
export const MIN_SCALE = 1.0
/**
 * Maximum canvas backing resolution (width or height).
 * Prevents excessive GPU memory usage at high zoom on large images.
 * 4096 is a safe limit for most GPUs; 8192 for high-end.
 */
export const MAX_CANVAS_DIMENSION = 4096
/**
 * Adaptive high-resolution ratio.
 * At low zoom (≤3×) use 2× for retina sharpness.
 * At high zoom (>3×) drop to 1× because individual pixels are already visible.
 *
 * @param viewScale current zoom level
 */
export function getUpResRatio(viewScale: number): number {
  return viewScale > 3 ? 1 : 2
}
/** The zoom ratio */
export const ZOOM_RATIO = 1.3
/** The scroll-zoom ratio */
export const SCROLL_ZOOM_RATIO = 1.03

/**
 * Get the current item in the state
 *
 * @param state
 * @param viewerId
 * @returns {Size2D}
 */
export function getCurrentImageSize(state: State, viewerId: number): Size2D {
  const item = getCurrentItem(state)
  const sensor = state.user.viewerConfigs[viewerId].sensor
  if (sensor in Session.images[item.index]) {
    const image = Session.images[item.index][sensor]
    return new Size2D(image.width, image.height)
  }
  return new Size2D(0, 0)
}

/**
 * Convert image coordinate to canvas coordinate.
 * If affine, assumes values to be [x, y]. Otherwise
 * performs linear transformation.
 *
 * @param {Vector2D} values - the values to convert.
 * @param {boolean} upRes
 * @param displayToImageRatio
 * @param upResRatio - effective up-resolution ratio (default 2)
 * @returns {Vector2D} - the converted values.
 */
export function toCanvasCoords(
  values: Vector2D,
  upRes: boolean,
  displayToImageRatio: number,
  upResRatio: number = 2
): Vector2D {
  const out = values.clone().scale(displayToImageRatio)
  if (upRes) {
    out.scale(upResRatio)
  }
  return out
}

/**
 * Convert canvas coordinate to image coordinate.
 * If affine, assumes values to be [x, y]. Otherwise
 * performs linear transformation.
 *
 * @param {Vector2D} values - the values to convert.
 * @param displayToImageRatio
 * @param {boolean} upRes - whether the canvas has higher resolution
 * @param upResRatio - effective up-resolution ratio (default 2)
 * @returns {Vector2D} - the converted values.
 */
export function toImageCoords(
  values: Vector2D,
  displayToImageRatio: number,
  upRes: boolean = true,
  upResRatio: number = 2
): Vector2D {
  const up = upRes ? 1 / upResRatio : 1
  return values.clone().scale(displayToImageRatio * up)
}

/**
 * Cache for ImageBitmap objects (faster GPU compositing than HTMLImageElement).
 * Key format: "itemIndex-sensorId"
 */
const imageBitmapCache: Map<string, ImageBitmap> = new Map()

/**
 * Get or create an ImageBitmap for the given image.
 * ImageBitmap provides faster drawing as the image is pre-decoded for GPU.
 *
 * @param image source HTMLImageElement
 * @param cacheKey unique key for caching
 */
async function getImageBitmap(
  image: HTMLImageElement,
  cacheKey: string
): Promise<ImageBitmap> {
  const cached = imageBitmapCache.get(cacheKey)
  if (cached !== undefined) {
    return cached
  }
  const bitmap = await createImageBitmap(image)
  imageBitmapCache.set(cacheKey, bitmap)
  return bitmap
}

/**
 * Clear the ImageBitmap cache (call when switching tasks/items).
 */
export function clearImageBitmapCache(): void {
  for (const bitmap of imageBitmapCache.values()) {
    bitmap.close()
  }
  imageBitmapCache.clear()
}

/**
 * Draw image on canvas.
 * Uses ImageBitmap when available for faster GPU-accelerated rendering.
 *
 * @param canvas
 * @param context
 * @param image
 * @param itemIndex optional item index for caching
 * @param sensorId optional sensor id for caching
 */
export function drawImageOnCanvas(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  itemIndex?: number,
  sensorId?: number
): void {
  clearCanvas(canvas, context)

  // Enable image smoothing for downscaled images (better quality)
  // Disable for upscaled images (preserves pixel detail)
  const isDownscaled = canvas.width < image.width || canvas.height < image.height
  context.imageSmoothingEnabled = isDownscaled
  context.imageSmoothingQuality = isDownscaled ? "high" : "low"

  // Try to use cached ImageBitmap for faster drawing
  if (itemIndex !== undefined && sensorId !== undefined) {
    const cacheKey = `${itemIndex}-${sensorId}`
    const cached = imageBitmapCache.get(cacheKey)
    if (cached !== undefined) {
      context.drawImage(
        cached,
        0,
        0,
        image.width,
        image.height,
        0,
        0,
        canvas.width,
        canvas.height
      )
      return
    }
    // Async create bitmap for future draws (don't block current frame)
    getImageBitmap(image, cacheKey).catch(() => {
      // Silently ignore - fallback to HTMLImageElement
    })
  }

  // Fallback to standard HTMLImageElement draw
  context.drawImage(
    image,
    0,
    0,
    image.width,
    image.height,
    0,
    0,
    canvas.width,
    canvas.height
  )
}

/**
 * Clear the canvas
 *
 * @param {HTMLCanvasElement} canvas - the canvas to redraw
 * @param {any} context - the context to redraw
 * @returns {boolean}
 */
export function clearCanvas(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D
): void {
  // Clear context
  context.clearRect(0, 0, canvas.width, canvas.height)
}

/**
 * Normalize mouse x & y to canvas coordinates
 *
 * @param display
 * @param canvas
 * @param canvasWidth
 * @param canvasHeight
 * @param displayToImageRatio
 * @param clientX
 * @param clientY
 */
export function normalizeMouseCoordinates(
  canvas: HTMLCanvasElement,
  canvasWidth: number,
  canvasHeight: number,
  displayToImageRatio: number,
  clientX: number,
  clientY: number
): Vector2D {
  // TODO(fyu): There is a rounding error between canvas.clientHeight
  //  and canvasHeight
  let offsetX = canvas.offsetLeft
  let offsetY = canvas.offsetTop
  const canvasBoundingRect = canvas.getBoundingClientRect()
  // Test if the bounding client is defined
  // If the bounding client is not defined, it can still return DOMRect, but the
  // values are undefined.
  // eslint-disable-next-line
  if (canvasBoundingRect.x !== undefined) {
    offsetX = canvasBoundingRect.x
    offsetY = canvasBoundingRect.y
  }
  let x = clientX - offsetX
  let y = clientY - offsetY

  // Limit the mouse within the image
  x = Math.max(0, Math.min(x, canvasWidth))
  y = Math.max(0, Math.min(y, canvasHeight))

  // Return in the image coordinates
  return new Vector2D(x / displayToImageRatio, y / displayToImageRatio)
}

/**
 * Function to find mode of a number array.
 *
 * @param {number[]} arr - the array.
 * @returns {number} the mode of the array.
 */
export function mode(arr: number[]): number | undefined {
  return arr
    .sort(
      (a, b) =>
        arr.filter((v) => v === a).length - arr.filter((v) => v === b).length
    )
    .pop()
}

/**
 * Get handle id from image color
 *
 * @param color
 * @param data
 */
export function imageDataToHandleId(data: Uint8ClampedArray): number[] {
  const arr = []
  for (let i = 0; i < 16; i++) {
    const color = rgbToIndex(Array.from(data.slice(i * 4, i * 4 + 3)))
    arr.push(color)
  }
  // Finding the mode of the data array to deal with anti-aliasing
  const hoveredIndex = mode(arr) as number
  return decodeControlIndex(hoveredIndex)
}

/**
 * Update canvas scale
 *
 * @param state
 * @param display
 * @param canvas
 * @param context
 * @param config
 * @param zoomRatio
 * @param upRes
 */
export function updateCanvasScale(
  state: State,
  display: HTMLDivElement,
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D | null,
  config: ImageViewerConfigType,
  zoomRatio: number,
  upRes: boolean
): number[] {
  const displayRect = display.getBoundingClientRect()

  if (context !== null) {
    context.scale(zoomRatio, zoomRatio)
  }

  // Resize canvas
  const item = getCurrentItem(state)
  const image = Session.images[item.index][config.sensor]
  const ratio = image.width / image.height
  let canvasHeight
  let canvasWidth
  let displayToImageRatio
  if (displayRect.width / displayRect.height > ratio) {
    canvasHeight = displayRect.height * config.viewScale
    canvasWidth = canvasHeight * ratio
    displayToImageRatio = canvasHeight / image.height
  } else {
    canvasWidth = displayRect.width * config.viewScale
    canvasHeight = canvasWidth / ratio
    displayToImageRatio = canvasWidth / image.width
  }

  // Adaptive up-res ratio based on current zoom level
  const upResRatio = getUpResRatio(config.viewScale)

  // Calculate target canvas backing resolution
  let targetWidth = upRes ? canvasWidth * upResRatio : canvasWidth
  let targetHeight = upRes ? canvasHeight * upResRatio : canvasHeight

  // Cap canvas backing resolution to prevent GPU memory issues.
  //
  // CRITICAL constraint: the backing canvas must NEVER be smaller than the
  // CSS display size (canvasWidth × canvasHeight). If it were, the image
  // would render at sub-1:1 pixel density → visibly blurry at high zoom.
  // The cap only reduces the *extra* pixels added by the upRes 2× retina
  // factor; the base 1:1 resolution is always preserved.
  //
  // This also guarantees effectiveUpResRatio >= 1, which keeps polyline
  // thickness adaptation correct (styleFactor = 1/√viewScale applied in
  // polygon2d.draw() maps directly to visual width in CSS pixels).
  if (targetWidth > MAX_CANVAS_DIMENSION || targetHeight > MAX_CANVAS_DIMENSION) {
    const scaleFactor = Math.min(
      MAX_CANVAS_DIMENSION / targetWidth,
      MAX_CANVAS_DIMENSION / targetHeight
    )
    const cappedWidth = Math.floor(targetWidth * scaleFactor)
    const cappedHeight = Math.floor(targetHeight * scaleFactor)
    // Enforce floor at CSS display size so image quality is never degraded
    targetWidth = Math.max(cappedWidth, Math.round(canvasWidth))
    targetHeight = Math.max(cappedHeight, Math.round(canvasHeight))
  }

  // Set canvas backing resolution
  canvas.width = targetWidth
  canvas.height = targetHeight

  // Set canvas CSS display size (visual size stays the same)
  canvas.style.height = `${canvasHeight}px`
  canvas.style.width = `${canvasWidth}px`

  // Set padding
  const padding = new Vector2D(
    Math.max(0, (displayRect.width - canvasWidth) / 2),
    Math.max(0, (displayRect.height - canvasHeight) / 2)
  )
  const padX = padding.x
  const padY = padding.y

  canvas.style.left = `${padX}px`
  canvas.style.top = `${padY}px`
  canvas.style.right = "auto"
  canvas.style.bottom = "auto"

  // Effective upRes ratio = actual backing pixels per CSS pixel.
  // Because canvas.width >= canvasWidth always, this is always >= 1.
  //
  // The label drawing invariant:
  //   drawingRatio = displayToImageRatio × effectiveUpResRatio
  //               = (canvas.width / image.width)   [backing px per image px]
  //
  // This ratio is passed as `ratio` to polygon2d/box2d .draw() which scales
  // image-space coordinates to backing canvas pixels. With effectiveUpResRatio
  // always >= 1, annotations always land exactly on the correct pixel and
  // the styleFactor-based line thinning at high zoom works as intended.
  const effectiveUpResRatio = upRes ? canvas.width / canvasWidth : 1

  return [
    canvasWidth,
    canvasHeight,
    displayToImageRatio,
    config.viewScale,
    effectiveUpResRatio
  ]
}
