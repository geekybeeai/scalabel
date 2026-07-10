import _ from "lodash"

import { isKeyHeld } from "../../common/keyboard_state"
import { isMarked } from "../../common/multi_delete_state"
import { Cursor, Key, LabelTypeName } from "../../const/common"
import { makeLabel } from "../../functional/states"
import { Size2D } from "../../math/size2d"
import { Vector2D } from "../../math/vector2d"
import {
  IdType,
  LabelType,
  ModeStatus,
  PathPoint2DType,
  PathPointType,
  ShapeType,
  State
} from "../../types/state"
import { blendColor, Context2D, encodeControlColor, getColorByCategory, toCssColor } from "../util"
import { DASH_LINE, DELETE_HIGHLIGHT_COLOR, MIN_SIZE, OPACITY } from "./common"
import { curveGroupIndices } from "./curve_groups"
import { getAntsOffset } from "./marching_ants"
import { DrawMode, Label2D } from "./label2d"
import { Label2DList } from "./label2d_list"
import {
  makeDrawablePathPoint2D,
  makeEdge2DStyle,
  makePathPoint2DStyle,
  PathPoint2D
} from "./path_point2d"

const DEFAULT_VIEW_EDGE_STYLE = makeEdge2DStyle({ lineWidth: 8 })
const DEFAULT_VIEW_POINT_STYLE = makePathPoint2DStyle({ radius: 8 })
const DEFAULT_VIEW_HIGH_POINT_STYLE = makePathPoint2DStyle({ radius: 12 })
const DEFAULT_CONTROL_EDGE_STYLE = makeEdge2DStyle({ lineWidth: 10 })
const DEFAULT_CONTROL_POINT_STYLE = makePathPoint2DStyle({ radius: 12 })
const DEFAULT_CONTROL_HIGH_POINT_STYLE = makePathPoint2DStyle({ radius: 14 })
/** Extra stroke width multiplier for a marked line, for emphasis. */
const MULTI_DELETE_HIGHLIGHT_WIDTH_FACTOR = 1.5

/** list all states */
export enum Polygon2DState {
  FREE,
  DRAW,
  FINISHED,
  RESHAPE,
  MOVE
}

/** list all orientation types */
enum OrientationType {
  COLLINEAR,
  CLOCKWISE,
  COUNTERCLOCKWISE
}

/**
 * polygon 2d label
 */
export class Polygon2D extends Label2D {
  /** array for vertices */
  private _points: PathPoint2D[]
  /** polygon label state */
  private _state: Polygon2DState
  /** mouse position */
  private _mouseCoord: Vector2D
  /** cache shape points for dragging, both move and reshape */
  private _startingPoints: PathPoint2D[]
  /** The hashed list of keys currently down */
  private _keyDownMap: { [key: string]: boolean }
  /** open or closed */
  private _closed: boolean
  /** snap target polyline */
  private _snapTargetPolyline: Polygon2D | null
  /** snap target endpoint index */
  private _snapTargetPointIndex: number
  /** whether this polyline has been merged out into another */
  private _mergedOut: boolean

  /**
   * Constructor
   *
   * @param labelList
   * @param closed
   */
  constructor(labelList: Label2DList, closed: boolean) {
    super(labelList)
    this._points = []
    this._state = Polygon2DState.FREE
    this._mouseCoord = new Vector2D()
    this._startingPoints = []
    this._keyDownMap = {}
    this._closed = closed
    this._snapTargetPolyline = null
    this._snapTargetPointIndex = -1
    this._mergedOut = false
  }

  /** Get cursor for highlighting */
  public get highlightCursor(): string {
    if (this._state === Polygon2DState.DRAW) {
      return Cursor.CROSSHAIR
    } else if (
      this._highlightedHandle > 0 &&
      this._highlightedHandle <= this._points.length
    ) {
      return Cursor.DEFAULT
    } else {
      return Cursor.MOVE
    }
  }

  /** Whether this polygon is currently being drawn (vertices being placed) */
  public get isDrawing(): boolean {
    return this._state === Polygon2DState.DRAW
  }

  /** Get highlighted handle */
  public get highlightedHandle(): number {
    return this._highlightedHandle
  }

  /**
   * Update state of the label
   *
   * @param state
   * @param itemIndex
   * @param labelId
   */
  public updateState(state: State, itemIndex: number, labelId: IdType): void {
    super.updateState(state, itemIndex, labelId)
    if (this._label !== null) {
      this._closed = this._label.type === LabelTypeName.POLYGON_2D || !!this._label.closed
    }
  }

  /**
   * Set (cloned) points
   */
  public set points(ps: PathPoint2D[]) {
    this._points = ps.map((p) => p.clone())
  }

  /**
   * Get (cloned) points
   */
  public get points(): PathPoint2D[] {
    return this._points.map((p) => p.clone())
  }

  /**
   * Get whether the polyline/polygon is closed
   */
  public get closed(): boolean {
    return this._closed
  }

  /**
   * Whether this shape contains at least one bezier control point.
   */
  public get hasCurves(): boolean {
    return this._points.some((p) => p.type === PathPointType.CURVE)
  }

  /**
   * Check if a vertex index in this._points is an endpoint
   * Only applicable to polylines (non-closed)
   *
   * @param vertexIndex
   */
  public isEndpoint(vertexIndex: number): boolean {
    if (this._closed) {
      return false
    }
    return vertexIndex === 0 || vertexIndex === this._points.length - 1
  }

