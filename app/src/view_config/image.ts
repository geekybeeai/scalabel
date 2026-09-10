import { isInteracting } from "../common/interaction_state"
import Session from "../common/session"
import { decodeControlIndex, rgbToIndex } from "../drawable/util"
import { getCurrentItem } from "../functional/state_util"
import { Size2D } from "../math/size2d"
import { Vector2D } from "../math/vector2d"
import { ImageViewerConfigType, State } from "../types/state"

// Display export constants
/**
 * The maximum scale.
 *
 * The old ceiling of 12 existed because the canvas BACKING store was sized to
 * the whole scaled image and so blew past the browser's 16384px per-dimension
 * limit — a 20000px-wide image was already over it at 12x. The backing store is
 * now capped independently of the CSS box (see MAX_CANVAS_DIMENSION), so zoom
 * is bounded by usefulness rather than allocation.
 *
 * The trade-off at very high zoom is sharpness, not correctness: the backing
 * store is stretched over a larger CSS box, so the image softens while every
 * coordinate stays exact.
 */
export const MAX_SCALE = 60.0

/** The minimum scale */
export const MIN_SCALE = 1.0

/**
 * Whether a viewer may render at this zoom.
 *
 * The old MAX_SCALE ceiling existed only because the canvas was sized to the
 * whole image, so it grew quadratically and hit the browser's 16384px
 * per-dimension limit. Now the canvas covers only the visible viewport, so
 * there is nothing to overflow and any zoom is renderable.
 *
 * @param _viewerId which viewer is asking (unused; kept for call sites)
 * @param viewScale the zoom level
 */
export function isScaleRenderable(
  _viewerId: number,
  viewScale: number
): boolean {
  return viewScale >= MIN_SCALE
}

/**
 * Maximum canvas backing resolution (width or height).
 * Prevents excessive GPU memory usage at high zoom on large images and keeps
 * every canvas inside the browser's 16384px per-dimension limit. 8192 is
 * supported essentially everywhere and leaves headroom for the CSS box, which
 * may be far larger at high zoom.
 */
export const MAX_CANVAS_DIMENSION = 8192

/** Backing-resolution multiplier applied while a gesture is in progress. */
export const MOTION_RESOLUTION_SCALE = 0.7

/**
 * Adaptive up-resolution ratio. 2x retina sharpness at low zoom, 1x at high
 * zoom (pixels already visible). The during-gesture downscale is applied in
 * updateCanvasScale (opt-in via applyMotionScale) so it affects ONLY the image
 * canvas (the costly blit). The vector label/control canvases keep full
 * resolution during a gesture, so label/tag sizes stay constant while zooming.
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
 * Rotate a point from the original image frame into the displayed (rotated)
 * frame. Exact, lossless 90° math. `rotation` is 0 | 90 | 180 | 270
 * (clockwise); `w`/`h` are the ORIGINAL image width/height.
 *
 * @param p the point in original-image coordinates
 * @param rotation clockwise rotation in degrees (0/90/180/270)
 * @param w original image width
 * @param h original image height
 */
export function rotatePoint(
  p: Vector2D,
  rotation: number,
  w: number,
  h: number
): Vector2D {
  switch (rotation) {
    case 90:
      return new Vector2D(h - p.y, p.x)
    case 180:
      return new Vector2D(w - p.x, h - p.y)
    case 270:
      return new Vector2D(p.y, w - p.x)
    default:
      return new Vector2D(p.x, p.y)
  }
}

/**
 * Inverse of rotatePoint: map a point in the displayed (rotated) frame back
 * to the original image frame. `w`/`h` are the ORIGINAL image width/height.
 *
 * @param p the point in displayed (rotated) coordinates
 * @param rotation clockwise rotation in degrees (0/90/180/270)
 * @param w original image width
 * @param h original image height
 */
