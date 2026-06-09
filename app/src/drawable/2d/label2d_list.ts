import _ from "lodash"

import { policyFromString } from "../../common/track"
import { LabelTypeName, TrackPolicyType } from "../../const/common"
import { makeState } from "../../functional/states"
import { Label2DTemplateType, ModeStatus, State } from "../../types/state"
import { Context2D } from "../util"
import { Box2D } from "./box2d"
import { CustomLabel2D } from "./custom_label"
import { DrawMode, Label2D } from "./label2d"
import { Polygon2D } from "./polygon2d"
import { Tag2D } from "./tag2d"

/**
 * Make a new drawable label based on the label type
 *
 * @param {string} labelType: type of the new label
 * @param labelList
 * @param labelType
 */
export function makeDrawableLabel2D(
  labelList: Label2DList,
  labelType: string,
  labelTemplates: { [name: string]: Label2DTemplateType }
): Label2D | null {
  if (labelType in labelTemplates) {
    return new CustomLabel2D(labelList, labelTemplates[labelType])
  }
  switch (labelType) {
    case LabelTypeName.BOX_2D:
      return new Box2D(labelList)
    case LabelTypeName.TAG:
      return new Tag2D(labelList)
    case LabelTypeName.POLYGON_2D:
      return new Polygon2D(labelList, true)
    case LabelTypeName.POLYLINE_2D:
      return new Polygon2D(labelList, false)
  }
  return null
}

/**
 * List of drawable labels
 * ViewController for the labels
 */
export class Label2DList {
  /** Label the labels */
  private _labels: { [labelId: string]: Label2D }
  /** list of the labels sorted by label order */
  private _labelList: Label2D[]
  /** selected label */
  private _selectedLabels: Label2D[]
  /** state */
  private _state: State
  /** label templates */
  private _labelTemplates: { [name: string]: Label2DTemplateType }
  /** callbacks */
  private readonly _callbacks: Array<() => void>
  /** New labels to be committed */
  private readonly _updatedLabels: Set<Label2D>

  /**
   * Constructor
   */
  constructor() {
    this._labels = {}
    this._labelList = []
    this._selectedLabels = []
    this._state = makeState()
    this._callbacks = []
    this._labelTemplates = {}
    this._updatedLabels = new Set()
  }

  /**
   * Access the drawable label by index
   *
   * @param index
   */
  public get(index: number): Label2D {
    return this._labelList[index]
  }

  /**
   * Subscribe callback for drawable update
   *
   * @param callback
   */
  public subscribe(callback: () => void): void {
    this._callbacks.push(callback)
  }

  /**
   * Unsubscribe callback for drawable update
   *
   * @param callback
   */
  public unsubscribe(callback: () => void): void {
    const index = this._callbacks.indexOf(callback)
    if (index >= 0) {
      this._callbacks.splice(index, 1)
    }
  }

  /**
   * Whether a requestAnimationFrame is already scheduled for redraw.
   * Prevents scheduling multiple redundant repaints within the same frame.
   */
  private _rafPending: boolean = false

  /**
   * Call when any drawable has been updated.
   *
   * All calls that arrive within the same animation frame are collapsed into
   * a single actual repaint. This caps the canvas repaint rate at the display
   * refresh rate (≤60fps) regardless of how fast mouse-move or key events
   * fire. At high zoom levels canvases can be very large (viewScale × 2 ×
   * display-size), so preventing redundant repaints is the single most
   * impactful performance optimisation. Label coordinates and hit detection
   * are unaffected because the LATEST state is always used when the frame
   * callback actually executes.
   */
  public onDrawableUpdate(): void {
    if (!this._rafPending) {
      this._rafPending = true
      requestAnimationFrame(() => {
        this._rafPending = false
        for (const callback of this._callbacks) {
          callback()
        }
      })
    }
  }

  /**
   * Get label by id
   *
   * @param id
   */
  public getLabelById(id: number): Label2D {
    return this._labels[id]
  }

  /** Whether a polyline/polygon is currently being drawn (unfinished) */
  public isDrawingInProgress(): boolean {
    const label = this._selectedLabels[0]
    return label instanceof Polygon2D && label.isDrawing
  }

  /**
   * Discard the in-progress (temporary) drawing without committing it.
   * Mirrors how commit2DLabels drops invalid temporary drawables, but on demand.
   */
  public cancelDrawing(): void {
    const label = this._selectedLabels[0]
    if (!(label instanceof Polygon2D && label.isDrawing)) {
      return
    }
    label.editing = false
    const selectedIndex = this._selectedLabels.indexOf(label)
    if (selectedIndex >= 0) {
      this._selectedLabels.splice(selectedIndex, 1)
    }
    const listIndex = this._labelList.indexOf(label)
    if (listIndex >= 0) {
      this._labelList.splice(listIndex, 1)
    }
    this.onDrawableUpdate()
  }

  /** get label list for state inspection */
  public get labelList(): Label2D[] {
    return this._labelList
  }

  /** get selectedLabel for state inspection */
  public get selectedLabels(): Label2D[] {
    return this._selectedLabels
  }

  /**
   * Get id's of selected labels
   */
  public get selectedLabelIds(): { [index: number]: string[] } {
    return this._state.user.select.labels
  }

  /**
   * Get current policy type
   */
  public get policyType(): TrackPolicyType {
    return policyFromString(
      this._state.task.config.policyTypes[this._state.user.select.policyType]
    )
  }