  /**
   * Draw snap indicator (glowing halo, enlarged green endpoint, white center dot)
   *
   * @param context
   * @param ratio
   * @param styleFactor
   */
  private drawSnapIndicator(
    context: Context2D,
    ratio: number,
    styleFactor: number
  ): void {
    if (this._snapTargetPolyline === null || this._snapTargetPointIndex === -1) {
      return
    }
    const targetPoint =
      this._snapTargetPolyline._points[this._snapTargetPointIndex]
    const realCoord = targetPoint.vector().scale(ratio)

    // Scale the join indicator with zoom the same way vertices/edges do (via
    // styleFactor = 1/sqrt(zoom)), so the green dot tracks the line and point
    // sizes instead of staying a fixed pixel size at every zoom level. The
    // floors mirror the point-style floors in draw() so it never vanishes.
    const haloRadius = Math.max(4, 16 * styleFactor)
    const endpointRadius = Math.max(2, 8 * styleFactor)
    const centerRadius = Math.max(1, 3 * styleFactor)
    const strokeWidth = Math.max(1, 2 * styleFactor)

    context.save()

    // 1. Green outer halo
    context.beginPath()
    context.strokeStyle = "rgba(0, 255, 0, 0.8)"
    context.fillStyle = "rgba(0, 255, 0, 0.2)"
    context.lineWidth = strokeWidth
    context.arc(realCoord.x, realCoord.y, haloRadius, 0, 2 * Math.PI)
    context.fill()
    context.stroke()

    // 2. Slightly enlarged green endpoint
    context.beginPath()
    context.fillStyle = "rgba(0, 255, 0, 0.9)"
    context.arc(realCoord.x, realCoord.y, endpointRadius, 0, 2 * Math.PI)
    context.fill()

    // 3. White center dot
    context.beginPath()
    context.fillStyle = "#ffffff"
    context.arc(realCoord.x, realCoord.y, centerRadius, 0, 2 * Math.PI)
    context.fill()

    context.restore()
  }

  /**
   * Clear the snap state variables
   */
  private clearSnapState(): void {
    this._snapTargetPolyline = null
    this._snapTargetPointIndex = -1
  }

  /**
   * Merge this polyline with another polyline at the specified endpoint
   *
   * @param targetPolyline
   * @param isStartB
   */
  private mergeWith(targetPolyline: Polygon2D, isStartB: boolean): void {
    if (targetPolyline === this) {
      // Self-closing
      const vertices = this.getVertices()
      const draggedIndex = this._highlightedHandle - 1
      const isStartA = draggedIndex === 0

      if (isStartA) {
        vertices.shift() // Remove start vertex
      } else {
        vertices.pop() // Remove end vertex
      }

      this._closed = true
      if (this._label !== null) {
        this._label.closed = true
      }

      const shapes = vertices.map((v) => v.shape())
      this.updateShapes(shapes)
      this._labelList.addUpdatedLabel(this)
      return
    }

    const verticesA = this.getVertices()
    const verticesB = targetPolyline.getVertices()

    const cloneVertex = (v: PathPoint2D) => {
      return makeDrawablePathPoint2D(v.x, v.y, v.type, this._label?.id)
    }

    const draggedIndex = this._highlightedHandle - 1
    const isStartA = draggedIndex === 0

    let mergedVertices: PathPoint2D[] = []

    if (!isStartA && isStartB) {
      // 1. A.end -> B.start
      const clonedB = verticesB.map(cloneVertex)
      clonedB.shift() // remove B.start
      mergedVertices = [...verticesA, ...clonedB]
    } else if (isStartA && !isStartB) {
      // 2. A.start -> B.end
      const clonedB = verticesB.map(cloneVertex)
      clonedB.pop() // remove B.end
      mergedVertices = [...clonedB, ...verticesA]
    } else if (isStartA && isStartB) {
      // 3. A.start -> B.start (reverse A)
      const reversedA = [...verticesA].reverse()
      const clonedB = verticesB.map(cloneVertex)
      clonedB.shift() // remove B.start
      mergedVertices = [...reversedA, ...clonedB]
    } else {
      // 4. A.end -> B.end (reverse B)
      const clonedReversedB = verticesB.map(cloneVertex).reverse()
      clonedReversedB.shift() // remove B.end
      mergedVertices = [...verticesA, ...clonedReversedB]
    }

    // Recreate midpoints automatically by calling updateShapes
    const shapes = mergedVertices.map((v) => v.shape())
    this.updateShapes(shapes)

    // Mark target as merged out and queue it for deletion
    targetPolyline._mergedOut = true
    targetPolyline.clearSnapState()
    this._labelList.addUpdatedLabel(targetPolyline)
    this._labelList.addUpdatedLabel(this)
  }

  /**
   * Get the bounding box of this polygon in image coordinates
   * Returns [x, y, width, height] or null if no points
   */
  public bounds(): [number, number, number, number] | null {
    if (this._points.length === 0) {
      return null
    }
    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    for (const point of this._points) {
      const v = point.vector()
      minX = Math.min(minX, v.x)
      minY = Math.min(minY, v.y)
      maxX = Math.max(maxX, v.x)
      maxY = Math.max(maxY, v.y)
    }
    return [minX, minY, maxX - minX, maxY - minY]
  }

