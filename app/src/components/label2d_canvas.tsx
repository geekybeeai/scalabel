import Menu from "@material-ui/core/Menu"
import MenuItem from "@material-ui/core/MenuItem"
import { withStyles } from "@material-ui/core/styles"
import * as React from "react"
import { connect } from "react-redux"

import { changeSelect, changeViewerConfig } from "../action/common"
import { changeSelectedLabelsCategories } from "../action/select"
import { drawHistory } from "../common/draw_history"
import Session from "../common/session"
import { isInteracting, onIdle } from "../common/interaction_state"
import {
  armEmptyDrag,
  isArmed,
  didPan,
  reset as resetPanState,
  shouldDeferPointerDown
} from "../common/pointer_pan_state"
import { isCutMode, setCutMode } from "../common/cut_state"
import {
  isCurveCutMode,
  setCurveCutMode
} from "../common/curve_cut_state"
import {
  isStraightenMode,
  setStraightenMode
} from "../common/straighten_state"
import { armKey, recordKeyDown, recordKeyUp } from "../common/keyboard_state"
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
  clearMarked,
  markedCount,
  onMarkedChange,
  toggleMarked
} from "../common/multi_delete_state"
import {
  addFreeformPoint,
  beginFreeformPath,
  completeRect,
  endFreeformPath,
  getSelectionOverlay,
  getSelectMode,
  isFreeformActive,
  isFreeformDrawing,
  isRectSizing,
  onFreeformChange,
  resetFreeform,
  setRectFirstCorner,
  updateRectCursor
} from "../common/freeform_select_state"
import { runFreeformSelect } from "../drawable/2d/freeform_select"
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
import { DASH_LINE, DELETE_HIGHLIGHT_COLOR } from "../drawable/2d/common"
import { advanceAnts, getAntsOffset } from "../drawable/2d/marching_ants"
import {
  ContentCutIcon,
  CURVE_CUT_CURSOR,
  CUT_CURSOR,
  DeleteSegmentIcon,
  STRAIGHTEN_CURSOR
} from "./cut_icon"
import { performCurveCut } from "../drawable/2d/polyline_curve_cut"
import { performStraighten } from "../drawable/2d/polyline_straighten"
import { Key, LabelTypeName } from "../const/common"
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
  rotatePoint,
  toCanvasCoords,
  unrotatePoint,
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
  /**
   * Last cursor position in image coordinates, or null before the pointer has
   * entered the canvas. Lets keyboard shortcuts act on the line under the
   * cursor rather than on the redux selection, which can hold more labels than
   * the one the user means (linked labels, appended multi-select).
   */
  private _lastMousePos: Vector2D | null = null
  /** context-menu anchor (viewport px), null while the menu is closed */
  private _menuAnchor: { left: number; top: number } | null = null
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDelete: (() => void) | null = null
  /** unsubscribe from marked-for-deletion set changes */
  private _offMarkedChange: (() => void) | null = null
  /** unsubscribe from freeform-select tool changes */
  private _offFreeformChange: (() => void) | null = null
  /** pending commit timer for the delete-segment preview */
  private _segmentDeleteTimer: number | null = null
  /** rAF handle for the marching-ants animation */
  private _antsRAF: number | null = null

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
    this._offMarkedChange = onMarkedChange(() => {
      // Repaint immediately, and start/stop the marching-ants animation
      // depending on whether anything is now marked.
      this.redraw()
      this.syncMarchingAnts()
    })
    this._offFreeformChange = onFreeformChange(() => {
      this.redraw()
    })
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
    if (this._offMarkedChange !== null) {
      this._offMarkedChange()
      this._offMarkedChange = null
    }
    if (this._offFreeformChange !== null) {
      this._offFreeformChange()
      this._offFreeformChange = null
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

  /** Current display-only view rotation (0/90/180/270) for this viewer. */
  private get viewRotation(): number {
    const config = this.state.user.viewerConfigs[
      this.props.id
    ] as ImageViewerConfigType
    return config.rotation ?? 0
  }

  /**
   * Apply the view rotation to a drawing context so content paints turned.
   * The canvas is sized to the rotated dimensions, so for 90°/270° we
   * translate by the swapped axis before rotating. Caller must
   * context.restore() afterwards.
   *
   * @param ctx the 2d context to transform
   * @param canvas the canvas owning the context (for its backing dimensions)
   * @param rotation 0 | 90 | 180 | 270
   */
  private applyRotation(
    ctx: CanvasRenderingContext2D,
    canvas: HTMLCanvasElement,
    rotation: number
  ): void {
    if (rotation === 90) {
      ctx.translate(canvas.width, 0)
      ctx.rotate(Math.PI / 2)
    } else if (rotation === 180) {
      ctx.translate(canvas.width, canvas.height)
      ctx.rotate(Math.PI)
    } else if (rotation === 270) {
      ctx.translate(0, canvas.height)
      ctx.rotate(-Math.PI / 2)
    }
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
            this.state.task.config.tracking ||
            (
              this.state.user.viewerConfigs[this.props.id] as unknown as {
                showCurvesOnly?: boolean
              }
            ).showCurvesOnly === true
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
            this.state.task.config.tracking ||
            (
              this.state.user.viewerConfigs[this.props.id] as unknown as {
                showCurvesOnly?: boolean
              }
            ).showCurvesOnly === true
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
      const showCurvesOnly: boolean =
        "showCurvesOnly" in config &&
        (config as unknown as { showCurvesOnly?: boolean }).showCurvesOnly ===
          true

      // Compute viewport bounds in image coordinates for culling
      let viewportBounds: [number, number, number, number] | undefined
      if (
        viewScale > 2 &&
        this.viewRotation === 0 &&
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

      // Rotate the label AND control contexts identically so labels, the
      // delete/lasso overlays, and color-coded hit-testing all turn with the
      // view. Their image-frame coordinates need no changes.
      const rotation = this.viewRotation
      this.labelContext.save()
      this.controlContext.save()
      this.applyRotation(this.labelContext, this.labelCanvas, rotation)
      this.applyRotation(this.controlContext, this.controlCanvas, rotation)
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
        lineWidthMultiplier,
        showCurvesOnly
      )
      this.drawSegmentDeleteOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.drawFreeformOverlay(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.labelContext.restore()
      this.controlContext.restore()
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
    resetPanState()
    // Freeform (lasso) select: armed via the toolbar, or ad-hoc via Shift+drag.
    // Begins the lasso here so the empty-space pan-arming below never runs.
    // Ctrl/Meta are excluded so Ctrl+click-mark and Ctrl-pan keep working.
    const ffConfig = this.state.user.viewerConfigs[
      this.props.id
    ] as ImageViewerConfigType
    const freeformAllowed =
      !this._labelList.isDrawingInProgress() &&
      !this.state.task.config.tracking &&
      ffConfig?.showCurvesOnly !== true
    if (!e.ctrlKey && !e.metaKey && freeformAllowed) {
      // Shift+drag is always a freeform lasso; the armed toolbar mode follows
      // the selected sub-mode.
      if (e.shiftKey || (isFreeformActive() && getSelectMode() === "freeform")) {
        beginFreeformPath(mousePos)
        this.setCursor("crosshair")
        return
      }
      if (isFreeformActive() && getSelectMode() === "rectangle") {
        // Consume the mousedown so the empty-space pan-arming never runs; the
        // two-click corner logic runs in onMouseUp.
        this.setCursor("crosshair")
        return
      }
    }
    // Control + click for dragging
    // get mouse position in image coordinates
    // Ctrl/Cmd drag pans anywhere via Viewer2D; never draw/edit on it.
    if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd+click directly on a polyline/polygon toggles it into the
      // batch-delete set; anywhere else (empty canvas, a box) falls through to
      // the pan behavior. Disabled for tracking tasks.
      if (labelIndex >= 0 && !this.state.task.config.tracking) {
        const drawable = this._labelList.labelList[labelIndex]
        if (
          drawable !== undefined &&
          (drawable.type === LabelTypeName.POLYLINE_2D ||
            drawable.type === LabelTypeName.POLYGON_2D)
        ) {
          toggleMarked(drawable.labelId)
          return
        }
      }
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
            "Cannot cut a curved segment."
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
    // Divide curve: splits the clicked bezier into two adjustable groups.
    // The polyline stays ONE label — nothing is cut into separate lines.
    if (isCurveCutMode()) {
      this.handleCurveCut(mousePos)
      return
    }
    // Straighten: drop the clicked curve's control points.
    if (isStraightenMode()) {
      this.handleStraighten(mousePos)
      return
    }
    // Empty canvas, OR within the post-double-click pan window: defer the
    // action. A drag pans (Viewer2D, via the armed flag); a click replays the
    // draw/select in onMouseUp. Arming (rather than returning early) inside the
    // pan window ensures a click there is not silently dropped. A hit on a
    // label POINT is never deferred — and neither is a body hit while the
    // hovered label still has a point handle highlighted: trackpad users
    // double-tap to start the C+drag curve gesture, and the pan window used
    // to swallow that drag (the C+tap conversion also moves the control
    // points to the 1/3 / 2/3 marks, out from under the cursor).
    const hoveredLabel = this._labelHandler.highlightedLabel
    const liveHandle =
      hoveredLabel !== null && hoveredLabel.index === labelIndex
        ? hoveredLabel.highlightedHandle
        : -1
    if (shouldDeferPointerDown(labelIndex, handleIndex, Date.now(), liveHandle)) {
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

    if (isFreeformDrawing()) {
      const path = endFreeformPath()
      if (path.length >= 3) {
        const config = this.state.user.viewerConfigs[this.props.id]
        runFreeformSelect(path, {
          hideLabels: config.hideLabels,
          hiddenLabelTypes:
            config.hiddenLabelTypes !== undefined
              ? config.hiddenLabelTypes
              : [],
          hiddenCategories:
            config.hiddenCategories !== undefined
              ? config.hiddenCategories
              : []
        })
      }
      this.setDefaultCursor()
      this._labelList.onDrawableUpdate()
      return
    }

    if (isFreeformActive() && getSelectMode() === "rectangle") {
      const rectPos = this.getMousePos(e)
      if (!isRectSizing()) {
        setRectFirstCorner(rectPos)
      } else {
        const corners = completeRect(rectPos)
        if (corners !== null) {
          const config = this.state.user.viewerConfigs[this.props.id]
          runFreeformSelect(corners, {
            hideLabels: config.hideLabels,
            hiddenLabelTypes:
              config.hiddenLabelTypes !== undefined
                ? config.hiddenLabelTypes
                : [],
            hiddenCategories:
              config.hiddenCategories !== undefined
                ? config.hiddenCategories
                : []
          })
        }
      }
      this.setCursor("crosshair")
      this._labelList.onDrawableUpdate()
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

    if (isFreeformDrawing()) {
      addFreeformPoint(this.getMousePos(e))
      this.setCursor("crosshair")
      return
    }

    if (
      isFreeformActive() &&
      getSelectMode() === "rectangle" &&
      isRectSizing()
    ) {
      updateRectCursor(this.getMousePos(e))
      this.setCursor("crosshair")
      return
    }

    if (isArmed()) {
      // While a deferred empty-space gesture is in progress, do not draw/edit.
      // Viewer2D decides pan-vs-nothing from the movement threshold.
      return
    }

    // Update the currently hovered shape
    const mousePos = this.getMousePos(e)
    this._lastMousePos = mousePos
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

    if (isCurveCutMode()) {
      this.setCursor(CURVE_CUT_CURSOR)
      return
    }
    if (isStraightenMode()) {
      this.setCursor(STRAIGHTEN_CURSOR)
      return
    }
    if (isCutMode() || isSegmentDeleteActive()) {
      // The scissors cursor overrides hover cursors while a tool is armed.
      this.setCursor(CUT_CURSOR)
    }
    if (isFreeformActive()) {
      this.setCursor("crosshair")
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
    } else {
      // Not previewing: cancel the pending commit timer. The marching-ants
      // animation keeps running if lines are still marked for deletion.
      if (this._segmentDeleteTimer !== null) {
        window.clearTimeout(this._segmentDeleteTimer)
        this._segmentDeleteTimer = null
      }
      // Repaint to add/remove the pick halo or erase the overlay.
      this.redraw()
    }
    this.syncMarchingAnts()
  }

  /**
   * Run the marching-ants animation while any delete affordance is on screen —
   * the delete-segment preview or one or more lines marked for batch deletion —
   * and stop it once neither is active. A single shared rAF advances the shared
   * dash offset and repaints, so the overlay and the marked lines animate in
   * lockstep.
   */
  private syncMarchingAnts(): void {
    const active = getSegmentDeletePhase() === "preview" || markedCount() > 0
    if (active) {
      if (this._antsRAF === null) {
        const step = (): void => {
          advanceAnts(0.75)
          this.redraw()
          this._antsRAF =
            getSegmentDeletePhase() === "preview" || markedCount() > 0
              ? window.requestAnimationFrame(step)
              : null
        }
        this._antsRAF = window.requestAnimationFrame(step)
      }
    } else if (this._antsRAF !== null) {
      window.cancelAnimationFrame(this._antsRAF)
      this._antsRAF = null
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
   * Draw one pick halo in the shared delete color.
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
    context.strokeStyle = "rgba(255, 0, 200, 0.8)"
    context.fillStyle = "rgba(255, 0, 200, 0.2)"
    context.lineWidth = 2
    context.arc(x, y, 12, 0, 2 * Math.PI)
    context.fill()
    context.stroke()
    context.beginPath()
    context.fillStyle = "rgba(255, 0, 200, 0.9)"
    context.arc(x, y, 5, 0, 2 * Math.PI)
    context.fill()
  }

  /**
   * Draw the delete-segment overlay: a halo on the first pick while waiting for
   * the second and, during the preview, the doomed piece as a dashed
   * marching-ants path with halos on BOTH picked points (the
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
      context.strokeStyle = DELETE_HIGHLIGHT_COLOR
      context.lineWidth = 4
      context.setLineDash(DASH_LINE)
      context.lineDashOffset = -getAntsOffset()
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
   * Draw the in-progress freeform lasso: the accumulated path as a dashed
   * magenta polyline, closed back to its start. Drawn on top of the labels
   * like the delete-segment overlay.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawFreeformOverlay(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    const path = getSelectionOverlay()
    if (path.length < 2) {
      return
    }
    context.save()
    context.beginPath()
    context.strokeStyle = DELETE_HIGHLIGHT_COLOR
    context.lineWidth = 2
    context.setLineDash(DASH_LINE)
    context.lineDashOffset = -getAntsOffset()
    context.moveTo(path[0].x * ratio, path[0].y * ratio)
    for (let i = 1; i < path.length; i++) {
      context.lineTo(path[i].x * ratio, path[i].y * ratio)
    }
    context.closePath()
    context.stroke()
    context.restore()
  }

  /**
   * Callback function when key is down
   *
   * @param {KeyboardEvent} e - event
   */
  public onKeyDown(e: KeyboardEvent): void {
    // Mirror the physical key state into the module-level record FIRST (before
    // any early return): drawables read held keys from there at mouse-down
    // time, since their per-instance key maps are wiped by select-on-click
    // rebuilds. Plain presses also open the press-then-click arm window —
    // laptop palm rejection blocks trackpad taps while a key is held, so the
    // C/D click gestures must work sequentially too. See
    // common/keyboard_state.ts.
    recordKeyDown(e.key)
    if (!e.ctrlKey && !e.metaKey) {
      armKey(e.key, Date.now())
    }
    if (this.checkFreeze()) {
      return
    }

    if (e.key === Key.ESCAPE && isSegmentDeleteActive()) {
      // Escape cancels the pending delete at any phase — nothing committed.
      resetSegmentDelete()
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.ESCAPE && isStraightenMode()) {
      // Escape disarms the one-shot straighten tool.
      setStraightenMode(false)
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.ESCAPE && isCurveCutMode()) {
      // Escape disarms the one-shot curve-aware cut tool.
      setCurveCutMode(false)
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.ESCAPE && isCutMode()) {
      // Escape disarms the one-shot cut tool.
      setCutMode(false)
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.ESCAPE && markedCount() > 0) {
      // Escape clears the batch-delete selection (redraw via the subscription).
      clearMarked()
      return
    }

    if (e.key === Key.ESCAPE && isFreeformActive()) {
      // Escape disarms the freeform tool and drops any in-progress lasso.
      resetFreeform()
      this.setDefaultCursor()
      return
    }

    if (e.key === Key.SPACE) {
      // Space toggles the image layer (same as the sidebar "Show image"
      // checkbox). Skip when typing in a text field, where Space is input.
      const target = e.target as HTMLElement | null
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      if (!typing) {
        e.preventDefault()
        const config = this.state.user.viewerConfigs[this.props.id]
        Session.dispatch(
          changeViewerConfig(this.props.id, {
            ...config,
            hideImage: !(config.hideImage ?? false)
          })
        )
        return
      }
    }

    if (
      (e.key === "r" || e.key === "R") &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
      // R rotates the view 90° clockwise; Shift+R counter-clockwise.
      // Display-only (stored coords stay in the original frame). Skipped
      // while typing, while drawing, and during a delete-segment preview.
      const target = e.target as HTMLElement | null
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      const blocked =
        Session.label2dList.isDrawingInProgress() ||
        getSegmentDeletePhase() === "preview"
      if (!typing && !blocked) {
        e.preventDefault()
        const config = this.state.user.viewerConfigs[
          this.props.id
        ] as ImageViewerConfigType
        const current = config.rotation ?? 0
        const delta = e.shiftKey ? -90 : 90
        const rotation = (((current + delta) % 360) + 360) % 360
        const newConfig: ImageViewerConfigType = { ...config, rotation }
        Session.dispatch(changeViewerConfig(this.props.id, newConfig))
        return
      }
    }

    if (
      (e.key === "a" || e.key === "A") &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
      // A straightens the single curve nearest the CURSOR — the keyboard
      // equivalent of the straighten toolbar button. Hover-targeted, not
      // selection-targeted (selection can hold more labels than the user
      // means), and per-curve, not whole-line (a merged line holds several
      // curve groups in one label). NOT bound to S: the title bar already claims bare S for Save on
      // document keydown and calls preventDefault, so an S binding here never
      // ran. Skipped while typing (A is input there) and while drawing, where
      // the line is not committed yet.
      const target = e.target as HTMLElement | null
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      const blocked =
        Session.label2dList.isDrawingInProgress() ||
        getSegmentDeletePhase() === "preview"
      if (!typing && !blocked) {
        e.preventDefault()
        this.straightenSelection()
        return
      }
    }

    if (/^[0-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      // A number key sets the category: it applies to every selected label and
      // becomes the default for the next line drawn. 1 is the first category
      // in the sidebar, matching what the user reads there rather than the
      // zero-based index underneath. 0 is accepted as the tenth slot.
      // Skipped while typing (digits are input there) and while drawing, where
      // switching category mid-line would apply to the wrong thing.
      const target = e.target as HTMLElement | null
      const typing =
        target !== null &&
        (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
      const blocked =
        Session.label2dList.isDrawingInProgress() ||
        getSegmentDeletePhase() === "preview"
      if (!typing && !blocked) {
        const digit = Number(e.key)
        const categoryIndex = digit === 0 ? 9 : digit - 1
        const categories = this.state.task.config.categories
        if (categoryIndex < categories.length) {
          e.preventDefault()
          Session.dispatch(changeSelect({ category: categoryIndex }))
          // Only retarget existing labels when something is actually
          // selected: changeSelectedLabelsCategories dereferences
          // labelIds[0][0] and would throw on an empty selection.
          const state = Session.getState()
          const hasSelected = Object.values(state.user.select.labels).some(
            (ids) => ids.length > 0
          )
          if (hasSelected) {
            Session.dispatch(
              changeSelectedLabelsCategories(state, [categoryIndex])
            )
          }
          return
        }
      }
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
   * Divide the curve at a click and report the outcome.
   *
   * A successful divide disarms the one-shot tool; a rejection keeps it armed
   * so the user can re-aim. The polyline is never broken in two — only the
   * bezier under the click is split into two adjustable groups.
   *
   * @param mousePos the click position in image coordinates
   */
  private handleCurveCut(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const result = performCurveCut(
      mousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
      CUT_SNAP_RADIUS_PX / this.displayToImageRatio,
      {
        hideLabels: config.hideLabels,
        hiddenLabelTypes:
          config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : [],
        hiddenCategories:
          config.hiddenCategories !== undefined ? config.hiddenCategories : []
      }
    )
    switch (result) {
      case "cut":
        setCurveCutMode(false)
        this.setDefaultCursor()
        break
      // No "closed" case: dividing only inserts an anchor, so closed rings are
      // valid targets and never rejected.
      case "near-endpoint":
        alert(Severity.WARNING, "Too close to an endpoint to divide.")
        break
      default:
        break
    }
  }

  /**
   * Straighten the single curve nearest the cursor.
   *
   * The keyboard equivalent of the straighten toolbar button, sharing its
   * executor so both behave identically. Deliberately per-curve, not
   * whole-line: merged lines hold several curve groups in ONE label, so a
   * whole-line straighten there wipes out every curve at once — which reads as
   * "it straightened both lines". Press A again to straighten the next curve.
   */
  private straightenSelection(): void {
    if (this._lastMousePos === null) {
      return
    }
    const config = this.state.user.viewerConfigs[this.props.id]
    const result = performStraighten(
      this._lastMousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
      {
        hideLabels: config.hideLabels,
        hiddenLabelTypes:
          config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : [],
        hiddenCategories:
          config.hiddenCategories !== undefined ? config.hiddenCategories : []
      }
    )
    if (result !== "straightened") {
      alert(
        Severity.WARNING,
        "Hover a curved segment, then press A to straighten it."
      )
    }
  }

  /**
   * Straighten the curve nearest a click and report the outcome.
   *
   * A successful straighten disarms the one-shot tool; a miss keeps it armed
   * so the user can re-aim.
   *
   * @param mousePos the click position in image coordinates
   */
  private handleStraighten(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const result = performStraighten(
      mousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
      {
        hideLabels: config.hideLabels,
        hiddenLabelTypes:
          config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : [],
        hiddenCategories:
          config.hiddenCategories !== undefined ? config.hiddenCategories : []
      }
    )
    if (result === "straightened") {
      setStraightenMode(false)
      this.setDefaultCursor()
    } else {
      alert(Severity.WARNING, "Click a curved segment to straighten it.")
    }
  }

  /**
   * Callback function when key is up
   *
   * @param {KeyboardEvent} e - event
   */
  public onKeyUp(e: KeyboardEvent): void {
    // Keep the module-level key record faithful to the physical keyboard even
    // when frozen (the matching keydown was recorded before the freeze check).
    recordKeyUp(e.key)
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
        setCurveCutMode(false)
        setStraightenMode(false)
        resetSegmentDelete()
        clearMarked()
        resetFreeform()
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
      const displayCoord = normalizeMouseCoordinates(
        this.labelCanvas,
        this.canvasWidth,
        this.canvasHeight,
        this.displayToImageRatio,
        e.clientX,
        e.clientY
      )
      // With a rotated view, normalizeMouseCoordinates returns the point in
      // the rotated/display frame; map it back to the original image frame so
      // every stored coordinate stays in the original (unrotated) frame.
      const rotation = this.viewRotation
      if (rotation === 0) {
        return displayCoord
      }
      const size = getCurrentImageSize(this.state, this.props.id)
      return unrotatePoint(displayCoord, rotation, size.width, size.height)
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
      // The control canvas is drawn through the rotation, but getImageData
      // reads raw backing pixels (ignoring the context transform), so probe
      // at the rotated position of the (original-frame) mouse coordinate.
      const rotation = this.viewRotation
      let probe = mousePos
      if (rotation !== 0) {
        const size = getCurrentImageSize(this.state, this.props.id)
        probe = rotatePoint(mousePos, rotation, size.width, size.height)
      }
      const [x, y] = toCanvasCoords(
        probe,
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
