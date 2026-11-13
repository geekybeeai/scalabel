import { Vector2D } from "../math/vector2d"
import { ImageViewerConfigType } from "../types/state"
import { rotatePoint, unrotatePoint } from "./image"

/** Existing drawable styles use two logical drawing units per CSS pixel. */
export const ANNOTATION_RESOLUTION = 2

/** Display-only mapping; annotation geometry always stays in image pixels. */
export interface ViewportTransform {
  width: number
  height: number
  imageWidth: number
  imageHeight: number
  rotation: number
  scale: number
  left: number
  top: number
}

/** Compute the same fitted image rectangle as the image canvas. */
export function makeViewportTransform(
  width: number,
  height: number,
  imageWidth: number,
  imageHeight: number,
  config: ImageViewerConfigType
): ViewportTransform {
  const rotation = config.rotation ?? 0
  const swap = rotation === 90 || rotation === 270
  const w = swap ? imageHeight : imageWidth
  const h = swap ? imageWidth : imageHeight
  const scale = Math.min(width / w, height / h) * config.viewScale
  return {
    width,
    height,
    imageWidth,
    imageHeight,
    rotation,
    scale,
    left: config.displayLeft + Math.max(0, (width - w * scale) / 2),
    top: config.displayTop + Math.max(0, (height - h * scale) / 2)
  }
}

/** Project an original-image point to viewport CSS pixels. */
export function imageToViewport(
  point: Vector2D,
  view: ViewportTransform
): Vector2D {
  return rotatePoint(point, view.rotation, view.imageWidth, view.imageHeight)
    .scale(view.scale)
    .add(new Vector2D(view.left, view.top))
}

/** Inverse projection; optionally keep drawing input inside the image. */
export function viewportToImage(
  point: Vector2D,
  view: ViewportTransform,
  clamp: boolean = false
): Vector2D {
  const rotated = point
    .clone()
    .subtract(new Vector2D(view.left, view.top))
    .scale(1 / view.scale)
  const result = unrotatePoint(
    rotated,
    view.rotation,
    view.imageWidth,
    view.imageHeight
  )
  if (clamp) {
    result.x = Math.max(0, Math.min(view.imageWidth, result.x))
    result.y = Math.max(0, Math.min(view.imageHeight, result.y))
  }
  return result
}

/** Visible image bounds, including rotated views, for inexpensive culling. */
export function viewportImageBounds(
  view: ViewportTransform
): [number, number, number, number] {
  const a = viewportToImage(new Vector2D(0, 0), view)
  const b = viewportToImage(new Vector2D(view.width, view.height), view)
  return [
    Math.min(a.x, b.x),
    Math.min(a.y, b.y),
    Math.abs(b.x - a.x),
    Math.abs(b.y - a.y)
  ]
}

/** Apply pan/rotation once; drawable positions use scale * logical resolution. */
export function applyViewportTransform(
  context: CanvasRenderingContext2D,
  view: ViewportTransform,
  pixelRatio: number
): void {
  context.setTransform(
    pixelRatio / ANNOTATION_RESOLUTION,
    0,
    0,
    pixelRatio / ANNOTATION_RESOLUTION,
    view.left * pixelRatio,
    view.top * pixelRatio
  )
  const ratio = view.scale * ANNOTATION_RESOLUTION
  if (view.rotation === 90) {
    context.translate(view.imageHeight * ratio, 0)
    context.rotate(Math.PI / 2)
  } else if (view.rotation === 180) {
    context.translate(view.imageWidth * ratio, view.imageHeight * ratio)
    context.rotate(Math.PI)
  } else if (view.rotation === 270) {
    context.translate(0, view.imageWidth * ratio)
    context.rotate(-Math.PI / 2)
  }
}