  /**
   * Draw the label on viewing or control canvas
   *
   * @param _context
   * @param _ratio
   * @param _mode
   * @param context
   * @param ratio
   * @param mode
   * @param isTrackLinking
   * @param hideLabelTags
   * @param sessionMode
   * @param viewScale: current zoom level (1 = no zoom)
   * @param lineWidthMultiplier
   * @param curvesOnly draw only bezier groups (curves-only display)
   */
  public draw(
    context: Context2D,
    ratio: number,
    mode: DrawMode,
    isTrackLinking: boolean,
    hideLabelTags: boolean,
    sessionMode: ModeStatus | undefined,
    viewScale: number = 1,
    lineWidthMultiplier: number = 1,
    curvesOnly: boolean = false
  ): void {
    const numPoints = this._points.length

    // Curves-only display: stroke only bezier groups; the label being
    // actively edited is exempt and renders in full.
    const curvesOnlyActive = curvesOnly && !this.editing
    const curveGroups = curvesOnlyActive
      ? curveGroupIndices(
          this._points.map((p) => p.type),
          this._closed
        )
      : null
    if (curveGroups !== null && curveGroups.length === 0) {
      return
    }

    // Compute zoom-aware scale factor: styles thin out as you zoom in
    const zoomScale = Math.max(1, viewScale)
    const styleFactor = 1 / Math.sqrt(zoomScale)

    if (numPoints === 0) return
    let pointStyle = makePathPoint2DStyle()
    let highPointStyle = makePathPoint2DStyle()
    let edgeStyle = makeEdge2DStyle()
    let assignColor: (i: number) => number[] = () => [0]

    switch (mode) {
      case DrawMode.VIEW:
        pointStyle = _.assign(pointStyle, DEFAULT_VIEW_POINT_STYLE)
        highPointStyle = _.assign(highPointStyle, DEFAULT_VIEW_HIGH_POINT_STYLE)
        edgeStyle = _.assign(edgeStyle, DEFAULT_VIEW_EDGE_STYLE)
        // Scale styles inversely with zoom for better precision at high zoom
        pointStyle.radius = Math.max(2, pointStyle.radius * styleFactor)
        highPointStyle.radius = Math.max(3, highPointStyle.radius * styleFactor)
        edgeStyle.lineWidth = Math.max(
          1,
          edgeStyle.lineWidth * styleFactor * lineWidthMultiplier
        )
        assignColor = (i: number): number[] => {
          if (
            i > 0 &&
            i <= this._points.length &&
            this._points[i - 1].type !== PathPointType.LINE
          ) {
            return blendColor(this._color, [255, 255, 255], 0.7)
          } else {
            return this._color
          }
        }
        break
      case DrawMode.CONTROL:
        pointStyle = _.assign(pointStyle, DEFAULT_CONTROL_POINT_STYLE)
        highPointStyle = _.assign(
          highPointStyle,
          DEFAULT_CONTROL_HIGH_POINT_STYLE
        )
        edgeStyle = _.assign(edgeStyle, DEFAULT_CONTROL_EDGE_STYLE)
        assignColor = (i: number): number[] => {
          return encodeControlColor(this._index, i)
        }
        break
    }

    // Draw line first
    edgeStyle.color = assignColor(0)
    context.save()
    context.strokeStyle = toCssColor(edgeStyle.color)
    context.lineWidth = edgeStyle.lineWidth
    // Lines Ctrl+clicked for batch deletion are stroked with the shared delete
    // color as animated marching ants (view canvas only — never override the
    // CONTROL canvas, whose color encodes hit ids). The dash offset is shared
    // with the delete-segment overlay so both animate in lockstep.
    if (mode === DrawMode.VIEW && isMarked(this.labelId)) {
      context.strokeStyle = DELETE_HIGHLIGHT_COLOR
      context.lineWidth =
        edgeStyle.lineWidth * MULTI_DELETE_HIGHLIGHT_WIDTH_FACTOR
      context.setLineDash(DASH_LINE)
      context.lineDashOffset = -getAntsOffset()
    }
    context.beginPath()
    if (curveGroups !== null) {
      for (const [a, c1, c2, b] of curveGroups) {
        const pa = this._points[a].vector().scale(ratio)
        const p1 = this._points[c1].vector().scale(ratio)
        const p2 = this._points[c2].vector().scale(ratio)
        const pb = this._points[b].vector().scale(ratio)
        context.moveTo(pa.x, pa.y)
        context.bezierCurveTo(p1.x, p1.y, p2.x, p2.y, pb.x, pb.y)
      }
    } else {
      const begin = this._points[0].vector().scale(ratio)
      context.moveTo(begin.x, begin.y)
      for (let i = 1; i < numPoints; ++i) {
        const point = this._points[i]
        const pointToDraw = point.vector().scale(ratio)
        if (point.type === PathPointType.CURVE) {
          const nextPointToDraw = this._points[(i + 1) % numPoints]
            .vector()
            .scale(ratio)
          const nextVertexToDraw = this._points[(i + 2) % numPoints]
            .vector()
            .scale(ratio)
          context.bezierCurveTo(
            pointToDraw.x,
            pointToDraw.y,
            nextPointToDraw.x,
            nextPointToDraw.y,
            nextVertexToDraw.x,
            nextVertexToDraw.y
          )
          i = i + 2
        } else if (point.type === PathPointType.LINE) {
          context.lineTo(pointToDraw.x, pointToDraw.y)
        }
      }

      if (this._state === Polygon2DState.DRAW) {
        const tmp = this._mouseCoord.clone().scale(ratio)
        context.lineTo(tmp.x, tmp.y)
      }

      if (this._closed) {
        context.lineTo(begin.x, begin.y)
        context.closePath()
        if (mode === DrawMode.VIEW) {
          const fillStyle = this._color.concat(OPACITY)
          context.fillStyle = toCssColor(fillStyle)
          context.fill()
        } else if (sessionMode === ModeStatus.SELECTING) {
          context.fillStyle = toCssColor(edgeStyle.color)
          context.fill()
        }
      }
    }
    context.stroke()
    context.restore()

    if (mode === DrawMode.CONTROL || this._selected || this._highlighted) {
      // Bezier control-polygon: the dashed line linking a vertex to its curve
      // control point. Drawn ONLY on the hit-detection (control) canvas; hidden
      // from the visible view because it clutters the curve while drawing.
      if (mode === DrawMode.CONTROL) {
        context.save()
        context.setLineDash(DASH_LINE)
        context.beginPath()
        for (let i = 0; i < numPoints; ++i) {
          const point = this._points[i]
          const nextPoint = this._points[(i + 1) % numPoints]
          if (
            (point.type === PathPointType.LINE &&
              nextPoint.type === PathPointType.CURVE) ||
            point.type === PathPointType.CURVE
          ) {
            const coord0 = point.vector().scale(ratio)
            const coord1 = nextPoint.vector().scale(ratio)
            context.moveTo(coord0.x, coord0.y)
            context.lineTo(coord1.x, coord1.y)
            context.stroke()
          }
        }
        context.closePath()
        context.restore()
      }

      // Draw points
      if (this._state === Polygon2DState.DRAW) {
        const tmpPoint = makeDrawablePathPoint2D(
          this._mouseCoord.x,
          this._mouseCoord.y,
          PathPointType.UNKNOWN,
          this._label?.id
        )
        const tmpStyle = { ...pointStyle }
        tmpStyle.color = assignColor(numPoints + 1)
        tmpPoint.draw(context, ratio, tmpStyle)
        let numVertices = 1
        _.forEach(this._points, (point, index) => {
          if (point.type === PathPointType.LINE) {
            let style = { ...pointStyle }
            if (numVertices === this._highlightedHandle) {
              style = { ...highPointStyle }
            }
            style.color = assignColor(index + 1)
            point.draw(context, ratio, style)
            numVertices++
          }
        })
      } else if (
        this._state === Polygon2DState.FINISHED ||
        this._state === Polygon2DState.RESHAPE ||
        this._state === Polygon2DState.MOVE
      ) {
        for (let i = 0; i < numPoints; ++i) {
          if (
            curveGroups !== null &&
            !curveGroups.some((g) => g.includes(i))
          ) {
            continue
          }
          const point = this._points[i]
          let style = { ...pointStyle }
          if (i + 1 === this._highlightedHandle) {
            style = { ...highPointStyle }
          }
          style.color = assignColor(i + 1)
          if (mode === DrawMode.VIEW && point.type === PathPointType.CURVE) {
            style.color = [0, 255, 255]
            style.strokeColor = [0, 0, 0]
          }
          point.draw(context, ratio, style)
        }
      }
    }

    let checked = false
    if (this._label !== null) {
      checked = this._label.checked
    }
    if (
      !hideLabelTags &&
      mode === DrawMode.VIEW &&
      this._state !== Polygon2DState.DRAW
    ) {
      if (this._selected) {
        isTrackLinking = isTrackLinking && true
      } else {
        isTrackLinking = isTrackLinking && false
      }
      this.drawTag(
        context,
        ratio,
        this.getCentroid(),
        this._color,
        isTrackLinking,
        checked
      )
    }
    if (mode === DrawMode.VIEW) {
      this.drawSnapIndicator(context, ratio, styleFactor)
    }
  }

