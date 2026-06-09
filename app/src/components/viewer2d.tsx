import { IconButton } from "@material-ui/core"
import Tooltip from "@mui/material/Tooltip"
import Fade from "@mui/material/Fade"
import AddIcon from "@material-ui/icons/Add"
import FindReplaceIcon from "@material-ui/icons/FindReplace"
import LineWeightIcon from "@material-ui/icons/LineWeight"
import RemoveIcon from "@material-ui/icons/Remove"
import ZoomInIcon from "@material-ui/icons/ZoomIn"
import ZoomOutIcon from "@material-ui/icons/ZoomOut"
import { withStyles } from "@material-ui/styles"
import React from "react"

import { changeViewerConfig } from "../action/common"
import Session from "../common/session"
import { notifyGesture } from "../common/interaction_state"
import { isFrameLoaded } from "../functional/state_util"
import {
  isArmed,
  markPanned,
  didPan,
  reset as resetPanState,
  exceededThreshold,
  openPanWindow,
  inPanWindow
} from "../common/pointer_pan_state"
import * as types from "../const/common"
import { Vector2D } from "../math/vector2d"
import { viewerStyles } from "../styles/viewer"
import { ImageViewerConfigType } from "../types/state"
import {
  MAX_SCALE,
  MIN_SCALE,
  SCROLL_ZOOM_RATIO,
  ZOOM_RATIO
} from "../view_config/image"
import {
  DrawableViewer,
  ViewerClassTypes,
  ViewerProps
} from "./drawable_viewer"
import ImageCanvas from "./image_canvas"
import Label2dCanvas from "./label2d_canvas"

interface ClassType extends ViewerClassTypes {
  /** buttons */
  viewer_button: string
}

export interface Viewer2DProps extends ViewerProps {
  /** classes */
  classes: ClassType
}

/**
 * Viewer for images and 2d labels
 */
export class Viewer2D extends DrawableViewer<Viewer2DProps> {
  /** Accumulated zoom ratio waiting for the next animation frame */
  private _pendingZoomRatio: number = 1
  /** Latest wheel cursor (viewport client coords) for the pending zoom */
  private _pendingZoomClientX: number = 0
  /** Latest wheel cursor (viewport client coords) for the pending zoom */
  private _pendingZoomClientY: number = 0
  /** Whether a requestAnimationFrame has already been scheduled for zoom */
  private _zoomRAFPending: boolean = false
  /** pending pan offset accumulated within a frame */
  private _pendingPan: { left: number; top: number } | null = null
  /** whether a pan RAF is already scheduled */
  private _panRAFPending: boolean = false

  /**
   * Render function
   *
   * @return {React.Fragment} React fragment
   */
  protected getDrawableComponents(): React.ReactElement[] {
    if (this._container !== null && this._viewerConfig !== undefined) {
      const config = this._viewerConfig as ImageViewerConfigType
      const { displayLeft, displayTop } = config

      const content = this._container.firstElementChild as HTMLElement
      content.style.marginLeft = `${displayLeft}px`
      content.style.marginTop = `${displayTop}px`
    }

    const views: React.ReactElement[] = []
    if (this._viewerConfig !== undefined) {
      views.push(
        <ImageCanvas
          key={`imageCanvas${this.props.id}`}
          display={this._container}
          id={this.props.id}
        />
      )
      views.push(
        <Label2dCanvas
          key={`label2dCanvas${this.props.id}`}
          display={this._container}
          id={this.props.id}
        />
      )
    }

    return views
  }

