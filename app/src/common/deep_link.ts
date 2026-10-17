import { changeSelect, changeViewerConfig } from "../action/common"
import { QueryArg } from "../const/common"
import { isFrameLoaded } from "../functional/state_util"
import { ImageViewerConfigType, ItemType, State } from "../types/state"
import { MAX_SCALE, MIN_SCALE } from "../view_config/image"
import Session from "./session"

/** Padding (in multiples of the bbox size) kept on every side of the label */
export const DEEP_LINK_PADDING = 3
/** Smallest bbox side (image px) used for zoom maths, for thin/degenerate labels */
export const MIN_BBOX_SIDE = 20

/** Deep link request parsed from the URL */
export interface DeepLink {
  /** 0-based item index, or null when not requested */
  item: number | null
  /** Label id, or null when not requested */
  label: string | null
}

/**
 * Parse the optional `item` (1-based) and `label` query params
 *
 * @param search
 */
export function parseDeepLink(search: string): DeepLink {
  const params = new URLSearchParams(search)
  const itemParam = params.get(QueryArg.ITEM)
  let item: number | null = null
  if (itemParam !== null && /^\d+$/.test(itemParam.trim())) {
    const parsed = parseInt(itemParam.trim(), 10)
    if (parsed >= 1) {
      item = parsed - 1
    }
  }
  const labelParam = params.get(QueryArg.LABEL)
  const label =
    labelParam !== null && labelParam.trim() !== "" ? labelParam.trim() : null
  return { item, label }
}

/**
 * Bounding box [x1, y1, x2, y2] of a label in image pixels, or null
 *
 * @param item
 * @param labelId
 */
export function getLabelBBox(
  item: ItemType,
  labelId: string
): [number, number, number, number] | null {
  const label = item.labels[labelId]
  if (label === undefined) {
    return null
  }
  let x1 = Infinity
  let y1 = Infinity
  let x2 = -Infinity
  let y2 = -Infinity
  const add = (x: unknown, y: unknown): void => {
    if (typeof x === "number" && typeof y === "number") {
      x1 = Math.min(x1, x)
      y1 = Math.min(y1, y)
      x2 = Math.max(x2, x)
      y2 = Math.max(y2, y)
    }
  }
  for (const shapeId of label.shapes) {
    const shape = item.shapes[shapeId] as unknown as Record<string, unknown>
    if (shape === undefined) {
      continue
    }
    add(shape.x, shape.y)
    add(shape.x1, shape.y1)
    add(shape.x2, shape.y2)
    if (Array.isArray(shape.points)) {
      for (const p of shape.points as Array<{ x: number; y: number }>) {
        add(p.x, p.y)
      }
    }
  }
  return x1 <= x2 && y1 <= y2 ? [x1, y1, x2, y2] : null
}

/**
 * Compute the viewScale/displayLeft/displayTop (see Viewer2D.zoom) that
 * centre the bbox in the container, showing it padded by `padding` bbox
 * sizes on each side, never zooming out past "fit image".
 *
 * @param bbox
 * @param imageW
 * @param imageH
 * @param rectW
 * @param rectH
 * @param padding
 */
export function bboxToViewport(
  bbox: [number, number, number, number],
  imageW: number,
  imageH: number,
  rectW: number,
  rectH: number,
  padding: number = DEEP_LINK_PADDING
): { viewScale: number; displayLeft: number; displayTop: number } {
  const bw = Math.max(bbox[2] - bbox[0], MIN_BBOX_SIDE)
  const bh = Math.max(bbox[3] - bbox[1], MIN_BBOX_SIDE)
  const cx = (bbox[0] + bbox[2]) / 2
  const cy = (bbox[1] + bbox[3]) / 2
  // Image px -> CSS px at viewScale 1 ("fit image")
  const fit = Math.min(rectW / imageW, rectH / imageH)
  const wanted = Math.min(
    rectW / (bw * (1 + 2 * padding)),
    rectH / (bh * (1 + 2 * padding))
  )
  const viewScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, wanted / fit))
  const ratio = fit * viewScale
  const w = imageW * ratio
  const h = imageH * ratio
  const padX = Math.max(0, (rectW - w) / 2)
  const padY = Math.max(0, (rectH - h) / 2)
  let displayLeft = rectW / 2 - cx * ratio - padX
  let displayTop = rectH / 2 - cy * ratio - padY
  // Same clamp as Viewer2D.zoom: never pull the image past the viewport edges
  const loL = Math.min(-padX, rectW - w - padX)
  const hiL = Math.max(-padX, rectW - w - padX)
  const loT = Math.min(-padY, rectH - h - padY)
  const hiT = Math.max(-padY, rectH - h - padY)
  displayLeft = Math.min(hiL, Math.max(loL, displayLeft))
  displayTop = Math.min(hiT, Math.max(loT, displayTop))
  return { viewScale, displayLeft, displayTop }
}

/**
 * Apply the deep link once the task, frame image and viewer are ready.
 * Only changes selection and view; never touches labels or task data.
 * Must be called after Session.store is set.
 *
 * @param link
 */
export function applyDeepLinkWhenReady(link: DeepLink): void {
  if (link.item === null && link.label === null) {
    return
  }
  let done = false
  let navigated = false
  const attempt = (): void => {
    if (done) {
      return
    }
    const state: State = Session.getState()
    const items = state.task.items
    if (items.length === 0) {
      return
    }
    let target = link.item
    if (target !== null && target >= items.length) {
      target = null
    }
    if (target === null && link.label !== null) {
      const cur = state.user.select.item
      if (link.label in (items[cur]?.labels ?? {})) {
        target = cur
      } else if (link.item === null) {
        const wanted = link.label
        const found = items.find((it) => wanted in it.labels)
        target = found !== undefined ? found.index : null
      }
    }
    if (target === null) {
      done = true
      return
    }
    if (!navigated && state.user.select.item !== target) {
      navigated = true
      Session.dispatch(changeSelect({ item: target, labels: {}, shapes: {} }))
      return
    }
    const viewerId = Object.keys(state.user.viewerConfigs)
      .map(Number)
      .find((id) => "viewScale" in state.user.viewerConfigs[id])
    if (viewerId === undefined || state.user.select.item !== target) {
      return
    }
    const config = state.user.viewerConfigs[viewerId] as ImageViewerConfigType
    if (!isFrameLoaded(state, target, config.sensor)) {
      return
    }
    done = true
    const labelId =
      link.label !== null && link.label in items[target].labels
        ? link.label
        : null
    if (labelId === null) {
      return
    }
    Session.dispatch(changeSelect({ labels: { [target]: [labelId] } }))
    const bbox = getLabelBBox(items[target], labelId)
    const image = Session.images[target]?.[config.sensor]
    const container = document.querySelector("[data-viewer-container]")
    if (bbox === null || image === undefined || container === null) {
      return
    }
    const rect = container.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) {
      return
    }
    const vp = bboxToViewport(
      bbox,
      image.width,
      image.height,
      rect.width,
      rect.height
    )
    Session.dispatch(changeViewerConfig(viewerId, { ...config, ...vp }))
  }
  Session.subscribe(attempt)
  attempt()
}