  /**
   * Handle mouse down
   *
   * @param coord
   * @param handleIndex
   */
  public onMouseDown(coord: Vector2D, handleIndex: number): boolean {
    this._mouseDown = true
    this._mouseCoord = coord.clone()
    if (this._selected) {
      this._mouseDownCoord = coord.clone()
      if (
        this._state === Polygon2DState.FINISHED &&
        this._highlightedHandle < 0
      ) {
        // Not click edge or point
        return true
      } else if (
        this._state === Polygon2DState.FINISHED &&
        this._highlightedHandle > 0
      ) {
        // Click point
        this._state = Polygon2DState.RESHAPE
        this.editing = true
        // Held-key checks read the module-level keyboard state (fed by the
        // canvas's document listeners), NOT this instance's _keyDownMap: the
        // select-on-click dispatch rebuilds this drawable mid-gesture with an
        // empty key map, which made C/D+click silently fail (reproducibly on
        // trackpads). See common/keyboard_state.ts.
        if (isKeyHeld(Key.C_UP) || isKeyHeld(Key.C_LOW)) {
          // Convert line to bezier curve; the drag that follows shapes it
          this.lineToCurve()
        } else if (isKeyHeld(Key.D_UP) || isKeyHeld(Key.D_LOW)) {
          // Delete vertex
          // Disable deletion for now
          this.toCache()
          this.deleteVertex()
        } else {
          // Drag vertex or midpoint
          this.toCache()
          if (
            this._points[this._highlightedHandle - 1].type === PathPointType.MID
          ) {
            // Drag midpoint: convert midpoint to vertex first
            this.midToVertex()
          }
        }
        this._labelList.addUpdatedLabel(this)
        return true
      } else if (
        this._state === Polygon2DState.FINISHED &&
        this._highlightedHandle === 0 &&
        handleIndex === 0
      ) {
        // Drag edges
        this._state = Polygon2DState.MOVE
        this.editing = true
        this.toCache()
        return true
      }
      return true
    }
    return false
  }