  /**
   * Render function
   *
   * @return {React.Fragment} React fragment
   */
  protected getMenuComponents(): JSX.Element[] | [] {
    if (this._viewerConfig !== undefined) {
      const zoomInButton = (
        <Tooltip
          key={`zoomIn2dButton${this.props.id}`}
          title="Zoom in"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => {
              if (this._container !== null) {
                const rect = this._container.getBoundingClientRect()
                let zoomRatio = SCROLL_ZOOM_RATIO
                if (
                  this.isKeyDown(types.Key.META) ||
                  this.isKeyDown(types.Key.CONTROL)
                ) {
                  zoomRatio = ZOOM_RATIO
                }
                this.zoom(
                  zoomRatio,
                  new Vector2D(rect.width / 2, rect.height / 2)
                )
              }
            }}
            className={this.props.classes.viewer_button}
          >
            <ZoomInIcon />
          </IconButton>
        </Tooltip>
      )
      const zoomOutButton = (
        <Tooltip
          key={`zoomOut2dButton${this.props.id}`}
          title="Zoom out"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => {
              if (this._container !== null) {
                const rect = this._container.getBoundingClientRect()
                let zoomRatio = 1 / SCROLL_ZOOM_RATIO
                if (
                  this.isKeyDown(types.Key.META) ||
                  this.isKeyDown(types.Key.CONTROL)
                ) {
                  zoomRatio = 1 / ZOOM_RATIO
                }
                this.zoom(
                  zoomRatio,
                  new Vector2D(rect.width / 2, rect.height / 2)
                )
              }
            }}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <ZoomOutIcon />
          </IconButton>
        </Tooltip>
      )
      const resetZoomButton = (
        <Tooltip
          key={`resetZoom2dButton${this.props.id}`}
          title="Reset zoom"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => {
              const config = this._viewerConfig as ImageViewerConfigType
              const newConfig = { ...config }
              newConfig.displayLeft = 0
              newConfig.displayTop = 0
              newConfig.viewScale = 1
              Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
            }}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <FindReplaceIcon />
          </IconButton>
        </Tooltip>
      )
      const widthUpButton = (
        <Tooltip
          key={`widthUp2dButton${this.props.id}`}
          title="Thicker lines"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(0.5)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <AddIcon />
          </IconButton>
        </Tooltip>
      )
      const widthDownButton = (
        <Tooltip
          key={`widthDown2dButton${this.props.id}`}
          title="Thinner lines"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(-0.5)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <RemoveIcon />
          </IconButton>
        </Tooltip>
      )
      const widthResetButton = (
        <Tooltip
          key={`widthReset2dButton${this.props.id}`}
          title="Reset line width"
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => this.changeLineWidth(0, true)}
            className={this.props.classes.viewer_button}
            edge={"start"}
          >
            <LineWeightIcon />
          </IconButton>
        </Tooltip>
      )
      return [
        zoomInButton,
        zoomOutButton,
        resetZoomButton,
        widthUpButton,
        widthDownButton,
        widthResetButton
      ]
    }
    return []
  }

  /**
   * Handle mouse move
   *
   * @param e
   */
  protected onMouseMove(e: React.MouseEvent): void {
    const oldX = this._mX
    const oldY = this._mY
    super.onMouseMove(e)
    if (
      this._mouseDown &&
      this._container !== null &&
      this._viewerConfig !== undefined
    ) {
      // Read the modifier from the event (see onWheel) so ctrl+drag pan works
      // immediately, without first clicking the iframe to focus it.
      const allowPan =
        e.ctrlKey ||
        e.metaKey ||
        didPan() ||
        inPanWindow(Date.now()) ||
        (isArmed() && exceededThreshold(this._mX, this._mY))
      if (allowPan) {
        markPanned()
        const dx = this._mX - oldX
        const dy = this._mY - oldY

        notifyGesture()
        // Accumulate raw deltas within the frame so fast drags don't drop
        // sub-frame movement. _pendingPan holds the SUMMED delta; the RAF
        // applies it on top of the latest committed config. (Storing an
        // absolute snapshot off a stale displayLeft would discard every
        // mousemove except the last one before the frame ticked.)
        if (this._pendingPan === null) {
          this._pendingPan = { left: dx, top: dy }
        } else {
          this._pendingPan.left += dx
          this._pendingPan.top += dy
        }
        if (!this._panRAFPending) {
          this._panRAFPending = true
          requestAnimationFrame(() => {
            this._panRAFPending = false
            const pan = this._pendingPan
            this._pendingPan = null
            if (pan === null) {
              return
            }
            const rafConfig = this._viewerConfig as ImageViewerConfigType
            const newConfig = {
              ...rafConfig,
              displayLeft: rafConfig.displayLeft + pan.left,
              displayTop: rafConfig.displayTop + pan.top
            }
            Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
          })
        }
      }
    }
  }

  /**
   * Handle double click
   *
   * @param e
   */
  protected onDoubleClick(): void {
    // A drag begun shortly after a double-click pans anywhere (trackpad-friendly).
    openPanWindow(Date.now())
  }

  /**
   * Handle mouse up
   *
   * @param e
   */
  protected onMouseUp(e: React.MouseEvent): void {
    resetPanState()
    super.onMouseUp(e)
  }

  /**
   * Handle key down
   *
   * @param e
   */
  protected onKeyDown(e: KeyboardEvent): void {
    super.onKeyDown(e)
    switch (e.key) {
      case types.Key.EQUAL:
      case types.Key.PLUS:
        this.zoom(ZOOM_RATIO, new Vector2D(this._mX, this._mY))
        break
      case types.Key.MINUS:
        this.zoom(1 / ZOOM_RATIO, new Vector2D(this._mX, this._mY))
    }
    e.stopPropagation()
  }

  /**
   * Handle mouse leave
   *
   * @param e
   */
  protected onMouseLeave(): void {}

  /**
   * Handle mouse wheel
   *
   * @param e
   */
  protected onWheel(e: WheelEvent): void {
    notifyGesture()
    e.preventDefault()
    if (this._viewerConfig !== undefined && this._container !== null) {
      // Read the modifier from the event itself rather than tracked key state.
      // In an embedded iframe the document only receives keydown events once the
      // iframe is focused (first click), so isKeyDown(CTRL) stays false until
      // then — which blocked ctrl+scroll zoom on load. e.ctrlKey is always
      // current, and trackpad pinch-zoom also arrives as a wheel event with
      // ctrlKey set, so this enables pinch-to-zoom too.
      if (e.ctrlKey || e.metaKey) {
        let zoomRatio = SCROLL_ZOOM_RATIO
        if (-e.deltaY < 0) {
          zoomRatio = 1 / zoomRatio
        }
        // Accumulate all scroll ticks that arrive within the same animation
        // frame. Without this, fast scrolling fires 60-120 Redux dispatches
        // per second each triggering a full canvas repaint, which is the root
        // cause of lag at high zoom. Batching into one rAF means exactly one
        // repaint per rendered frame regardless of scroll speed.
        this._pendingZoomRatio *= zoomRatio
        // Store only the raw cursor coords here. Computing the container-
        // relative offset needs getBoundingClientRect(), which forces a
        // synchronous layout; doing that on every wheel event (60-120/sec
        // during a pinch) thrashes layout and causes the zoom lag. Defer it to
        // the once-per-frame RAF below.
        this._pendingZoomClientX = e.clientX
        this._pendingZoomClientY = e.clientY
        if (!this._zoomRAFPending) {
          this._zoomRAFPending = true
          requestAnimationFrame(() => {
            this._zoomRAFPending = false
            if (this._container === null) {
              this._pendingZoomRatio = 1
              return
            }
            const rect = this._container.getBoundingClientRect()
            const offset = new Vector2D(
              this._pendingZoomClientX - rect.left,
              this._pendingZoomClientY - rect.top
            )
            this.zoom(this._pendingZoomRatio, offset)
            this._pendingZoomRatio = 1
          })
        }
      }
    }
  }

  /**
   * Zoom
   *
   * @param zoomRatio
   * @param offset
   */
  protected zoom(zoomRatio: number, offset: Vector2D): void {
    notifyGesture()
    const config = this._viewerConfig as ImageViewerConfigType
    const newScale = config.viewScale * zoomRatio
    const newConfig = { ...config }
    if (newScale >= MIN_SCALE && newScale <= MAX_SCALE) {
      newConfig.viewScale = newScale

      const item = this.state.user.select.item
      const sensor = this.state.user.viewerConfigs[this.props.id].sensor

      // Guard: skip the offset math if the image hasn't loaded yet (avoids a
      // crash on early keyboard/button/wheel zoom). The scale still updates.
      if (this._container !== null && isFrameLoaded(this.state, item, sensor)) {
        const rect = this._container.getBoundingClientRect()
        const image = Session.images[item][sensor]
        const imageAspect = image.width / image.height

        // Displayed image size + centering padding (CSS px) at a given scale,
        // matching updateCanvasScale's letterboxing.
        const dims = (
          s: number
        ): { w: number; h: number; padX: number; padY: number } => {
          let w: number
          let h: number
          if (rect.width / rect.height > imageAspect) {
            h = rect.height * s
            w = h * imageAspect
          } else {
            w = rect.width * s
            h = w / imageAspect
          }
          return {
            w,
            h,
            padX: Math.max(0, (rect.width - w) / 2),
            padY: Math.max(0, (rect.height - h) / 2)
          }
        }
        const cur = dims(config.viewScale)
        const next = dims(newScale)

        // Cursor-focal zoom: keep the image point under the cursor fixed.
        // `offset` is the cursor relative to the container. Convert it to an
        // image-relative point by subtracting the current pan + padding, scale
        // that point, then re-add the new padding. (The previous formula had
        // the offset sign inverted, so zooming drifted toward the top.)
        let displayLeft =
          offset.x -
          next.padX -
          (offset.x - config.displayLeft - cur.padX) * zoomRatio
        let displayTop =
          offset.y -
          next.padY -
          (offset.y - config.displayTop - cur.padY) * zoomRatio

        // Clamp so the image can't be pulled past the viewport edges into blank.
        const loL = Math.min(-next.padX, rect.width - next.w - next.padX)
        const hiL = Math.max(-next.padX, rect.width - next.w - next.padX)
        const loT = Math.min(-next.padY, rect.height - next.h - next.padY)
        const hiT = Math.max(-next.padY, rect.height - next.h - next.padY)
        displayLeft = Math.min(hiL, Math.max(loL, displayLeft))
        displayTop = Math.min(hiT, Math.max(loT, displayTop))

        newConfig.displayLeft = displayLeft
        newConfig.displayTop = displayTop
      }

      Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
    }
  }

  /**
   * Change the polyline line-width multiplier (display-only).
   *
   * @param delta additive change applied to the current value
   * @param reset when true, reset the multiplier to 1
   */
  protected changeLineWidth(delta: number, reset = false): void {
    const config = this._viewerConfig as ImageViewerConfigType
    const current = config.lineWidthMultiplier ?? 1
    const value = reset
      ? 1
      : Math.min(4, Math.max(0.5, Math.round((current + delta) * 10) / 10))
    const newConfig = { ...config, lineWidthMultiplier: value }
    Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
  }
}

export default withStyles(viewerStyles)(Viewer2D)
