import Menu from "@material-ui/core/Menu"
import MenuItem from "@material-ui/core/MenuItem"
import { withStyles } from "@material-ui/core/styles"
import * as React from "react"
import { connect } from "react-redux"

import { drawHistory } from "../common/draw_history"
import Session from "../common/session"
import { isInteracting, onIdle } from "../common/interaction_state"
import {
  armEmptyDrag,
  isArmed,
  didPan,
  reset as resetPanState,
  inPanWindow
} from "../common/pointer_pan_state"
import { isCutMode, setCutMode } from "../common/cut_state"
import {
  armSegmentDelete,
  getPickData,
  getPreviewData,
  getSegmentDeletePhase,
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  resetSegmentDelete
} from "../common/segment_delete_state"
import {
  CUT_CLICK_RADIUS_PX,
  CUT_SNAP_RADIUS_PX,
  performCut
} from "../drawable/2d/polyline_cut"
import {
  commitPendingSegmentDelete,
  handleSegmentDeletePick,
  SEGMENT_DELETE_PREVIEW_MS
} from "../drawable/2d/polyline_segment_delete"
import { DASH_LINE } from "../drawable/2d/common"
import { ContentCutIcon, CUT_CURSOR, DeleteSegmentIcon } from "./cut_icon"
import { Key } from "../const/common"
import { Label2DHandler } from "../drawable/2d/label2d_handler"
import { Label2DList } from "../drawable/2d/label2d_list"
import { getCurrentViewerConfig, isFrameLoaded } from "../functional/state_util"
import { Vector2D } from "../math/vector2d"
import { label2dViewStyle } from "../styles/label"
import { ImageViewerConfigType, State } from "../types/state"
import {
  clearCanvas,
  getCurrentImageSize,
  imageDataToHandleId,
  MAX_SCALE,
  MIN_SCALE,
  normalizeMouseCoordinates,
  toCanvasCoords,
  updateCanvasScale
} from "../view_config/image"
import { Crosshair, Crosshair2D } from "./crosshair"
import {
  DrawableCanvas,
  DrawableProps,
  mapStateToDrawableProps
} from "./viewer"
import { isKeyFrame } from "./util"
import { alert } from "../common/alert"
import { Severity } from "../types/common"

interface ClassType {
  /** label canvas */
  label2d_canvas: string
  /** control canvas */
  control_canvas: string
}

interface Props extends DrawableProps {
  /** styles */
  classes: ClassType
  /** display */
  display: HTMLDivElement | null
  /** viewer id */
  id: number
}

/**
 * Canvas Viewer
 */
export class Label2dCanvas extends DrawableCanvas<Props> {
  /** The label context */
  public labelContext: CanvasRenderingContext2D | null
  /** The control context */
  public controlContext: CanvasRenderingContext2D | null

  /** drawable label list */
  private readonly _labelList: Label2DList
  /** drawing action handler */
  private readonly _labelHandler: Label2DHandler
  /** The label canvas */
  private labelCanvas: HTMLCanvasElement | null
  /** The control canvas */
  private controlCanvas: HTMLCanvasElement | null
  /** The mask to hold the display */
  private display: HTMLDivElement | null

  // Display variables
  /** The current scale */
  private scale: number
  /** The canvas height */
  private canvasHeight: number
  /** The canvas width */
  private canvasWidth: number
  /** The scale between the display and image data */
  private displayToImageRatio: number
  /** The crosshair */
  private readonly crosshair: React.RefObject<Crosshair2D>
  /** key up listener */
  private readonly _keyUpListener: (e: KeyboardEvent) => void
  /** key down listener */
  private readonly _keyDownListener: (e: KeyboardEvent) => void
  /** effective up-resolution ratio (adaptive based on zoom level) */
  private _upResRatio: number

  // Keyboard and mouse status
  /** The hashed list of keys currently down */
  private _keyDownMap: { [key: string]: boolean }
  /** drawable callback */
  private readonly _drawableUpdateCallback: () => void
  /** unsubscribe from interaction-idle notifications */
  private _offIdle: (() => void) | null = null
  /** last seen item index, to disarm the cut tools on item navigation */
  private _cutItemIndex: number = -1
  /** context-menu anchor (viewport px), null while the menu is closed */
  private _menuAnchor: { left: number; top: number } | null = null
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDelete: (() => void) | null = null
  /** pending commit timer for the delete-segment preview */
  private _segmentDeleteTimer: number | null = null
  /** rAF handle for the marching-ants animation */
  private _antsRAF: number | null = null
  /** marching-ants dash offset (canvas px) */
  private _antsOffset: number = 0