  /**
   * Handle mouse move
   *
   * @param coord
   * @param _limit
   * @param _labelIndex
   * @param handleIndex
   */
  public onMouseMove(
    coord: Vector2D,
    _limit: Size2D,
    _labelIndex: number,
    handleIndex: number
  ): boolean {
    if (this._state === Polygon2DState.DRAW) {
      // Move to add vertex
      this._mouseCoord = coord.clone()
      this._highlightedHandle = handleIndex
    } else if (this._mouseDown && this._state === Polygon2DState.RESHAPE) {
      // Dragging point
      const draggedIndex = this._highlightedHandle - 1
      if (this.isEndpoint(draggedIndex)) {
        const candidate = this._labelList.findNearestEndpoint(this, coord, 15)
        if (candidate !== null) {
          this._snapTargetPolyline = candidate.polyline
          this._snapTargetPointIndex = candidate.isStart
            ? 0
            : candidate.polyline.points.length - 1
          // Snap the coordinates of the dragged vertex to the target coordinates
          const targetPoint = candidate.polyline._points[this._snapTargetPointIndex]
          coord.x = targetPoint.x
          coord.y = targetPoint.y
        } else {
          this.clearSnapState()
        }
      } else {
        this.clearSnapState()
      }

      this.reshape(coord, _limit)
      this._labelList.addUpdatedLabel(this)
    } else if (this._mouseDown && this._state === Polygon2DState.MOVE) {
      // Dragging edges
      this.move(coord, _limit)
      this._labelList.addUpdatedLabel(this)
    }
    return true
  }

  /**
   * Handle mouse up
   *
   * @param coord
   */
  public onMouseUp(coord: Vector2D): boolean {
    this._mouseCoord = coord.clone()
    if (this.editing && this._state === Polygon2DState.DRAW) {
      // Add vertex
      const isFinished = this.addVertex(coord)
      if (isFinished) {
        // Finish adding when it is FINISHED
        this._state = Polygon2DState.FINISHED
        this.editing = false
        if (this.isValid()) {
          this._labelList.addUpdatedLabel(this)
        }
      }
    } else if (this.editing && this._state === Polygon2DState.RESHAPE) {
      // Finish dragging point
      if (this._snapTargetPolyline !== null && this._snapTargetPointIndex !== -1) {
        this.mergeWith(this._snapTargetPolyline, this._snapTargetPointIndex === 0)
      }
      this._state = Polygon2DState.FINISHED
      this.editing = false
      this.clearSnapState()
      this._highlightedHandle = -1
    } else if (this.editing && this._state === Polygon2DState.MOVE) {
      // Finish dragging edges
      this._state = Polygon2DState.FINISHED
      this.editing = false
      this._highlightedHandle = -1
    }
    this._mouseDown = false
    if (!this.isValid() && !this.editing && !this.temporary) {
      this._points = []
      for (const point of this._startingPoints) {
        this._points.push(point.clone())
      }
    }
    this.UpdateLabelShapes()
    console.log("[DEBUG] Polygon2D.onMouseUp completed:", {
      editing: this.editing,
      state: this._state,
      highlightedHandle: this._highlightedHandle,
      snapTargetPolyline: this._snapTargetPolyline ? { index: this._snapTargetPolyline.index } : null,
      snapTargetPointIndex: this._snapTargetPointIndex
    })
    return true
  }

  /**
   * handle keyboard down event
   *
   * @param e pressed key
   */
  public onKeyDown(e: string): boolean {
    this._keyDownMap[e] = true
    if (
      (e === Key.D_UP || e === Key.D_LOW) &&
      this._state === Polygon2DState.DRAW
    ) {
      return this.deleteVertex()
    } else if (e === Key.ENTER) {
      this.finishDrawing()
    }
    return true
  }

  /**
   * handle keyboard up event
   *
   * @param e pressed key
   */
  public onKeyUp(e: string): void {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete this._keyDownMap[e]
  }

  /**
   * to check whether the label is valid
   */
  public isValid(): boolean {
    if (this._mergedOut) {
      return false
    }
    if (this._state !== Polygon2DState.FINISHED) {
      return false
    }
    if (this._closed) {
      const lines: PathPoint2D[][] = []
      let l = 0
      let r = 1
      let maxx = Number.MIN_VALUE
      let minx = Number.MAX_VALUE
      let maxy = Number.MIN_VALUE
      let miny = Number.MAX_VALUE
      for (const item of this._points) {
        maxx = Math.max(maxx, item.x)
        minx = Math.min(minx, item.x)
        maxy = Math.max(maxy, item.y)
        miny = Math.min(miny, item.y)
      }
      if ((maxx - minx) * (maxy - miny) < MIN_SIZE) {
        return false
      }
      while (r < this._points.length) {
        if (this._points[r].type === PathPointType.LINE) {
          lines.push([this._points[l], this._points[r]])
          l = r
        }
        r++
      }
      if (this._state === Polygon2DState.FINISHED) {
        if (this._points[l].type === PathPointType.LINE) {
          lines.push([this._points[l], this._points[0]])
        }
      }
      for (let i = 0; i < lines.length; i++) {
        for (let j = i + 1; j < lines.length; j++) {
          if (
            lines[i][0].x === lines[j][0].x &&
            lines[i][0].y === lines[j][0].y
          ) {
            continue
          }
          if (
            lines[i][0].x === lines[j][1].x &&
            lines[i][0].y === lines[j][1].y
          ) {
            continue
          }
          if (
            lines[i][1].x === lines[j][0].x &&
            lines[i][1].y === lines[j][0].y
          ) {
            continue
          }
          if (
            lines[i][1].x === lines[j][1].x &&
            lines[i][1].y === lines[j][1].y
          ) {
            continue
          }
          if (this.intersect(lines[i], lines[j])) {
            return false
          }
        }
      }
    } else {
      // TODO: check polyline validation
      if (this._points.length <= 1) {
        return false
      }
    }
    return true
  }