export function unrotatePoint(
  p: Vector2D,
  rotation: number,
  w: number,
  h: number
): Vector2D {
  switch (rotation) {
    case 90:
      return new Vector2D(p.y, h - p.x)
    case 180:
      return new Vector2D(w - p.x, h - p.y)
    case 270:
      return new Vector2D(w - p.y, p.x)
    default:
      return new Vector2D(p.x, p.y)
  }
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
 * @param rotation view rotation in degrees (0/90/180/270)
 */
export function drawImageOnCanvas(
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  itemIndex?: number,
  sensorId?: number,
  rotation: number = 0
): void {
  clearCanvas(canvas, context)

  // Enable image smoothing for downscaled images (better quality)
  // Disable for upscaled images (preserves pixel detail)
  const isDownscaled =
    canvas.width < image.width || canvas.height < image.height
  context.imageSmoothingEnabled = isDownscaled
  context.imageSmoothingQuality = isDownscaled ? "high" : "low"

  // Rotate the drawing context so the bitmap paints turned. The canvas is
  // already sized to the rotated dimensions (updateCanvasScale), so for
  // 90°/270° the un-rotated content box uses the swapped width/height.
  const swap = rotation === 90 || rotation === 270
  const drawW = swap ? canvas.height : canvas.width
  const drawH = swap ? canvas.width : canvas.height
  context.save()
  if (rotation === 90) {
    context.translate(canvas.width, 0)
    context.rotate(Math.PI / 2)
  } else if (rotation === 180) {
    context.translate(canvas.width, canvas.height)
    context.rotate(Math.PI)
  } else if (rotation === 270) {
    context.translate(0, canvas.height)
    context.rotate(-Math.PI / 2)
  }

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
        drawW,
        drawH
      )
      context.restore()
      return
    }
    // Async create bitmap for future draws (don't block current frame)
    getImageBitmap(image, cacheKey).catch(() => {
      // Silently ignore - fallback to HTMLImageElement
    })
  }

  // Fallback to standard HTMLImageElement draw
  context.drawImage(image, 0, 0, image.width, image.height, 0, 0, drawW, drawH)
  context.restore()
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
 * @param applyMotionScale
 */
export function updateCanvasScale(
  state: State,
  display: HTMLDivElement,
  canvas: HTMLCanvasElement,
  context: CanvasRenderingContext2D | null,
  config: ImageViewerConfigType,
  zoomRatio: number,
  upRes: boolean,
  applyMotionScale: boolean = false
): number[] {
  const displayRect = display.getBoundingClientRect()

  if (context !== null) {
    context.scale(zoomRatio, zoomRatio)
  }

  // Resize canvas
  const item = getCurrentItem(state)
  const image = Session.images[item.index][config.sensor]
  // For a 90°/270° view rotation the displayed image is the original turned
  // on its side, so the canvas is sized to the swapped dimensions. The canvas
  // DOM element stays axis-aligned (content is rotated via the draw context).
  const rotated = config.rotation === 90 || config.rotation === 270
  const imgW = rotated ? image.height : image.width
  const imgH = rotated ? image.width : image.height
  const ratio = imgW / imgH
  let canvasHeight
  let canvasWidth
  let displayToImageRatio
  if (displayRect.width / displayRect.height > ratio) {
    canvasHeight = displayRect.height * config.viewScale
    canvasWidth = canvasHeight * ratio
    displayToImageRatio = canvasHeight / imgH
  } else {
    canvasWidth = displayRect.width * config.viewScale
    canvasHeight = canvasWidth / ratio
    displayToImageRatio = canvasWidth / imgW
  }

  // Adaptive up-res ratio based on current zoom level. Only the image canvas
  // opts into the during-gesture downscale (applyMotionScale); the label/
  // control canvases keep full resolution so label/tag sizes stay constant.
  const upResRatio =
    applyMotionScale && isInteracting()
      ? MOTION_RESOLUTION_SCALE
      : getUpResRatio(config.viewScale)

  // Calculate target canvas backing resolution
  let targetWidth = upRes ? canvasWidth * upResRatio : canvasWidth
  let targetHeight = upRes ? canvasHeight * upResRatio : canvasHeight

  // Cap the backing store so allocation never fails.
  //
  // The CSS box still covers the whole scaled image, so every coordinate
  // mapping (which works in CSS px via getBoundingClientRect) is unchanged and
  // annotations stay exactly where they are. Only RESOLUTION is capped: past
  // this point the browser upscales the backing store to the CSS box.
  //
  // Previously this clamp was undone by a floor at the CSS size, which is why
  // a 20000px-wide image already exceeded the browser's 16384px per-dimension
  // limit at 12x zoom and the canvas silently failed to allocate.
  if (
    targetWidth > MAX_CANVAS_DIMENSION ||
    targetHeight > MAX_CANVAS_DIMENSION
  ) {
    const scaleFactor = Math.min(
      MAX_CANVAS_DIMENSION / targetWidth,
      MAX_CANVAS_DIMENSION / targetHeight
    )
    targetWidth = Math.max(1, Math.floor(targetWidth * scaleFactor))
    targetHeight = Math.max(1, Math.floor(targetHeight * scaleFactor))
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
  // >= 1 except for the image canvas during an active gesture, where it is
  // intentionally < 1 (see MOTION_RESOLUTION_SCALE / applyMotionScale),
  // restored on the idle repaint. Label/control canvases are always >= 1.
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