  /**
   * Constructor, handles subscription to store
   *
   * @param {Object} props: react props
   * @param props
   */
  constructor(props: Readonly<Props>) {
    super(props)

    // Constants

    // Initialization
    this._keyDownMap = {}
    this.scale = 1
    this.canvasHeight = 0
    this.canvasWidth = 0
    this.displayToImageRatio = 1
    this._upResRatio = 2
    this.controlContext = null
    this.controlCanvas = null
    this.labelContext = null
    this.labelCanvas = null
    this.display = this.props.display
    this._labelList = Session.label2dList
    this._labelHandler = new Label2DHandler(this._labelList)
    this.crosshair = React.createRef()

    this._keyUpListener = (e) => {
      this.onKeyUp(e)
    }
    this._keyDownListener = (e) => {
      this.onKeyDown(e)
    }
    this._drawableUpdateCallback = this.redraw.bind(this)
  }

  /**
   * Component mount callback
   */
  public componentDidMount(): void {
    super.componentDidMount()
    document.addEventListener("keydown", this._keyDownListener)
    document.addEventListener("keyup", this._keyUpListener)
    this._labelList.subscribe(this._drawableUpdateCallback)
    // Use forceUpdate (not redraw) so the gesture-settled repaint goes through
    // render -> updateScale, which resizes the canvas back to full resolution
    // and refreshes _upResRatio. Calling redraw() directly would repaint at the
    // stale 0.7x motion resolution, leaving labels blurry until the next state
    // change. Mirrors ImageCanvas's idle handler.
    this._offIdle = onIdle(() => this.forceUpdate())
    this._offSegmentDelete = onSegmentDeleteChange(() =>
      this.onSegmentDeleteStateChange()
    )
  }

  /**
   * Unmount callback
   */
  public componentWillUnmount(): void {
    super.componentWillUnmount()
    document.removeEventListener("keydown", this._keyDownListener)
    document.removeEventListener("keyup", this._keyUpListener)
    this._labelList.unsubscribe(this._drawableUpdateCallback)
    if (this._offIdle !== null) {
      this._offIdle()
      this._offIdle = null
    }
    if (this._offSegmentDelete !== null) {
      this._offSegmentDelete()
      this._offSegmentDelete = null
    }
    this.clearSegmentDeleteTimers()
  }

  /**
   * Set the current cursor
   *
   * @param {string} cursor - cursor type
   */
  public setCursor(cursor: string): void {
    if (this.labelCanvas !== null) {
      this.labelCanvas.style.cursor = cursor
    }
  }

  /**
   * Set the current cursor to default
   */
  public setDefaultCursor(): void {
    this.setCursor("crosshair")
  }

  /**
   * Render function
   *
   * @return {React.Fragment} React fragment
   */
  public render(): JSX.Element[] {
    const { classes } = this.props
    let controlCanvas = (
      <canvas
        key="control-canvas"
        className={classes.control_canvas}
        ref={(canvas) => {
          if (canvas !== null) {
            this.updateCanvas(canvas, true)
          }
        }}
      />
    )
    let labelCanvas = (
      <canvas
        key="label2d-canvas"
        className={classes.label2d_canvas}
        ref={(canvas) => {
          if (canvas !== null) {
            this.updateCanvas(canvas, false)
          }
        }}
        onMouseDown={(e) => {
          this.onMouseDown(e)
        }}
        onMouseUp={(e) => {
          this.onMouseUp(e)
        }}
        onMouseMove={(e) => {
          this.onMouseMove(e)
        }}
        onContextMenu={(e) => {
          this.onContextMenu(e)
        }}
      />
    )
    const ch = (
      <Crosshair
        key="crosshair-canvas"
        display={this.display}
        innerRef={this.crosshair}
      />
    )
    if (this.display !== null) {
      const displayRect = this.display.getBoundingClientRect()
      controlCanvas = React.cloneElement(controlCanvas, {
        height: displayRect.height,
        width: displayRect.width
      })
      labelCanvas = React.cloneElement(labelCanvas, {
        height: displayRect.height,
        width: displayRect.width
      })
    }

    const contextMenu = (
      <Menu
        key="cut-context-menu"
        open={this._menuAnchor !== null}
        onClose={() => {
          this._menuAnchor = null
          this.forceUpdate()
        }}
        anchorReference="anchorPosition"
        anchorPosition={
          this._menuAnchor !== null ? this._menuAnchor : undefined
        }
      >
        <MenuItem
          dense
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking
          }
          onClick={() => {
            this._menuAnchor = null
            setCutMode(true)
            this.setCursor(CUT_CURSOR)
            this.forceUpdate()
          }}
        >
          <ContentCutIcon fontSize="small" style={{ marginRight: 8 }} />
          Cut polyline
        </MenuItem>
        <MenuItem
          dense
          disabled={
            Session.label2dList.isDrawingInProgress() ||
            this.state.task.config.tracking
          }
          onClick={() => {
            this._menuAnchor = null
            armSegmentDelete()
            this.setCursor(CUT_CURSOR)
            this.forceUpdate()
          }}
        >
          <DeleteSegmentIcon fontSize="small" style={{ marginRight: 8 }} />
          Delete segment
        </MenuItem>
      </Menu>
    )