  /** Get shape objects for committing to state */
  public shapes(): ShapeType[] {
    if (this._label === null) {
      throw new Error("Uninitialized label")
    }
    /**
     * This is a temporary solution for assigning the correct ID to the shapes
     * We should initialize the shape when the temporary label is created.
     * Also store the shape id properly so that the generated shape state has
     * the right id directly.
     */
    const pathPoints: PathPoint2DType[] = []
    for (const point of this._points) {
      if (point.type === PathPointType.MID) continue
      pathPoints.push(point.shape())
    }
    return pathPoints
  }

  /**
   * to update the shape of polygon
   *
   * @param _shapes
   * @param shapes
   */
  public updateShapes(shapes: ShapeType[]): void {
    if (this._label === null) {
      return
    }
    this._points = []
    for (const shape of shapes) {
      const point = shape as PathPoint2DType
      switch (point.pointType) {
        case PathPointType.LINE: {
          const currPoint = new PathPoint2D(point)
          if (this._points.length !== 0) {
            const prevPoint = this._points[this._points.length - 1]
            if (prevPoint.type === PathPointType.LINE) {
              this._points.push(this.getMidpoint(prevPoint, currPoint))
            }
          }
          this._points.push(currPoint)
          break
        }
        case PathPointType.CURVE: {
          this._points.push(new PathPoint2D(point))
          break
        }
      }
    }

    // The root of a set of linked polygons has no points.
    if (this._points.length === 0) {
      return
    }
    if (this._closed) {
      const tmp = this._points[this._points.length - 1]
      if (tmp.type === PathPointType.LINE) {
        this._points.push(this.getMidpoint(tmp, this._points[0]))
      }
    }
    this._state = Polygon2DState.FINISHED
  }

  /**
   * create new polygon label
   *
   * @param _state
   * @param state
   * @param _start
   */
  protected initTempLabel(state: State, _start: Vector2D): LabelType {
    this.editing = true
    this._state = Polygon2DState.DRAW
    const itemIndex = state.user.select.item
    let sensor = -1
    if (state.user.viewerConfigs[0] !== undefined) {
      sensor = state.user.viewerConfigs[0].sensor
    }
    const labelType = this._closed
      ? LabelTypeName.POLYGON_2D
      : LabelTypeName.POLYLINE_2D
    const label = makeLabel({
      type: labelType,
      item: itemIndex,
      category: [state.user.select.category],
      order: this._order,
      sensors: [sensor]
    })
    // Set color immediately from category so the polygon renders in the
    // correct category color while it is being drawn (before state commit)
    const cats = state.task.config.categories
    const catIdx = state.user.select.category
    const catName =
      catIdx >= 0 && catIdx < cats.length ? cats[catIdx] : undefined
    this._color = getColorByCategory(catIdx, catName)
    this._highlightedHandle = 1
    return label
  }

  /**
   * Update the label with the current set of shapes
   */
  private UpdateLabelShapes(): void {
    if (this._label === null) {
      return
    }
    const vertices = this.getVertices()
    this._label.shapes = vertices.map((v) => v.id)
  }

  /**
   * Get vertices of the polygon
   */
  private getVertices(): PathPoint2D[] {
    const vertices: PathPoint2D[] = []
    for (const point of this._points) {
      if (point.type === PathPointType.MID) continue
      vertices.push(point)
    }
    return vertices
  }

  /**
   * Get the centroid of the drawable polygon
   */
  private getCentroid(): Vector2D {
    const points = this.getVertices()
    let centroid = new Vector2D(0, 0)
    if (this._closed) {
      // Polygon center
      centroid = _.reduce(points, (sum, p) => sum.add(p.vector()), centroid)
      centroid.scale(1 / points.length)
    } else {
      // Put the centroid between the middle two points
      const index = Math.floor((points.length - 1) / 2)
      centroid
        .add(points[index].vector().scale(2))
        .add(points[index + 1].vector())
        .scale(1 / 3)
    }
    return centroid
  }

  /**
   * Move the polygon
   *
   * @param _end
   * @param end
   * @param _limit
   */
  private move(end: Vector2D, _limit: Size2D): void {
    if (this._highlightedHandle !== 0) {
      throw new Error(`not operation move`)
    }
    const delta = end.clone().subtract(this._mouseDownCoord)
    for (let i = 0; i < this._points.length; ++i) {
      this._points[i].x = this._startingPoints[i].x + delta.x
      this._points[i].y = this._startingPoints[i].y + delta.y
    }
  }

  /**
   * add a new vertex to polygon label
   * it will return whether it is FINISHED
   *
   * @param _coord
   * @param _limit
   * @param coord
   */
  private addVertex(coord: Vector2D): boolean {
    let closed = false
    const newPoint = makeDrawablePathPoint2D(
      coord.x,
      coord.y,
      PathPointType.LINE,
      this._label?.id
    )
    if (this._points.length === 0) {
      // First point for the polygon
      this._points.push(newPoint)
    } else if (this._highlightedHandle === 1) {
      // When the polygon is FINISHED, only add a new mid point
      if (this._closed) {
        const point1 = this._points[0]
        const point2 = this._points[this._points.length - 1]
        const midPoint = this.getMidpoint(point1, point2)
        this._points.push(midPoint)
      }
      closed = true
    } else {
      // Add a new vertex and the new mid point on the new edge
      const point2 = this._points[this._points.length - 1]
      const midPoint = this.getMidpoint(point2, newPoint)
      this._points.push(midPoint)
      this._points.push(newPoint)
    }
    return closed
  }

