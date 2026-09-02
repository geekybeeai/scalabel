import { IconButton, Menu, MenuItem } from "@material-ui/core"
import Tooltip from "@mui/material/Tooltip"
import Fade from "@mui/material/Fade"
import AddIcon from "@material-ui/icons/Add"
import FindReplaceIcon from "@material-ui/icons/FindReplace"
import LineWeightIcon from "@material-ui/icons/LineWeight"
import RedoIcon from "@material-ui/icons/Redo"
import RemoveIcon from "@material-ui/icons/Remove"
import RotateLeftIcon from "@material-ui/icons/RotateLeft"
import RotateRightIcon from "@material-ui/icons/RotateRight"
import UndoIcon from "@material-ui/icons/Undo"
import ZoomInIcon from "@material-ui/icons/ZoomIn"
import ZoomOutIcon from "@material-ui/icons/ZoomOut"
import ArrowDropDownIcon from "@material-ui/icons/ArrowDropDown"
import { withStyles } from "@material-ui/styles"
import React from "react"

import { changeViewerConfig } from "../action/common"
import { drawHistory } from "../common/draw_history"
import Session from "../common/session"
import { isCutMode, onCutModeChange, setCutMode } from "../common/cut_state"
import {
  isCurveCutMode,
  onCurveCutModeChange,
  setCurveCutMode
} from "../common/curve_cut_state"
import {
  isStraightenMode,
  onStraightenModeChange,
  setStraightenMode
} from "../common/straighten_state"
import {
  isSnapEnabled,
  onSnapChange,
  setSnapEnabled
} from "../common/snap_state"
import {
  armSegmentDelete,
  getSegmentDeletePhase,
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  resetSegmentDelete
} from "../common/segment_delete_state"
import {
  armFreeform,
  getSelectMode,
  isFreeformArmed,
  onFreeformChange,
  resetFreeform,
  setSelectMode
} from "../common/freeform_select_state"
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
import {
  ContentCutCurveIcon,
  ContentCutIcon,
  SnapOffIcon,
  SnapOnIcon,
  StraightenIcon,
  DeleteSegmentIcon,
  FreeformSelectIcon,
  RectangleSelectIcon,
  RefreshCcwIcon
} from "./cut_icon"
import ImageCanvas from "./image_canvas"
import Label2dCanvas from "./label2d_canvas"

/**
 * Display pixels panned per unit of horizontal wheel delta.
 *
 * 1 maps a wheel notch straight onto the pan distance the browser would have
 * scrolled, which keeps a tilt wheel and a trackpad swipe feeling the same.
 */
