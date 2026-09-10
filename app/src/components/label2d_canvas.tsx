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
import { isCurveCutMode, setCurveCutMode } from "../common/curve_cut_state"
import { isStraightenMode, setStraightenMode } from "../common/straighten_state"
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
import { getColorByCategory, toCssColor } from "../drawable/util"
import { advanceAnts, getAntsOffset } from "../drawable/2d/marching_ants"
import {
  ContentCutIcon,
  CURVE_CUT_CURSOR,
  CUT_CURSOR,
  GRAB_CURSOR,
  DeleteSegmentIcon,
  STRAIGHTEN_CURSOR
} from "./cut_icon"
import { performCurveCut } from "../drawable/2d/polyline_curve_cut"
import { performSimplify } from "../drawable/2d/polyline_simplify"
import {
  captureMarkAt,
  commitStamp,
  findGuideLine,
  previewMarks
} from "../drawable/2d/polyline_stamp"
import {
  addTemplate,
  endPreview,
  getAppliedIds,
  getPreviewGuide,
  getStampOptions,
  getTemplates,
  hasApplied,
  isCaptureMode,
  isPreviewing,
  isStampMode,
  onStampChange,
  redoPositions,
  setAppliedIds,
  setCaptureMode,
  setPositions,
  setStampHandlers,
  setStampMode,
  setStampOptions,
  startPreview,
  undoPositions
} from "../common/stamp_state"
import { performDisjoint } from "../drawable/2d/polyline_disjoint"
import { disjointableAnchors } from "../drawable/2d/polyline_disjoint_geometry"
import { isDisjointMode, setDisjointMode } from "../common/disjoint_state"
import { performArc } from "../drawable/2d/polyline_arc"
import { curveThroughPoints } from "../drawable/2d/polyline_arc_geometry"
import {
  addArcPick,
  clearArcPicks,
  getArcPicks,
  isArcMode,
  setArcMode
} from "../common/arc_state"
import { isSimplifyMode, setSimplifyMode } from "../common/simplify_state"
import {
  commitGrab,
  findGrabbableLabel
} from "../drawable/2d/polyline_grab_move"
import {
  endGrab,
  getGrab,
  GrabMode,
  isGrabbing,
  startGrab,
  updateGrab
} from "../common/grab_move_state"
import { performStraighten } from "../drawable/2d/polyline_straighten"
import { Key, LabelTypeName } from "../const/common"
import { Label2DHandler } from "../drawable/2d/label2d_handler"
import { Label2DList } from "../drawable/2d/label2d_list"
import {
  getCurrentViewerConfig,
  getShapes,
  isFrameLoaded
} from "../functional/state_util"
import { Vector2D } from "../math/vector2d"
import { label2dViewStyle } from "../styles/label"
import { ImageViewerConfigType, PathPoint2DType, State } from "../types/state"
import {
  clearCanvas,
  getCurrentImageSize,
  imageDataToHandleId,
  isScaleRenderable,
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
import {
  projectToPath,
  StampTemplate
} from "../drawable/2d/polyline_stamp_geometry"
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
  /**
   * Item index the cached canvas scale was computed for.
   *
   * updateScale runs from the canvas ref callback, which React invokes when
   * the element is created — NOT when the frame changes. Image sizes vary
   * enormously between frames (2500x3540 to 15000x3540 within one task), so a
   * stale displayToImageRatio scales the new frame's labels by the previous
   * frame's ratio and draws them far outside the image.
   */
  private _scaledForItem: number = -1
  /** context-menu anchor (viewport px), null while the menu is closed */
  private _menuAnchor: { left: number; top: number } | null = null
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDelete: (() => void) | null = null
  /** unsubscribe from marked-for-deletion set changes */
  private _offMarkedChange: (() => void) | null = null
  /** unsubscribe from freeform-select tool changes */
  private _offFreeformChange: (() => void) | null = null
  /** unsubscribe from stamp-tool changes */
  private _offStampChange: (() => void) | null = null
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
    // The settings panel lives in the viewer so it does not pan with the
    // image, but the commit logic lives here.
    setStampHandlers(
      () => {
        this.commitStampPreview()
      },
      () => {
        this.finishStampPreview()
      }
    )
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
    // The settings panel renders in the viewer, so its own re-render does not
    // repaint this canvas. Subscribe to the shared state instead, so tuning a
    // slider redraws the preview immediately.
    this._offStampChange = onStampChange(() => {
      this.redraw()
    })
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
    setStampHandlers(null, null)
    if (this._offStampChange !== null) {
      this._offStampChange()
      this._offStampChange = null
    }
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
    // Guarded: a canvas can mount a frame before its viewer config lands (the
    // zoom panel adds one at runtime), and an unguarded read here throws
    // during render, which blanks the whole app.
    const config = this.state.user.viewerConfigs[this.props.id] as
      | ImageViewerConfigType
      | undefined
    return config?.rotation ?? 0
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
    // The frame may have changed since the scale was last computed; the ref
    // callback does not fire on a frame change, so rescale here or the new
    // frame's labels are drawn at the previous frame's scale.
    this.rescaleIfItemChanged()

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
      this.drawGrabPreview(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.drawStampPreview(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.drawArcPreview(
        this.labelContext,
        this.displayToImageRatio * this._upResRatio
      )
      this.drawDisjointHints(
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
      if (
        e.shiftKey ||
        (isFreeformActive() && getSelectMode() === "freeform")
      ) {
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
            config.hiddenCategories !== undefined ? config.hiddenCategories : []
        }
      )
      switch (outcome) {
        case "curve":
          alert(Severity.WARNING, "Cannot cut a curved segment.")
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
            config.hiddenCategories !== undefined ? config.hiddenCategories : []
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
    // A carried line or an armed one-shot tool consumes the click entirely.
    if (this.handleArmedTool(mousePos)) {
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
    if (
      shouldDeferPointerDown(labelIndex, handleIndex, Date.now(), liveHandle)
    ) {
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
            config.hiddenCategories !== undefined ? config.hiddenCategories : []
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
    if (isGrabbing()) {
      // The carried line follows the cursor with no button held.
      updateGrab(mousePos.x, mousePos.y)
      this.redraw()
      return
    }
    if (isArcMode() && getArcPicks().length > 0) {
      // Rubber-band the curve: the cursor is a provisional next point, so the
      // shape has to repaint as the mouse moves.
      this.redraw()
      return
    }
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
    if (isArcMode() || isDisjointMode()) {
      this.setCursor(CUT_CURSOR)
      return
    }
    if (isStampMode() || isCaptureMode()) {
      // Both pick a line by clicking it, so they share the cut tools' aim.
      this.setCursor(CUT_CURSOR)
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
        context.lineTo(preview.doomed[i].x * ratio, preview.doomed[i].y * ratio)
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
   * Break the clicked line apart at the join point nearest the cursor.
   *
   * @param mousePos the click position, in image px
   */
  private handleDisjoint(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const result = performDisjoint(
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
    if (result === "disjointed") {
      setDisjointMode(false)
      this.setDefaultCursor()
      alert(Severity.INFO, "Disconnected into two lines.")
    } else if (result === "closed") {
      alert(Severity.WARNING, "Closed shapes cannot be disconnected.")
    } else if (result === "no-anchor") {
      // The ends of a line are already free, so only interior anchors count.
      alert(
        Severity.WARNING,
        "No join point there — click the vertex where two lines meet."
      )
    }
    this.redraw()
  }

  /**
   * Ring the anchors the disjoint tool can break, so the seams are visible.
   *
   * A merged run looks like one continuous line; without this the user would
   * have to guess where its pieces were joined.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawDisjointHints(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    if (!isDisjointMode()) {
      return
    }
    const itemIndex = this.state.user.select.item
    const item = this.state.task.items[itemIndex]
    if (item === undefined) {
      return
    }
    context.save()
    context.strokeStyle = "#4caf50"
    context.lineWidth = 2
    for (const labelId of Object.keys(item.labels)) {
      const label = item.labels[labelId]
      if (label.type !== LabelTypeName.POLYLINE_2D || label.closed === true) {
        continue
      }
      const stored = getShapes(
        this.state,
        itemIndex,
        labelId
      ) as PathPoint2DType[]
      const points = stored.map((p) => ({
        x: p.x,
        y: p.y,
        pointType: p.pointType
      }))
      for (const i of disjointableAnchors(points)) {
        context.beginPath()
        context.arc(points[i].x * ratio, points[i].y * ratio, 6, 0, 2 * Math.PI)
        context.stroke()
      }
    }
    context.restore()
  }

  /**
   * Record one click of the arc gesture, placing the arc on the third.
   *
   * @param mousePos the click position, in image px
   */
  private handleArcPick(mousePos: Vector2D): void {
    // Clicks simply accumulate; Enter (or a double-click) finishes the curve.
    // Auto-committing on the third click would make an arc through more than
    // three points impossible to draw.
    addArcPick({ x: mousePos.x, y: mousePos.y })
    this.redraw()
  }

  /**
   * Finish the arc gesture, placing a curve through every clicked point.
   */
  private commitArc(): void {
    const picks = getArcPicks()
    if (picks.length < 2) {
      alert(Severity.WARNING, "Click at least two points to draw a curve.")
      return
    }
    const result = performArc(picks, this.state.user.select.category)
    clearArcPicks()
    if (!result.ok) {
      // Every click landing on the same spot defines no curve. The tool stays
      // armed so the user simply clicks again.
      alert(Severity.WARNING, "Those points do not define a curve.")
    }
    this.redraw()
  }

  /**
   * Draw the arc gesture in progress: the clicks made, and the arc they imply.
   *
   * The third point follows the cursor, so the arc's sweep is visible before it
   * is committed — which is how a semicircle and an almost-full ring are told
   * apart while drawing.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawArcPreview(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    if (!isArcMode()) {
      return
    }
    const picks = getArcPicks()
    if (picks.length === 0) {
      return
    }
    context.save()
    context.strokeStyle = "#4caf50"
    context.fillStyle = "#4caf50"
    context.lineWidth = 2

    // The clicks placed so far.
    for (const p of picks) {
      context.beginPath()
      context.arc(p.x * ratio, p.y * ratio, 4, 0, 2 * Math.PI)
      context.fill()
    }

    // The cursor acts as a provisional next point, so the curve's shape — and
    // for three points its sweep — is visible before it is committed.
    const cursor = this._lastMousePos
    if (picks.length >= 1 && cursor !== null) {
      const points = curveThroughPoints([
        ...picks,
        { x: cursor.x, y: cursor.y }
      ])
      if (points !== null) {
        context.beginPath()
        context.moveTo(points[0].x * ratio, points[0].y * ratio)
        for (let i = 1; i + 2 < points.length; i += 3) {
          context.bezierCurveTo(
            points[i].x * ratio,
            points[i].y * ratio,
            points[i + 1].x * ratio,
            points[i + 1].y * ratio,
            points[i + 2].x * ratio,
            points[i + 2].y * ratio
          )
        }
        context.stroke()
      }
    }
    context.restore()
  }

  /**
   * Draw the marks that would be added by the pending stamp.
   *
   * Preview only: nothing is written until the user clicks to commit, so the
   * settings can be tuned and the marks redrawn as often as needed.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawStampPreview(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    const guideId = getPreviewGuide()
    if (guideId === null) {
      return
    }
    const marks = previewMarks(guideId, getStampOptions())
    if (marks.length === 0) {
      return
    }
    // After Apply the committed marks are drawn by the normal label path. The
    // preview still runs, so moving a slider shows where the marks WOULD go
    // before Update is pressed; it is drawn thinner so the two are
    // distinguishable rather than looking like doubled lines.
    const applied = hasApplied()
    const trace = (): void => {
      for (const mark of marks) {
        context.beginPath()
        context.moveTo(mark[0].x * ratio, mark[0].y * ratio)
        for (let i = 1; i < mark.length; i++) {
          context.lineTo(mark[i].x * ratio, mark[i].y * ratio)
        }
        context.stroke()
      }
    }
    context.save()
    // Black casing then a bright fill, matching the grab preview, so the marks
    // stay readable over imagery of any brightness.
    context.lineWidth = applied ? 3 : 4
    context.strokeStyle = "#000000"
    trace()
    context.lineWidth = applied ? 1.5 : 2
    context.strokeStyle = "#00e676"
    if (applied) {
      context.setLineDash(DASH_LINE)
    }
    trace()
    context.restore()
  }

  /**
   * Draw the line currently being carried by the cursor.
   *
   * A dashed white outline at the pending offset. Preview only: the stored
   * geometry is untouched until the drop commits.
   *
   * @param context the label canvas context
   * @param ratio image-to-canvas scale (displayToImageRatio * upResRatio)
   */
  private drawGrabPreview(
    context: CanvasRenderingContext2D,
    ratio: number
  ): void {
    const grab = getGrab()
    if (grab === null) {
      return
    }
    const state = this.state
    const label = state.task.items[grab.itemIndex]?.labels[grab.labelId]
    if (label === undefined) {
      return
    }
    const points = label.shapes
      .map((id) => state.task.items[grab.itemIndex].shapes[id])
      .filter((shape) => shape !== undefined)
    if (points.length < 2) {
      return
    }
    // Preview only — the real geometry is not touched until the drop commits.
    const p = points as Array<{ x: number; y: number }>
    const trace = (): void => {
      context.beginPath()
      context.moveTo(
        (p[0].x + grab.offsetX) * ratio,
        (p[0].y + grab.offsetY) * ratio
      )
      for (let i = 1; i < p.length; i++) {
        context.lineTo(
          (p[i].x + grab.offsetX) * ratio,
          (p[i].y + grab.offsetY) * ratio
        )
      }
    }

    const categories = state.task.config.categories
    const categoryIndex = label.category[0]
    const color = getColorByCategory(
      categoryIndex,
      categories !== undefined ? categories[categoryIndex] : undefined
    )

    context.save()
    // Black casing under the line, matching the vertex handles: the preview
    // has to stay readable over imagery of any brightness.
    context.lineWidth = 4
    context.strokeStyle = "#000000"
    trace()
    context.stroke()
    // The line's own category colour on top, so what is being carried is
    // identifiable at a glance rather than an anonymous white outline.
    context.lineWidth = 2
    context.strokeStyle = toCssColor(color)
    trace()
    context.stroke()
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

    if (e.key === Key.ENTER && isArcMode()) {
      // Enter finishes the curve. Clicks accumulate rather than committing at
      // a fixed count, so the gesture has to be closed explicitly.
      e.preventDefault()
      this.commitArc()
      return
    }

    if (e.key === Key.ESCAPE && this.handleEscape()) {
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
      if (this.canRunShortcut(e)) {
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
      (e.key === "w" || e.key === "W" || e.key === "e" || e.key === "E") &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey
    ) {
      // W picks up the hovered line so it follows the cursor; E does the same
      // with a copy, leaving the original in place. A click drops it, Escape
      // cancels. Press-move-click rather than press-and-drag: a drag would
      // collide with the pan/reshape handling on mouse-down, and this works on
      // a trackpad where holding a button while moving is awkward.
      if (this.canRunShortcut(e)) {
        e.preventDefault()
        const copy = e.key === "e" || e.key === "E"
        this.grabHoveredLine(copy ? GrabMode.COPY : GrabMode.MOVE)
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
      if (this.canRunShortcut(e)) {
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
      if (this.canRunShortcut(e)) {
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

    // While placing marks by hand, Ctrl+Z/Y act on the placements rather than
    // the annotation: they are not in redux yet, so DrawHistory cannot see
    // them, and a mis-click is exactly what undo is expected to fix.
    if (isPreviewing() && !getStampOptions().evenSpacing) {
      if (this.handlePlacementUndo(e)) {
        e.preventDefault()
        return
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
   * Cancel whatever one-shot tool or gesture is active.
   *
   * Returns true when something was cancelled, so the caller can stop
   * processing the key.
   */
  private handleEscape(): boolean {
    if (isDisjointMode()) {
      setDisjointMode(false)
      this.setDefaultCursor()
      this.redraw()
      return true
    }
    if (isArcMode()) {
      // First Esc abandons a half-drawn arc; a second disarms the tool, so a
      // mis-click does not force re-arming from the toolbar.
      if (getArcPicks().length > 0) {
        clearArcPicks()
      } else {
        setArcMode(false)
        this.setDefaultCursor()
      }
      this.redraw()
      return true
    }
    if (isPreviewing()) {
      // Discard the previewed marks; nothing was written.
      endPreview()
      this.setDefaultCursor()
      this.redraw()
      return true
    }
    if (isGrabbing()) {
      // Cancel the carry: nothing was committed, so the line simply snaps back.
      endGrab()
      this.setDefaultCursor()
      this.redraw()
      return true
    }

    if (isStraightenMode()) {
      // Escape disarms the one-shot straighten tool.
      setStraightenMode(false)
      this.setDefaultCursor()
      return true
    }

    if (isCurveCutMode()) {
      // Escape disarms the one-shot curve-aware cut tool.
      setCurveCutMode(false)
      this.setDefaultCursor()
      return true
    }

    if (isCutMode()) {
      // Escape disarms the one-shot cut tool.
      setCutMode(false)
      this.setDefaultCursor()
      return true
    }

    if (markedCount() > 0) {
      // Escape clears the batch-delete selection (redraw via the subscription).
      clearMarked()
      return true
    }

    if (isFreeformActive()) {
      // Escape disarms the freeform tool and drops any in-progress lasso.
      resetFreeform()
      this.setDefaultCursor()
      return true
    }

    return false
  }

  /**
   * Whether a bare-letter shortcut may run for this event.
   *
   * False while typing in a text field, where the letter is real input, and
   * while a line is being drawn or a delete-segment preview is pending, where
   * the geometry is not settled.
   *
   * @param e the keyboard event
   */
  private canRunShortcut(e: KeyboardEvent): boolean {
    const target = e.target as HTMLElement | null
    const typing =
      target !== null &&
      (target.tagName === "INPUT" || target.tagName === "TEXTAREA")
    const blocked =
      Session.label2dList.isDrawingInProgress() ||
      getSegmentDeletePhase() === "preview"
    return !typing && !blocked
  }

  /**
   * Run whichever carried line or armed one-shot tool owns this click.
   *
   * @param mousePos the click position in image coordinates
   * @returns true when the click was consumed
   */
  private handleArmedTool(mousePos: Vector2D): boolean {
    if (isGrabbing()) {
      // A carried line (W/E) drops on the next click.
      this.dropGrabbedLine()
      return true
    }
    if (isStraightenMode()) {
      this.handleStraighten(mousePos)
      return true
    }
    if (isSimplifyMode()) {
      this.handleSimplify(mousePos)
      return true
    }
    if (isPreviewing()) {
      const options = getStampOptions()
      if (!options.evenSpacing) {
        // Manual mode: each click drops a mark where it lands rather than
        // committing, so a run can be built up one paint stripe at a time.
        this.addManualMark(mousePos)
        return true
      }
      // Even mode: a click applies the marks at the current settings; the
      // panel stays open so they can still be adjusted.
      this.commitStampPreview()
      return true
    }
    if (isDisjointMode()) {
      this.handleDisjoint(mousePos)
      return true
    }
    if (isArcMode()) {
      this.handleArcPick(mousePos)
      return true
    }
    if (isCaptureMode()) {
      this.handleCapture(mousePos)
      return true
    }
    if (isStampMode()) {
      this.handleStamp(mousePos)
      return true
    }
    return false
  }

  /**
   * Capture the mark under the cursor as a reusable stamp template.
   *
   * @param mousePos the click position in image coordinates
   */
  private handleCapture(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const template = captureMarkAt(
      mousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
      `Mark ${getTemplates().length + 1}`,
      {
        hideLabels: config.hideLabels,
        hiddenLabelTypes:
          config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : [],
        hiddenCategories:
          config.hiddenCategories !== undefined ? config.hiddenCategories : []
      }
    )
    setCaptureMode(false)
    this.setDefaultCursor()
    if (template === null) {
      alert(Severity.WARNING, "Hover a mark, then click to save it as a shape.")
      return
    }
    addTemplate(template)
    // Select it straight away: capturing is almost always followed by using it.
    setStampOptions({
      ...getStampOptions(),
      template: StampTemplate.CUSTOM,
      custom: template
    })
    alert(Severity.INFO, `Saved "${template.name}" — pick it in the stamp bar.`)
    this.forceUpdate()
  }

  /**
   * Pick the line under the cursor as the stamp guide and start previewing.
   *
   * Nothing is written yet: the marks are drawn as a preview so the spacing and
   * angle can be tuned before committing.
   *
   * @param mousePos the click position in image coordinates
   */
  private handleStamp(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const guideId = findGuideLine(
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
    if (guideId === null) {
      alert(
        Severity.WARNING,
        "Hover a line, then click to preview marks along it."
      )
      return
    }
    startPreview(guideId)
    this.setDefaultCursor()
    this.redraw()
  }

  /**
   * Handle undo/redo for manually placed marks.
   *
   * @param e the keyboard event
   * @returns true when a placement was undone or redone
   */
  private handlePlacementUndo(e: KeyboardEvent): boolean {
    if (!e.ctrlKey && !e.metaKey) {
      return false
    }
    const key = e.key.toLowerCase()
    if (key === "z" && !e.shiftKey) {
      return undoPositions()
    }
    if (key === "y" || (key === "z" && e.shiftKey)) {
      return redoPositions()
    }
    return false
  }

  /**
   * Record a manually placed mark at the click.
   *
   * The click is stored as a distance along the guide, so the mark stays put if
   * the guide is later reshaped. Clicking an existing mark removes it, which
   * makes correcting a misplaced one a single click rather than an undo.
   *
   * @param mousePos the click position in image coordinates
   */
  private addManualMark(mousePos: Vector2D): void {
    const guideId = getPreviewGuide()
    if (guideId === null) {
      return
    }
    const state = this.state
    const itemIndex = state.user.select.item
    const guide = state.task.items[itemIndex]?.labels[guideId]
    if (guide === undefined) {
      return
    }
    const points = (
      guide.shapes
        .map((id) => state.task.items[itemIndex].shapes[id])
        .filter((shape) => shape !== undefined) as PathPoint2DType[]
    ).map((p) => ({ x: p.x, y: p.y, pointType: p.pointType }))
    const hit = projectToPath(points, mousePos)
    if (hit === null) {
      return
    }
    const options = getStampOptions()
    // A click within half a period of an existing mark toggles it off.
    const nearby = options.positions.findIndex(
      (d) => Math.abs(d - hit.distance) < Math.max(8, options.length / 2)
    )
    const positions =
      nearby >= 0
        ? options.positions.filter((_, i) => i !== nearby)
        : [...options.positions, hit.distance].sort((a, b) => a - b)
    setPositions(positions)
  }

  /**
   * Write the previewed marks to the annotation and end the preview.
   */
  private commitStampPreview(): void {
    const guideId = getPreviewGuide()
    if (guideId === null) {
      return
    }
    // Replace the previous run's marks, so applying again after changing a
    // setting adjusts the stamp instead of stacking a second set on top.
    const result = commitStamp(guideId, getStampOptions(), getAppliedIds())
    if (result.count > 0) {
      setAppliedIds(result.labelIds ?? [])
      alert(Severity.INFO, `${result.count} marks placed — adjust or Done.`)
    } else {
      alert(Severity.WARNING, "That line is too short to stamp marks along.")
    }
    this.redraw()
  }

  /**
   * Close the stamp preview, keeping whatever was last applied.
   */
  private finishStampPreview(): void {
    endPreview()
    this.setDefaultCursor()
    this.redraw()
  }

  /**
   * Simplify the line nearest the cursor.
   *
   * @param mousePos the click position in image coordinates
   */
  private handleSimplify(mousePos: Vector2D): void {
    const config = this.state.user.viewerConfigs[this.props.id]
    const result = performSimplify(
      mousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio,
      undefined,
      {
        hideLabels: config.hideLabels,
        hiddenLabelTypes:
          config.hiddenLabelTypes !== undefined ? config.hiddenLabelTypes : [],
        hiddenCategories:
          config.hiddenCategories !== undefined ? config.hiddenCategories : []
      }
    )
    if (result === "simplified") {
      setSimplifyMode(false)
      this.setDefaultCursor()
    } else if (result === "nothing-to-do") {
      alert(Severity.INFO, "That line has no redundant vertices.")
    }
  }

  /**
   * Pick up the hovered line so it follows the cursor.
   *
   * @param mode whether the drop moves the original or leaves a copy
   */
  private grabHoveredLine(mode: GrabMode): void {
    if (this._lastMousePos === null || isGrabbing()) {
      return
    }
    const labelId = findGrabbableLabel(
      this._lastMousePos,
      CUT_CLICK_RADIUS_PX / this.displayToImageRatio
    )
    if (labelId === null) {
      alert(
        Severity.WARNING,
        "Hover a line first, then press W to move it or E to copy it."
      )
      return
    }
    startGrab(
      labelId,
      this.state.user.select.item,
      mode,
      this._lastMousePos.x,
      this._lastMousePos.y
    )
    this.setCursor(GRAB_CURSOR)
  }

  /**
   * Drop the carried line at the cursor.
   */
  private dropGrabbedLine(): void {
    const grab = endGrab()
    this.setDefaultCursor()
    if (grab === null) {
      return
    }
    commitGrab(grab)
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
        setSimplifyMode(false)
        setStampMode(false)
        setCaptureMode(false)
        endPreview()
        endGrab()
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
    if (isScaleRenderable(this.props.id, imgConfig.viewScale)) {
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
      this._scaledForItem = this.state.user.select.item
    }
  }

  /**
   * Recompute the canvas scale when the displayed frame has changed.
   *
   * Frames within a single task differ hugely in size, and the cached
   * displayToImageRatio belongs to whichever frame was last scaled. Drawing a
   * new frame with it puts labels far outside the image, so the scale is
   * refreshed as soon as the item index moves.
   */
  private rescaleIfItemChanged(): void {
    const item = this.state.user.select.item
    if (item === this._scaledForItem) {
      return
    }
    if (this.display === null) {
      return
    }
    const sensor = this.state.user.viewerConfigs[this.props.id].sensor
    if (!isFrameLoaded(this.state, item, sensor)) {
      // The new frame's image has not arrived yet; its size is unknown, so
      // leave the old scale and rescale once it loads.
      return
    }
    const rect = this.display.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) {
      return
    }
    if (this.labelCanvas !== null && this.labelContext !== null) {
      this.updateScale(this.labelCanvas, this.labelContext, true)
    }
    if (this.controlCanvas !== null && this.controlContext !== null) {
      this.updateScale(this.controlCanvas, this.controlContext, true)
    }
  }
}

const styledCanvas = withStyles(label2dViewStyle, { withTheme: true })(
  Label2dCanvas
)
export default connect(mapStateToDrawableProps)(styledCanvas)