  /**
   * delete one vertex in polygon
   */
  private deleteVertex(): boolean {
    const numPoints = this._points.length
    if (numPoints === 0) return false
    switch (this._state) {
      case Polygon2DState.DRAW: {
        const indexGap = numPoints === 1 ? 1 : 2
        this._points.splice(numPoints - indexGap, indexGap)
        break
      }
      case Polygon2DState.RESHAPE: {
        let numVertices = 0
        for (const point of this._points) {
          if (point.type === PathPointType.LINE) {
            ++numVertices
          }
        }
        const minVertexNumber = this._closed ? 4 : 3
        if (
          numVertices >= minVertexNumber &&
          this._highlightedHandle > 0 &&
          this._points[this._highlightedHandle - 1].type === PathPointType.LINE
        ) {
          const selectedLabelIndex = this._highlightedHandle - 1
          let numPreviousPointsToDelete = 0
          let previousVertexIndex = 0
          for (
            let i = (selectedLabelIndex - 1 + numPoints) % numPoints;
            i >= 0;
            i--
          ) {
            if (this._points[i].type === PathPointType.LINE) {
              previousVertexIndex = i
              break
            }
            numPreviousPointsToDelete++
          }

          let nextVertexIndex = 0
          let numNextPointsToDelete = 0
          for (
            let i = (selectedLabelIndex + 1) % numPoints;
            i < this._points.length;
            i = (i + 1) % numPoints
          ) {
            if (this._points[i].type === PathPointType.LINE) {
              nextVertexIndex = i
              break
            }
            numNextPointsToDelete++
          }

          const prevPoint = this._points[previousVertexIndex]
          const nextPoint = this._points[nextVertexIndex]
          this._points[selectedLabelIndex] = this.getMidpoint(
            prevPoint,
            nextPoint
          )

          if (numNextPointsToDelete === 0) {
            this._points.splice(selectedLabelIndex, 1)
            this._points.splice(
              previousVertexIndex + 1,
              numPreviousPointsToDelete
            )
          } else if (numPreviousPointsToDelete === 0) {
            this._points.splice(selectedLabelIndex + 1, numNextPointsToDelete)
            this._points.splice(selectedLabelIndex, 1)
          } else if (previousVertexIndex > selectedLabelIndex) {
            this._points.splice(
              previousVertexIndex + 1,
              numPreviousPointsToDelete
            )
            this._points.splice(selectedLabelIndex + 1, numNextPointsToDelete)
          } else {
            this._points.splice(selectedLabelIndex + 1, numNextPointsToDelete)
            this._points.splice(
              previousVertexIndex + 1,
              numPreviousPointsToDelete
            )
          }

          while (this._points[0].type !== PathPointType.LINE) {
            const point1 = this._points.shift()
            if (point1 !== null && point1 !== undefined) {
              this._points.push(point1)
            }
          }
        }
      }
    }
    return this._points.length !== 0
  }

  /** Finish the drawing of a polygon or polyline.*/
  private finishDrawing(): void {
    if (this._closed) {
      const point1 = this._points[0]
      const point2 = this._points[this._points.length - 1]
      const midPoint = this.getMidpoint(point1, point2)
      this._points.push(midPoint)
    }
    this._state = Polygon2DState.FINISHED
    this.editing = false
    if (this.isValid()) {
      this._labelList.addUpdatedLabel(this)
    }
  }

  /**
   * reshape the polygon: drag vertex or control points
   *
   * @param _end
   * @param end
   * @param _limit
   */
  private reshape(end: Vector2D, _limit: Size2D): void {
    //
    if (this._highlightedHandle <= 0) {
      throw new Error("No handle is selected for reshape")
    }
    const point = this._points[this._highlightedHandle - 1]
    point.x = end.x
    point.y = end.y

    // Change corresponding midpoint
    if (point.type === PathPointType.LINE) {
      const numPoints = this._points.length
      const selectedLabelIndex = this._highlightedHandle - 1
      const nextPoint =
        this.getNextIndex(selectedLabelIndex) === -1
          ? null
          : this._points[(selectedLabelIndex + 1) % numPoints]
      const prevPoint =
        this.getPreviousIndex(selectedLabelIndex) === -1
          ? null
          : this._points[(selectedLabelIndex + numPoints - 1) % numPoints]

      if (prevPoint !== null && prevPoint.type !== PathPointType.CURVE) {
        const prevVertex =
          this._points[(selectedLabelIndex + numPoints - 2) % numPoints]
        prevPoint.copy(this.getMidpoint(prevVertex, point))
      }
      if (nextPoint !== null && nextPoint.type !== PathPointType.CURVE) {
        const nextVertex = this._points[(selectedLabelIndex + 2) % numPoints]
        nextPoint.copy(this.getMidpoint(point, nextVertex))
      }
    }
  }

  /**
   * return the midpoint of the line
   *
   * @param prev the previous vertex
   * @param next the next vertex
   */
  private getMidpoint(prev: PathPoint2D, next: PathPoint2D): PathPoint2D {
    const mid = prev.vector().add(next.vector()).scale(0.5)
    return makeDrawablePathPoint2D(
      mid.x,
      mid.y,
      PathPointType.MID,
      this._label?.id
    )
  }

  /**
   * return the control points of the bezier curve
   *
   * @param src the source vertex
   * @param dest the next vertex
   */
  private getCurvePoints(src: Vector2D, dest: Vector2D): PathPoint2D[] {
    const first = src
      .clone()
      .scale(2)
      .add(dest)
      .scale(1 / 3)
    const point1 = makeDrawablePathPoint2D(
      first.x,
      first.y,
      PathPointType.CURVE,
      this._label?.id
    )
    const second = dest
      .clone()
      .scale(2)
      .add(src)
      .scale(1 / 3)
    const point2 = makeDrawablePathPoint2D(
      second.x,
      second.y,
      PathPointType.CURVE,
      this._label?.id
    )
    return [point1, point2]
  }