    return [ch, controlCanvas, labelCanvas, contextMenu]
  }

  /**
   * Function to redraw all canvases
   *
   * @return {boolean}
   */
  public redraw(): boolean {
    this.clear()
    if (
      this.labelCanvas !== null &&
      this.labelContext !== null &&
      this.controlCanvas !== null &&
      this.controlContext !== null
    ) {
      const config = this.state.user.viewerConfigs[this.props.id]
      const mode = this.state.session.mode
      const viewScale =
        "viewScale" in config
          ? (config as unknown as { viewScale: number }).viewScale
          : 1
      const hiddenLabelTypes: string[] =
        config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : []
      const hiddenCategories: number[] =
        config.hiddenCategories !== undefined ? config.hiddenCategories : []
      const lineWidthMultiplier: number =
        "lineWidthMultiplier" in config &&
        (config as unknown as { lineWidthMultiplier?: number })
          .lineWidthMultiplier !== undefined
          ? (config as unknown as { lineWidthMultiplier: number })
              .lineWidthMultiplier
          : 1

      // Compute viewport bounds in image coordinates for culling
      let viewportBounds: [number, number, number, number] | undefined
      if (
        viewScale > 2 &&
        this.display !== null &&
        "displayLeft" in config &&
        "displayTop" in config
      ) {
        const displayRect = this.display.getBoundingClientRect()
        const imgConfig = config as unknown as {
          displayLeft: number
          displayTop: number
        }
        // Viewport in image coordinates
        // Note: displayToImageRatio already includes viewScale factor
        // displayLeft/Top are CSS pixel offsets (negative when panned)
        const ratio = this.displayToImageRatio
        const viewportX = -imgConfig.displayLeft / ratio
        const viewportY = -imgConfig.displayTop / ratio
        const viewportW = displayRect.width / ratio
        const viewportH = displayRect.height / ratio
        viewportBounds = [viewportX, viewportY, viewportW, viewportH]
      }

      this._labelList.redraw(
        this.labelContext,
        this.controlContext,
        this.displayToImageRatio * this._upResRatio,
        config.hideLabels,
        config.hideTags,
        mode,
        viewScale,
        viewportBounds,
        hiddenLabelTypes,
        hiddenCategories,
        !isInteracting(),
        lineWidthMultiplier
      )
      this.drawSegmentDeleteOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
    }
    return true
  }

  /**
   * Clear canvas
   */
  public clear(): void {
    if (
      this.labelCanvas !== null &&
      this.labelContext !== null &&
      this.controlCanvas !== null &&
      this.controlContext !== null
    ) {
      clearCanvas(this.labelCanvas, this.labelContext)
      clearCanvas(this.controlCanvas, this.controlContext)
    }
  }

  /**
   * Callback function when mouse is down
   *
   * @param {MouseEvent} e - event
   */
  public onMouseDown(e: React.MouseEvent<HTMLCanvasElement>): void {
    if (e.button !== 0 || this.checkFreeze()) {
      return
    }
    if (
      !isKeyFrame(
        this.state.user.select.item,
        this.state.task.config.keyInterval
      )
    ) {
      alert(
        Severity.WARNING,
        "You can not edit label in non-keyframe, please press CTRL + left/right arrow to the nearest keyframe"
      )
      return
    }
    // Control + click for dragging
    // get mouse position in image coordinates
    const mousePos = this.getMousePos(e)
    const [labelIndex, handleIndex] = this.fetchHandleId(mousePos)
    console.log("[DEBUG] Canvas.onMouseDown hit testing:", {
      mousePos,
      labelIndex,
      handleIndex,
      inPanWindow: inPanWindow(Date.now()),
      hasSelectedLabels: this._labelHandler["hasSelectedLabels"](),
      isEditingSelectedLabels: this._labelHandler["isEditingSelectedLabels"]()
    })
    resetPanState()
    // Control + click for dragging
    // get mouse position in image coordinates
    // Ctrl/Cmd drag pans anywhere via Viewer2D; never draw/edit on it.
    if (e.ctrlKey || e.metaKey) {
      return
    }
    // Delete-segment tool: while active, clicks are picks (or ignored
    // entirely during the preview countdown).
    if (isSegmentDeleteActive()) {
      if (getSegmentDeletePhase() === "preview") {
        return
      }
      const config = this.state.user.viewerConfigs[this.props.id]
      const outcome = handleSegmentDeletePick(
        mousePos,
        CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
        CUT_SNAP_RADIUS_PX / this.displayToImageRatio,
        {
          hideLabels: config.hideLabels,
          hiddenLabelTypes:
            config.hiddenLabelTypes !== undefined
              ? config.hiddenLabelTypes
              : [],
          hiddenCategories:
            config.hiddenCategories !== undefined
              ? config.hiddenCategories
              : []
        }
      )
      switch (outcome) {
        case "curve":
          alert(
            Severity.WARNING,
            "Cannot cut a curved segment — straighten it first."
          )
          break
        case "closed":
          alert(
            Severity.WARNING,
            "Delete segment works on open polylines only."
          )
          break
        case "wrong-line":
          alert(Severity.WARNING, "Pick both points on the same polyline.")
          break
        case "too-close":
          alert(Severity.WARNING, "Picked points are too close.")
          break
        case "stale":
          alert(
            Severity.WARNING,
            "The line changed — segment delete cancelled."
          )
          this.setDefaultCursor()
          break
        case "first-picked":
        case "preview-started":
        case "miss":
        case "ignored":
          break
      }
      return
    }
    // One-shot cut tool: while armed, this click belongs to the scissors.
    // It never starts a draw or select; a successful cut disarms the tool,
    // any rejection keeps it armed so the user can re-aim.
    if (isCutMode()) {
      const config = this.state.user.viewerConfigs[this.props.id]
      const result = performCut(
        mousePos,
        CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
        CUT_SNAP_RADIUS_PX / this.displayToImageRatio,
        {
          hideLabels: config.hideLabels,
          hiddenLabelTypes:
            config.hiddenLabelTypes !== undefined
              ? config.hiddenLabelTypes
              : [],
          hiddenCategories:
            config.hiddenCategories !== undefined
              ? config.hiddenCategories
              : []
        }
      )
      switch (result) {
        case "cut":
          setCutMode(false)
          this.setDefaultCursor()
          break
        case "curve":
          alert(
            Severity.WARNING,
            "Cannot cut a curved segment — straighten it first."
          )
          break
        case "closed":
          alert(Severity.WARNING, "Cut works on open polylines only.")
          break
        case "near-endpoint":
          alert(Severity.WARNING, "Too close to an endpoint to cut.")
          break
        case "miss":
          break
      }
      return
    }
    // Empty canvas, OR within the post-double-click pan window: defer the
    // action. A drag pans (Viewer2D, via the armed flag); a click replays the
    // draw/select in onMouseUp. Arming (rather than returning early) inside the
    // pan window ensures a click there is not silently dropped.
    if (labelIndex < 0 || inPanWindow(Date.now())) {
      console.log("[DEBUG] Canvas.onMouseDown REJECTED: labelIndex < 0 or inPanWindow", { labelIndex })
      const rect = (this.display as HTMLDivElement).getBoundingClientRect()
      armEmptyDrag(e.clientX - rect.left, e.clientY - rect.top)
      this.setCursor("grab")
      return
    }
    if (this._labelHandler.onMouseDown(mousePos, labelIndex, handleIndex)) {
      // Panning requires the event being propagated to upper view. Not sure
      // if there is any side-effect of this propagation. Let's see.
      // e.stopPropagation()
    }
    this._labelList.onDrawableUpdate()
  }

  /**
   * Callback function when mouse is up
   *
   * @param {MouseEvent} e - event
   */
  public onMouseUp(e: React.MouseEvent<HTMLCanvasElement>): void {
    if (e.button !== 0 || this.checkFreeze()) {
      return
    }

    if (isArmed()) {
      const panned = didPan()
      resetPanState()
      this.setDefaultCursor()
      if (!panned) {
        // It was a click, not a pan: perform the deferred draw now (down then
        // up) so an empty-space click still adds a polyline point.
        const pos = this.getMousePos(e)
        const [li, hi] = this.fetchHandleId(pos)
        this._labelHandler.onMouseDown(pos, li, hi)
        this._labelHandler.onMouseUp(pos, li, hi)
        this._labelList.onDrawableUpdate()
      }
      return
    }

    const mousePos = this.getMousePos(e)
    const [labelIndex, handleIndex] = this.fetchHandleId(mousePos)
    this._labelHandler.onMouseUp(mousePos, labelIndex, handleIndex)
    this._labelList.onDrawableUpdate()
  }

  /**
   * Callback function when mouse moves
   *
   * @param {MouseEvent} e - event
   */
  public onMouseMove(e: React.MouseEvent<HTMLCanvasElement>): void {
    if (this.checkFreeze()) {
      return
    }

    if (this.crosshair.current !== null) {
      this.crosshair.current.onMouseMove(e)
    }

    if (isArmed()) {
      // While a deferred empty-space gesture is in progress, do not draw/edit.
      // Viewer2D decides pan-vs-nothing from the movement threshold.
      return
    }

    // Update the currently hovered shape
    const mousePos = this.getMousePos(e)
    const [labelIndex, handleIndex] = this.fetchHandleId(mousePos)
    if (
      this._labelHandler.onMouseMove(
        mousePos,
        getCurrentImageSize(this.state, this.props.id),
        labelIndex,
        handleIndex
      )
    ) {
      // Panning requires the event being propagated to upper view. Not sure
      // if there is any side-effect of this propagation. Let's see.
      // e.stopPropagation()
    }
    this._labelList.onDrawableUpdate()

    if (this._labelHandler.highlightedLabel !== null) {
      this.setCursor(this._labelHandler.highlightedLabel.highlightCursor)
    } else {
      this.setDefaultCursor()
    }

    if (isCutMode() || isSegmentDeleteActive()) {
      // The scissors cursor overrides hover cursors while a tool is armed.
      this.setCursor(CUT_CURSOR)
    }
  }

  /**
   * Open the canvas context menu on right-click. The menu's only entry arms
   * the one-shot cut tool — arming via menu beats cutting at the right-click
   * point because precisely right-clicking a thin polyline is hard.
   *
   * @param {MouseEvent} e - event
   */
  public onContextMenu(e: React.MouseEvent<HTMLCanvasElement>): void {
    e.preventDefault()
    if (this.checkFreeze()) {
      return
    }
    this._menuAnchor = { left: e.clientX, top: e.clientY }
    this.forceUpdate()
  }

  /**
   * React to delete-segment phase changes: start the commit countdown and the
   * marching-ants animation when a preview begins; tear both down on any
   * other transition (cancel, commit, disarm).
   */
  private onSegmentDeleteStateChange(): void {
    if (getSegmentDeletePhase() === "preview") {
      if (this._segmentDeleteTimer === null) {
        this._segmentDeleteTimer = window.setTimeout(() => {
          this._segmentDeleteTimer = null
          const outcome = commitPendingSegmentDelete()
          if (outcome === "stale") {
            alert(
              Severity.WARNING,
              "The line changed — segment delete cancelled."
            )
          }
          this.setDefaultCursor()
        }, SEGMENT_DELETE_PREVIEW_MS)
      }
      if (this._antsRAF === null) {
        const step = (): void => {
          this._antsOffset += 0.75
          this.redraw()
          this._antsRAF =
            getSegmentDeletePhase() === "preview"
              ? window.requestAnimationFrame(step)
              : null
        }
        this._antsRAF = window.requestAnimationFrame(step)
      }
    } else {
      this.clearSegmentDeleteTimers()
      // Repaint to add/remove the pick halo or erase the overlay.
      this.redraw()
    }
  }

  /**
   * Clear the delete-segment preview timer and animation, if running.
   */
  private clearSegmentDeleteTimers(): void {
    if (this._segmentDeleteTimer !== null) {
      window.clearTimeout(this._segmentDeleteTimer)
      this._segmentDeleteTimer = null
    }
    if (this._antsRAF !== null) {
      window.cancelAnimationFrame(this._antsRAF)
      this._antsRAF = null
    }
  }

  /**
   * Draw one green pick halo (matches the endpoint-snap indicator styling).
   *
   * @param context the label canvas context
   * @param x halo center x (canvas px)
   * @param y halo center y (canvas px)
   */
  private drawPickHalo(
    context: CanvasRenderingContext2D,
    x: number,
    y: number
  ): void {
    context.beginPath()
    context.strokeStyle = "rgba(0, 255, 0, 0.8)"
    context.fillStyle = "rgba(0, 255, 0, 0.2)"
    context.lineWidth = 2
    context.arc(x, y, 12, 0, 2 * Math.PI)
    context.fill()
    context.stroke()
    context.beginPath()
    context.fillStyle = "rgba(0, 255, 0, 0.9)"
    context.arc(x, y, 5, 0, 2 * Math.PI)
    context.fill()
  }

  /**
   * Draw the delete-segment overlay: a green halo on the first pick while
   * waiting for the second and, during the preview, the doomed piece as a
   * green dashed marching-ants path with halos on BOTH picked points (the
   * doomed path's ends are exactly the two pick coordinates). Drawn after
   * the labels so it always sits on top.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawSegmentDeleteOverlay(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    const pickData = getPickData()
    if (pickData === null) {
      return
    }
    context.save()
    const preview = getPreviewData()
    if (preview !== null && preview.doomed.length >= 2) {
      context.beginPath()
      context.strokeStyle = "rgba(0, 230, 0, 0.95)"
      context.lineWidth = 4
      context.setLineDash(DASH_LINE)
      context.lineDashOffset = -this._antsOffset
      context.moveTo(preview.doomed[0].x * ratio, preview.doomed[0].y * ratio)
      for (let i = 1; i < preview.doomed.length; i++) {
        context.lineTo(
          preview.doomed[i].x * ratio,
          preview.doomed[i].y * ratio
        )
      }
      context.stroke()
      // Halos on both picked points, on top of the dashed path.
      context.setLineDash([])
      const first = preview.doomed[0]
      const last = preview.doomed[preview.doomed.length - 1]
      this.drawPickHalo(context, first.x * ratio, first.y * ratio)
      this.drawPickHalo(context, last.x * ratio, last.y * ratio)
    } else {
      this.drawPickHalo(
        context,
        pickData.pick1Point.x * ratio,
        pickData.pick1Point.y * ratio
      )
    }
    context.restore()
  }

  /**
   * Callback function when key is down
   *
   * @param {KeyboardEvent} e - event
   */
  public onKeyDown(e: KeyboardEvent): void {
    if (this.checkFreeze()) {
      return
    }

    if (e.key === Key.ESCAPE && isSegmentDeleteActive()) {
      // Escape cancels the pending delete at any phase — nothing committed.
      resetSegmentDelete()
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.ESCAPE && isCutMode()) {
      // Escape disarms the one-shot cut tool.
      setCutMode(false)
      this.setDefaultCursor()
      return
    }

    // Polyline-level undo/redo (Ctrl/Cmd+Z / Ctrl/Cmd+Y / Ctrl/Cmd+Shift+Z).
    // Only swallow the shortcut when it actually did something.
    if (drawHistory.handleKeyboard(e)) {
      e.preventDefault()
      return
    }

    const key = e.key
    this._keyDownMap[key] = true
    this._labelHandler.onKeyDown(e)
    this._labelList.onDrawableUpdate()
  }

  /**
   * Callback function when key is up
   *
   * @param {KeyboardEvent} e - event
   */
  public onKeyUp(e: KeyboardEvent): void {
    if (this.checkFreeze()) {
      return
    }

    const key = e.key
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete this._keyDownMap[key]
    if (key === Key.CONTROL || key === Key.META) {
      // Control or command
      this.setDefaultCursor()
    }
    this._labelHandler.onKeyUp(e)
    this._labelList.onDrawableUpdate()
  }

  /**
   * notify state is updated
   *
   * @param state
   */
  public updateState(state: State): void {
    if (this.display !== this.props.display) {
      this.display = this.props.display
      this.forceUpdate()
    }
    if (this._cutItemIndex !== state.user.select.item) {
      // Navigating to another image disarms the cut tools.
      if (this._cutItemIndex !== -1) {
        setCutMode(false)
        resetSegmentDelete()
      }
      this._cutItemIndex = state.user.select.item
    }
    this._labelHandler.updateState(state)
  }

  /**
   * Get the mouse position on the canvas in the image coordinates.
   *
   * @param {MouseEvent | WheelEvent} e: mouse event
   * @param e
   * @return {Vector2D}
   * mouse position (x,y) on the canvas
   */
  private getMousePos(e: React.MouseEvent<HTMLCanvasElement>): Vector2D {
    if (this.display !== null && this.labelCanvas !== null) {
      return normalizeMouseCoordinates(
        this.labelCanvas,
        this.canvasWidth,
        this.canvasHeight,
        this.displayToImageRatio,
        e.clientX,
        e.clientY
      )
    }
    return new Vector2D(0, 0)
  }

  /**
   * Get the label under the mouse.
   *
   * @param {Vector2D} mousePos: position of the mouse
   * @param mousePos
   * @return {number[]}
   */
  private fetchHandleId(mousePos: Vector2D): number[] {
    if (this.controlContext !== null) {
      const [x, y] = toCanvasCoords(
        mousePos,
        true,
        this.displayToImageRatio,
        this._upResRatio
      )
      const data = this.controlContext.getImageData(x, y, 4, 4).data
      return imageDataToHandleId(data)
    } else {
      return [-1, 0]
    }
  }

  /**
   * Update the canvas dimentions from the htmlcanvas element
   *
   * @param canva
   * @param canvas
   * @param isContorl
   */
  private updateCanvas(canvas: HTMLCanvasElement, isContorl: boolean): void {
    // The control canvas is read back every interaction via getImageData for
    // hit-testing; willReadFrequently avoids GPU readback stalls. The visible
    // label canvas is composited, so it keeps the default (GPU) context.
    const context = canvas.getContext(
      "2d",
      isContorl ? { willReadFrequently: true } : undefined
    )
    if (context === null) {
      return
    }
    if (canvas !== null && this.display !== null) {
      if (isContorl) {
        this.controlCanvas = canvas
        this.controlContext = context
      } else {
        this.labelCanvas = canvas
        this.labelContext = context
      }
      const displayRect = this.display.getBoundingClientRect()
      const item = this.state.user.select.item
      const sensor = this.state.user.viewerConfigs[this.props.id].sensor
      if (
        isFrameLoaded(this.state, item, sensor) &&
        displayRect.width !== 0 &&
        !isNaN(displayRect.width) &&
        displayRect.height !== 0 &&
        !isNaN(displayRect.height)
      ) {
        this.updateScale(canvas, context, true)
        // Draw synchronously in the same commit so the freshly-resized (and
        // therefore cleared) label/control canvases are never painted blank.
        // React re-renders this component on every pan frame (viewer config
        // changes), which clears the canvases; without an immediate redraw the
        // deferred RAF redraw leaves a blank frame and the labels visibly
        // flicker/disappear while panning. redraw() is a no-op until both the
        // label and control contexts are set, so calling it from each canvas's
        // ref is safe regardless of ref order. Mirrors ImageCanvas.
        this.redraw()
      }
    }
  }

  /**
   * Set the scale of the image in the display
   *
   * @param {object} canvas
   * @param context
   * @param {boolean} upRes
   */
  private updateScale(
    canvas: HTMLCanvasElement,
    context: CanvasRenderingContext2D,
    upRes: boolean
  ): void {
    if (this.display === null) {
      return
    }
    const imgConfig = getCurrentViewerConfig(
      this.state,
      this.props.id
    ) as ImageViewerConfigType
    if (imgConfig.viewScale >= MIN_SCALE && imgConfig.viewScale < MAX_SCALE) {
      ;[
        this.canvasWidth,
        this.canvasHeight,
        this.displayToImageRatio,
        this.scale,
        this._upResRatio
      ] = updateCanvasScale(
        this.state,
        this.display,
        canvas,
        context,
        imgConfig,
        imgConfig.viewScale / this.scale,
        upRes
      )
    }
  }
}

const styledCanvas = withStyles(label2dViewStyle, { withTheme: true })(
  Label2dCanvas
)
export default connect(mapStateToDrawableProps)(styledCanvas)