const WHEEL_PAN_RATIO = 1

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
  /** unsubscribe from cut-mode change notifications */
  private _offCutModeChange: (() => void) | null = null
  /** unsubscribe from curve-cut-mode change notifications */
  private _offCurveCutModeChange: (() => void) | null = null
  /** unsubscribe from straighten-mode change notifications */
  private _offStraightenModeChange: (() => void) | null = null
  /** unsubscribe from endpoint-snap toggle notifications */
  private _offSnapChange: (() => void) | null = null
  /** unsubscribe from delete-segment state changes */
  private _offSegmentDeleteChange: (() => void) | null = null
  /** anchor element for the select-mode dropdown menu (null = closed) */
  private _selectMenuAnchor: HTMLElement | null = null
  /** unsubscribe from select-tool (freeform/rectangle) state changes */
  private _offFreeformChange: (() => void) | null = null

  /**
   * Mount: re-render the toolbar tints when the cut tools change state
   * elsewhere (Escape in the canvas, a successful cut/delete, context-menu
   * arming).
   */
  public componentDidMount(): void {
    super.componentDidMount()
    this._offCutModeChange = onCutModeChange(() => this.forceUpdate())
    this._offCurveCutModeChange = onCurveCutModeChange(() => this.forceUpdate())
    this._offStraightenModeChange = onStraightenModeChange(() =>
      this.forceUpdate()
    )
    this._offSnapChange = onSnapChange(() => this.forceUpdate())
    this._offSegmentDeleteChange = onSegmentDeleteChange(() =>
      this.forceUpdate()
    )
    this._offFreeformChange = onFreeformChange(() => this.forceUpdate())
  }

  /**
   * Unmount: stop listening for cut-tool state changes.
   */
  public componentWillUnmount(): void {
    super.componentWillUnmount()
    if (this._offCutModeChange !== null) {
      this._offCutModeChange()
      this._offCutModeChange = null
    }
    if (this._offCurveCutModeChange !== null) {
      this._offCurveCutModeChange()
      this._offCurveCutModeChange = null
    }
    if (this._offStraightenModeChange !== null) {
      this._offStraightenModeChange()
      this._offStraightenModeChange = null
    }
    if (this._offSnapChange !== null) {
      this._offSnapChange()
      this._offSnapChange = null
    }
    if (this._offSegmentDeleteChange !== null) {
      this._offSegmentDeleteChange()
      this._offSegmentDeleteChange = null
    }
    if (this._offFreeformChange !== null) {
      this._offFreeformChange()
      this._offFreeformChange = null
    }
  }

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
        widthResetButton,
        ...this.getRotationButtons(),
        ...this.getHistoryButtons(),
        this.getCutButton(),
        this.getCurveCutButton(),
        this.getStraightenButton(),
        this.getSnapButton(),
        this.getDeleteSegmentButton(),
        this.getFreeformSelectButton()
      ]
    }
    return []
  }

  /**
   * Build the polyline undo/redo toolbar buttons
   *
   * @return {JSX.Element[]} undo and redo buttons
   */
  protected getHistoryButtons(): JSX.Element[] {
    // When there is nothing to undo/redo the button is dimmed (still visible)
    // rather than hidden. Clicking a dimmed button is a no-op (undo/redo return
    // early), so no `disabled` attribute is needed and the icon keeps its color.
    const DIMMED = 0.4
    const undoButton = (
      <Tooltip
        key={`undo2dButton${this.props.id}`}
        title="Undo last polyline"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => drawHistory.undo()}
          className={this.props.classes.viewer_button}
          style={{ opacity: drawHistory.canUndo() ? 1 : DIMMED }}
          edge={"start"}
        >
          <UndoIcon />
        </IconButton>
      </Tooltip>
    )
    const redoButton = (
      <Tooltip
        key={`redo2dButton${this.props.id}`}
        title="Redo polyline"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => drawHistory.redo()}
          className={this.props.classes.viewer_button}
          style={{ opacity: drawHistory.canRedo() ? 1 : DIMMED }}
          edge={"start"}
        >
          <RedoIcon />
        </IconButton>
      </Tooltip>
    )
    return [undoButton, redoButton]
  }

  /**
   * Rotate the image view by ±90° (display only; never affects the JSON).
   * Inert while a line is being drawn or a delete-segment preview is pending,
   * so the view cannot change frames mid-gesture.
   *
   * @param delta +90 (clockwise / right) or -90 (counter-clockwise / left)
   */
  private rotateView(delta: number): void {
    if (
      Session.label2dList.isDrawingInProgress() ||
      getSegmentDeletePhase() === "preview"
    ) {
      return
    }
    const config = this._viewerConfig as ImageViewerConfigType
    const current = config.rotation ?? 0
    const rotation = (((current + delta) % 360) + 360) % 360
    const newConfig: ImageViewerConfigType = { ...config, rotation }
    Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
  }

  /**
   * Reset the view rotation to the original (0°) orientation. Same
   * mid-gesture guards as rotateView; a no-op when already at 0°.
   */
  private resetRotation(): void {
    if (
      Session.label2dList.isDrawingInProgress() ||
      getSegmentDeletePhase() === "preview"
    ) {
      return
    }
    const config = this._viewerConfig as ImageViewerConfigType
    if ((config.rotation ?? 0) === 0) {
      return
    }
    const newConfig: ImageViewerConfigType = { ...config, rotation: 0 }
    Session.dispatch(changeViewerConfig(this._viewerId, newConfig))
  }

  /**
   * Build the rotate-left / rotate-right / reset-rotation toolbar buttons.
   *
   * @return {JSX.Element[]} rotate-left, rotate-right and reset buttons
   */
  protected getRotationButtons(): JSX.Element[] {
    const rotateLeftButton = (
      <Tooltip
        key={`rotateLeft2dButton${this.props.id}`}
        title="Rotate left 90°"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => this.rotateView(-90)}
          className={this.props.classes.viewer_button}
          edge={"start"}
        >
          <RotateLeftIcon />
        </IconButton>
      </Tooltip>
    )
    const rotateRightButton = (
      <Tooltip
        key={`rotateRight2dButton${this.props.id}`}
        title="Rotate right 90°"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => this.rotateView(90)}
          className={this.props.classes.viewer_button}
          edge={"start"}
        >
          <RotateRightIcon />
        </IconButton>
      </Tooltip>
    )
    const resetRotationButton = (
      <Tooltip
        key={`resetRotation2dButton${this.props.id}`}
        title="Reset rotation"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => this.resetRotation()}
          className={this.props.classes.viewer_button}
          edge={"start"}
        >
          <RefreshCcwIcon />
        </IconButton>
      </Tooltip>
    )
    return [rotateLeftButton, rotateRightButton, resetRotationButton]
  }

  /**
   * Build the scissor (cut polyline) toolbar button. One-shot: arming it cuts
   * on the next canvas click; a successful cut (or Escape) disarms it.
   *
   * @return {JSX.Element} the cut button
   */
  protected getCutButton(): JSX.Element {
    const armed = isCutMode()
    return (
      <Tooltip
        key={`cut2dButton${this.props.id}`}
        title="Cut polyline"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              setCutMode(false)
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking &&
              (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !==
                true
            ) {
              setCurveCutMode(false)
              setStraightenMode(false)
              setCutMode(true)
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <ContentCutIcon />
        </IconButton>
      </Tooltip>
    )
  }

  /**
   * Build the divide-curve toolbar button.
   *
   * Unlike the plain cut button this does NOT break the line in two: it splits
   * the clicked bezier into two adjustable curve groups while the polyline
   * stays a single label. Arming it disarms the other one-shot tools.
   *
   * @return {JSX.Element} the divide-curve button
   */
  protected getCurveCutButton(): JSX.Element {
    const armed = isCurveCutMode()
    return (
      <Tooltip
        key={`curveCut2dButton${this.props.id}`}
        title="Divide curve"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              setCurveCutMode(false)
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking &&
              (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !==
                true
            ) {
              setCutMode(false)
              setStraightenMode(false)
              setCurveCutMode(true)
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <ContentCutCurveIcon />
        </IconButton>
      </Tooltip>
    )
  }

  /**
   * Build the straighten toolbar button.
   *
   * Arms a one-shot tool that removes the control points of the clicked curve,
   * leaving its two anchors joined by a straight span. Mutually exclusive with
   * both cut tools.
   *
   * @return {JSX.Element} the straighten button
   */
  protected getStraightenButton(): JSX.Element {
    const armed = isStraightenMode()
    return (
      <Tooltip
        key={`straighten2dButton${this.props.id}`}
        title="Straighten curve"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              setStraightenMode(false)
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking
            ) {
              setCutMode(false)
              setCurveCutMode(false)
              setStraightenMode(true)
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <StraightenIcon />
        </IconButton>
      </Tooltip>
    )
  }

  /**
   * Build the endpoint-snap toggle button.
   *
   * Unlike the one-shot tools this is a sticky mode: dragging an endpoint near
   * another normally snaps and (same category) merges the two lines, which is
   * wrong where lane lines legitimately run close together. Turning it off
   * suppresses snap, merge and indicator so vertices land exactly where they
   * are dragged.
   *
   * @return {JSX.Element} the snap toggle button
   */
  protected getSnapButton(): JSX.Element {
    const enabled = isSnapEnabled()
    return (
      <Tooltip
        key={`snap2dButton${this.props.id}`}
        title={
          enabled
            ? "Endpoint snapping on — click to keep lines separate"
            : "Endpoint snapping off — lines will not join"
        }
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            setSnapEnabled(!enabled)
          }}
          className={this.props.classes.viewer_button}
          // Tinted while OFF: the non-default state is the one worth flagging,
          // since a silent merge is what the user is trying to avoid.
          style={{ color: enabled ? undefined : "#ff9800" }}
          edge={"start"}
        >
          {enabled ? <SnapOnIcon /> : <SnapOffIcon />}
        </IconButton>
      </Tooltip>
    )
  }

  /**
   * Build the delete-segment toolbar button. Arms the two-pick delete tool;
   * clicking it while armed cancels. Mutually exclusive with the cut tool.
   *
   * @return {JSX.Element} the delete-segment button
   */
  protected getDeleteSegmentButton(): JSX.Element {
    const armed = isSegmentDeleteActive()
    return (
      <Tooltip
        key={`deleteSegment2dButton${this.props.id}`}
        title="Delete segment"
        enterDelay={500}
        TransitionComponent={Fade}
        TransitionProps={{ timeout: 600 }}
        arrow
      >
        <IconButton
          onClick={() => {
            if (armed) {
              resetSegmentDelete()
            } else if (
              !Session.label2dList.isDrawingInProgress() &&
              !this.state.task.config.tracking &&
              (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !==
                true
            ) {
              armSegmentDelete()
            }
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined }}
          edge={"start"}
        >
          <DeleteSegmentIcon />
        </IconButton>
      </Tooltip>
    )
  }

  /**
   * Build the split select button: the dashed-rectangle icon arms/disarms the
   * currently-selected mode (freeform lasso or rectangle); the caret opens a
   * menu (with per-mode icons) to switch mode. Green when armed. Mutually
   * exclusive with the cut and delete-segment tools.
   *
   * @return {JSX.Element} the select split button
   */
  protected getFreeformSelectButton(): JSX.Element {
    const armed = isFreeformArmed()
    const mode = getSelectMode()
    const canArm = (): boolean =>
      !Session.label2dList.isDrawingInProgress() &&
      !this.state.task.config.tracking &&
      (this._viewerConfig as ImageViewerConfigType)?.showCurvesOnly !== true
    return (
      <React.Fragment key={`selectSplit2dButton${this.props.id}`}>
        <Tooltip
          title={mode === "rectangle" ? "Rectangle select" : "Freeform select"}
          enterDelay={500}
          TransitionComponent={Fade}
          TransitionProps={{ timeout: 600 }}
          arrow
        >
          <IconButton
            onClick={() => {
              if (armed) {
                resetFreeform()
              } else if (canArm()) {
                armFreeform()
              }
            }}
            className={this.props.classes.viewer_button}
            style={{ color: armed ? "#4caf50" : undefined }}
            edge={"start"}
          >
            <RectangleSelectIcon />
          </IconButton>
        </Tooltip>
        <IconButton
          size="small"
          onClick={(e) => {
            this._selectMenuAnchor = e.currentTarget
            this.forceUpdate()
          }}
          className={this.props.classes.viewer_button}
          style={{ color: armed ? "#4caf50" : undefined, padding: 0 }}
        >
          <ArrowDropDownIcon />
        </IconButton>
        <Menu
          anchorEl={this._selectMenuAnchor}
          open={this._selectMenuAnchor !== null}
          onClose={() => {
            this._selectMenuAnchor = null
            this.forceUpdate()
          }}
        >
          <MenuItem
            selected={mode === "freeform"}
            onClick={() => {
              setSelectMode("freeform")
              if (!isFreeformArmed() && canArm()) {
                armFreeform()
              }
              this._selectMenuAnchor = null
              this.forceUpdate()
            }}
          >
            <FreeformSelectIcon fontSize="small" style={{ marginRight: 8 }} />
            Freeform
          </MenuItem>
          <MenuItem
            selected={mode === "rectangle"}
            onClick={() => {
              setSelectMode("rectangle")
              if (!isFreeformArmed() && canArm()) {
                armFreeform()
              }
              this._selectMenuAnchor = null
              this.forceUpdate()
            }}
          >
            <RectangleSelectIcon fontSize="small" style={{ marginRight: 8 }} />
            Rectangle
          </MenuItem>
        </Menu>
      </React.Fragment>
    )
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
        this.panBy(dx, dy)
      }
    }
  }

  /**
   * Shift the view by a pixel delta, batched to one repaint per frame.
   *
   * Accumulates raw deltas within the frame so fast input does not drop
   * sub-frame movement. `_pendingPan` holds the SUMMED delta and the RAF
   * applies it on top of the latest committed config. (Storing an absolute
   * snapshot off a stale displayLeft would discard every event except the last
   * one before the frame ticked.)
   *
   * @param dx horizontal shift in display pixels
   * @param dy vertical shift in display pixels
   */
  protected panBy(dx: number, dy: number): void {
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
        if (pan === null || this._viewerConfig === undefined) {
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
      // A horizontal wheel (tilt wheel, side-scroll button, or a trackpad
      // two-finger swipe) pans instead of zooming. Such an event carries
      // deltaX with deltaY at or near zero; feeding it to the zoom path below
      // would read deltaY === 0 as "zoom in" and the horizontal intent would
      // be lost entirely. Shift+wheel is the conventional
      // horizontal-scroll alias, and browsers deliver it as deltaX on some
      // platforms and deltaY on others, so both are accepted.
      // Ctrl/Meta is excluded: that is the pinch/zoom gesture.
      if (!e.ctrlKey && !e.metaKey) {
        const horizontal =
          Math.abs(e.deltaX) > Math.abs(e.deltaY)
            ? e.deltaX
            : e.shiftKey
            ? e.deltaY
            : 0
        if (horizontal !== 0) {
          this.panBy(-horizontal * WHEEL_PAN_RATIO, 0)
          return
        }
      }
      // Plain mouse-wheel scroll zooms directly — no modifier required. Ctrl/
      // Meta scroll zooms too, and trackpad pinch arrives as a wheel event with
      // ctrlKey set, so pinch-to-zoom is covered as well. Reading deltaY from
      // the event (not tracked key state) keeps this correct inside an embedded
      // iframe that hasn't received a focus/keydown yet.
      // One wheel notch zooms by ZOOM_RATIO (the same step as the +/- keys and
      // toolbar buttons) so wheel zoom feels as fast as keyboard/button zoom.
      let zoomRatio = ZOOM_RATIO
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