  /**
   * convert a midpoint to a vertex
   */
  private midToVertex(): void {
    const selectedLabelIndex = this._highlightedHandle - 1
    const point = this._points[selectedLabelIndex]
    if (point.type !== PathPointType.MID) {
      throw new Error(`not a midpoint`)
    }
    const prevPoint = this._points[this.getPreviousIndex(selectedLabelIndex)]
    const mid1 = this.getMidpoint(prevPoint, point)
    const mid2 = this.getMidpoint(
      point,
      this._points[this.getNextIndex(selectedLabelIndex)]
    )
    this._points.splice(selectedLabelIndex, 0, mid1)
    this._points.splice(
      this.getNextIndex(this.getNextIndex(selectedLabelIndex)),
      0,
      mid2
    )
    point.type = PathPointType.LINE
    this._highlightedHandle++
  }

  /**
   * convert a line to a curve and vice-versa
   */
  private lineToCurve(): void {
    const selectedLabelIndex = this._highlightedHandle - 1
    const point = this._points[selectedLabelIndex]
    const highlightedHandleIndex = this._highlightedHandle - 1
    switch (point.type) {
      case PathPointType.MID: {
        // From midpoint to curve
        const prevPoint =
          this._points[this.getPreviousIndex(highlightedHandleIndex)]
        const nextPoint =
          this._points[this.getNextIndex(highlightedHandleIndex)]
        const controlPoints = this.getCurvePoints(
          prevPoint.vector(),
          nextPoint.vector()
        )
        this._points[highlightedHandleIndex] = controlPoints[0]
        this._points.splice(highlightedHandleIndex + 1, 0, controlPoints[1])
        break
      }
      case PathPointType.CURVE: {
        // From curve to midpoint
        const newMidPointIndex =
          this._points[highlightedHandleIndex - 1].type === PathPointType.CURVE
            ? this.getPreviousIndex(highlightedHandleIndex)
            : highlightedHandleIndex
        this._points.splice(highlightedHandleIndex, 1)
        this._points[newMidPointIndex] = this.getMidpoint(
          this._points[this.getNextIndex(newMidPointIndex)],
          this._points[this.getPreviousIndex(newMidPointIndex)]
        )
      }
    }
  }

  /**
   * save current points to cache
   */
  private toCache(): void {
    this._startingPoints = []
    for (const point of this._points) {
      this._startingPoints.push(point.clone())
    }
  }

  /**
   * Given three collinear points p, q, r, the function checks if q lies
   * on line segment pr
   *
   * @param p
   * @param q
   * @param r
   */
  private onSegment(p: PathPoint2D, q: PathPoint2D, r: PathPoint2D): boolean {
    if (
      q.x <= Math.max(p.x, r.x) &&
      q.x >= Math.min(p.x, r.x) &&
      q.y <= Math.max(p.y, r.y) &&
      q.y >= Math.min(p.y, r.y)
    ) {
      return true
    }
    return false
  }

  /**
   * To find orientation of ordered triplet
   * The function returns following values
   * 0 -> p, q and r are collinear
   * 1 -> Clockwise
   * 2 -> Counterclockwise
   *
   * @param p
   * @param q
   * @param r
   */
  private orientation(
    p: PathPoint2D,
    q: PathPoint2D,
    r: PathPoint2D
  ): OrientationType {
    const val = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
    switch (true) {
      case val === 0:
        return OrientationType.COLLINEAR
      case val > 0:
        return OrientationType.CLOCKWISE
      default:
        return OrientationType.COUNTERCLOCKWISE
    }
  }

  /**
   * to check whether two line segments intersect
   *
   * @param a
   * @param b
   */
  private intersect(a: PathPoint2D[], b: PathPoint2D[]): boolean {
    const p1 = a[0]
    const q1 = a[1]
    const p2 = b[0]
    const q2 = b[1]
    const length1 =
      (q1.x - p1.x) * (q1.x - p1.x) + (q1.y - p1.y) * (q1.y - p1.y)
    const length2 =
      (q2.x - p2.x) * (q2.x - p2.x) + (q2.y - p2.y) * (q2.y - p2.y)
    if (length1 < 1 || length2 < 1) {
      return false
    }
    const o1 = this.orientation(p1, q1, p2)
    const o2 = this.orientation(p1, q1, q2)
    const o3 = this.orientation(p2, q2, p1)
    const o4 = this.orientation(p2, q2, q1)
    if (o1 !== o2 && o3 !== o4) {
      return true
    }
    if (o1 === OrientationType.COLLINEAR && this.onSegment(p1, p2, q1))
      return true
    if (o2 === OrientationType.COLLINEAR && this.onSegment(p1, q2, q1))
      return true
    if (o3 === OrientationType.COLLINEAR && this.onSegment(p2, p1, q2))
      return true
    if (o4 === OrientationType.COLLINEAR && this.onSegment(p2, q1, q2))
      return true
    return false
  }

  /**
   * Get index of previous point, circular indexing
   *
   * @param index
   */
  private getPreviousIndex(index: number): number {
    if (!this._closed && index === 0) {
      return -1
    } else {
      return (index - 1 + this._points.length) % this._points.length
    }
  }

  /**
   * Get index of previous point, circular indexing
   *
   * @param index
   */
  private getNextIndex(index: number): number {
    if (!this._closed && index === this._points.length - 1) {
      return -1
    } else {
      return (index + 1) % this._points.length
    }
  }
}