  /**
   * Draw label and control context
   *
   * @param {Context2D} labelContext
   * @param {Context2D} controlContext
   * @param {number} ratio: ratio: display to image size ratio
   * @param ratio
   * @param hideLabels
   * @param hideLabelTags
   * @param sessionMode
   * @param viewScale: current zoom level (1 = no zoom)
   * @param viewportBounds: optional viewport bounds for culling [x, y, width, height] in image coords
   * @param hiddenLabelTypes: label type names to hide (e.g. ['box2d'])
   * @param hiddenCategories: category indices to hide (e.g. [0, 2])
   */
  public redraw(
    labelContext: Context2D,
    controlContext: Context2D,
    ratio: number,
    hideLabels?: boolean,
    hideLabelTags?: boolean,
    sessionMode?: ModeStatus,
    viewScale?: number,
    viewportBounds?: [number, number, number, number],
    hiddenLabelTypes?: string[],
    hiddenCategories?: number[],
    drawControl: boolean = true,
    lineWidthMultiplier: number = 1
  ): void {
    const isTrackLinking = this._state.session.trackLinking
    let labelsToDraw =
      hideLabels !== null && hideLabels !== undefined && hideLabels
        ? this._labelList.filter((label) => label.selected)
        : this._labelList

    // Per-type visibility: hide label types that are toggled off in the sidebar
    // Selected labels are always shown so users can't lose their selection
    if (hiddenLabelTypes !== undefined && hiddenLabelTypes.length > 0) {
      labelsToDraw = labelsToDraw.filter(
        (label) => label.selected || !hiddenLabelTypes.includes(label.type)
      )
    }

    // Per-category visibility: hide categories that are toggled off in the
    // sidebar. Only the label being actively drawn/edited is exempt (so it
    // stays visible while you work on it). A merely-selected committed label
    // still obeys the checkbox — gating on `selected` here would leave every
    // newly-added or just-edited polyline permanently visible, because the
    // drawable's `selected` flag isn't reset when the Redux selection changes.
    if (hiddenCategories !== undefined && hiddenCategories.length > 0) {
      labelsToDraw = labelsToDraw.filter(
        (label) =>
          label.editing || !hiddenCategories.includes(label.category[0])
      )
    }

    // Viewport culling: at high zoom, only draw labels within visible area
    // This provides massive performance improvement when zoomed in on large images
    if (
      viewportBounds !== undefined &&
      viewScale !== undefined &&
      viewScale > 2
    ) {
      const [vx, vy, vw, vh] = viewportBounds
      // Add margin to prevent labels from popping in/out at edges
      const margin = 50 / ratio
      labelsToDraw = labelsToDraw.filter((label) => {
        const bounds = label.bounds()
        if (bounds === null) return true // Always draw labels without bounds
        const [lx, ly, lw, lh] = bounds
        // Check if label bounds intersect viewport
        return !(
          lx + lw < vx - margin ||
          lx > vx + vw + margin ||
          ly + lh < vy - margin ||
          ly > vy + vh + margin
        )
      })
    }

    labelsToDraw.forEach((v) => {
      const passes = drawControl
        ? [
            { ctx: labelContext, mode: DrawMode.VIEW },
            { ctx: controlContext, mode: DrawMode.CONTROL }
          ]
        : [{ ctx: labelContext, mode: DrawMode.VIEW }]
      passes.forEach(({ ctx, mode }) => {
        v.draw(
          ctx,
          ratio,
          mode,
          isTrackLinking,
          hideLabelTags ?? false,
          sessionMode,
          viewScale,
          lineWidthMultiplier
        )
      })
    })
  }

  /**
   * update labels from the state
   *
   * @param state
   */
  public updateState(state: State): void {
    // Don't interrupt ongoing editing
    if (this._selectedLabels.length > 0 && this.selectedLabels[0].editing) {
      return
    }

    this._state = state
    this._labelTemplates = state.task.config.label2DTemplates
    const itemIndex = state.user.select.item
    const item = state.task.items[itemIndex]
    let sensor = -1
    if (state.user.viewerConfigs[0] !== undefined) {
      sensor = state.user.viewerConfigs[0].sensor
    }
    // Remove any label not in the state
    this._labels = Object.assign({}, _.pick(this._labels, _.keys(item.labels)))
    for (const key of Object.keys(this._labels)) {
      if (!this._labels[key].label.sensors.includes(sensor)) {
        // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
        delete this._labels[key]
      }
    }
    // Update drawable label values
    _.forEach(item.labels, (label, labelId) => {
      if (label.sensors.includes(sensor)) {
        if (!(labelId in this._labels)) {
          const newLabel = makeDrawableLabel2D(
            this,
            label.type,
            this._labelTemplates
          )
          if (newLabel !== null) {
            this._labels[labelId] = newLabel
          }
        }
        if (labelId in this._labels) {
          const drawableLabel = this._labels[labelId]
          if (!drawableLabel.editing) {
            drawableLabel.updateState(state, itemIndex, labelId)
          }
        }
      }
    })
    // Order the labels and assign order values
    this._labelList = _.sortBy(_.values(this._labels), [(label) => label.order])
    _.forEach(this._labelList, (l: Label2D, index: number) => {
      l.index = index
    })
    this._selectedLabels = []
    const select = state.user.select
    const selectedLabelItems = Object.keys(select.labels)
    for (const key of selectedLabelItems) {
      const index = Number(key)
      for (const id of select.labels[index]) {
        if (id in this._labels) {
          this._selectedLabels.push(this._labels[id])
        }
      }
    }
  }

  /** Get uncommitted labels */
  public popUpdatedLabels(): Label2D[] {
    const labels = [...this._updatedLabels.values()]
    this._updatedLabels.clear()
    return labels
  }

  /**
   * Push updated label to array
   *
   * @param label
   */
  public addUpdatedLabel(label: Label2D): void {
    this._updatedLabels.add(label)
  }

  /** Clear uncommitted label list */
  public clearUpdatedLabels(): void {
    this._updatedLabels.clear()
  }
}
