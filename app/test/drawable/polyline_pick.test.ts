/** @jest-environment node */

import * as action from "../../src/action/common"
import { addPolygon2dLabel } from "../../src/action/polygon2d"
import { configureStore } from "../../src/common/configure_store"
import Session, { dispatch } from "../../src/common/session"
import { PathPointType } from "../../src/const/common"
import { Label2DList, PICK_STROKE_PX } from "../../src/drawable/2d/label2d_list"
import { Polygon2D } from "../../src/drawable/2d/polygon2d"
import { Context2D } from "../../src/drawable/util"
import {
  makeImageViewerConfig,
  makeSimplePathPoint2D
} from "../../src/functional/states"
import { Vector2D } from "../../src/math/vector2d"
import { ImageViewerConfigType } from "../../src/types/state"
import { testJson } from "../test_states/test_image_objects"

/*
 * Node has no Path2D and node-canvas is not built here, so these tests use a
 * small geometric stand-in: straight segments only, butt caps, even-odd
 * interior. That is enough to check the hit-band widths and pick order.
 */
type Segment = [number, number, number, number]

/** Records straight segments and closed rings. */
class FakePath2D {
  public segments: Segment[] = []
  public rings: Array<Array<[number, number]>> = []
  private current: Array<[number, number]> = []

  /**
   * @param x x
   * @param y y
   */
  public moveTo(x: number, y: number): void {
    this.current = [[x, y]]
  }

  /**
   * @param x x
   * @param y y
   */
  public lineTo(x: number, y: number): void {
    const [px, py] = this.current[this.current.length - 1]
    this.segments.push([px, py, x, y])
    this.current.push([x, y])
  }

  /**
   * Approximated by its chord; the tests only use straight lines.
   *
   * @param _c1x unused
   * @param _c1y unused
   * @param _c2x unused
   * @param _c2y unused
   * @param x x
   * @param y y
   */
  public bezierCurveTo(
    _c1x: number,
    _c1y: number,
    _c2x: number,
    _c2y: number,
    x: number,
    y: number
  ): void {
    this.lineTo(x, y)
  }

  /** Close the current ring. */
  public closePath(): void {
    const [sx, sy] = this.current[0]
    this.lineTo(sx, sy)
    this.rings.push(this.current)
  }
}

/**
 * Whether a point is within half a band of a segment, with butt caps.
 *
 * @param s segment
 * @param x point x
 * @param y point y
 * @param half half band width
 */
function nearSegment(s: Segment, x: number, y: number, half: number): boolean {
  const [ax, ay, bx, by] = s
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return false
  const t = ((x - ax) * dx + (y - ay) * dy) / len2
  if (t < 0 || t > 1) return false
  return Math.hypot(ax + t * dx - x, ay + t * dy - y) <= half
}

/** Minimal context: only the members the picker touches. */
function makeContext(): Context2D {
  const context = {
    lineWidth: 1,
    lineCap: "butt",
    lineJoin: "miter",
    save: () => undefined,
    restore: () => undefined,
    setTransform: () => undefined,
    setLineDash: () => undefined,
    isPointInStroke(path: FakePath2D, x: number, y: number): boolean {
      return path.segments.some((s) =>
        nearSegment(s, x, y, context.lineWidth / 2)
      )
    },
    isPointInPath(path: FakePath2D, x: number, y: number): boolean {
      return path.rings.some((ring) => {
        let inside = false
        for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
          const [xi, yi] = ring[i]
          const [xj, yj] = ring[j]
          if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi)
            inside = !inside
        }
        return inside
      })
    }
  }
  return context as unknown as Context2D
}

let originalPath2D: unknown

beforeAll(() => {
  originalPath2D = (global as { Path2D?: unknown }).Path2D
  ;(global as { Path2D?: unknown }).Path2D = FakePath2D
})

afterAll(() => {
  ;(global as { Path2D?: unknown }).Path2D = originalPath2D
})

/**
 * Fresh store and label list with the given shapes, at 1 screen px per image
 * px.
 *
 * @param shapes vertex lists and whether each is closed
 */
function setup(
  shapes: Array<{ points: Array<[number, number]>; closed: boolean }>
): Label2DList {
  Session.label2dList = new Label2DList()
  Session.store = configureStore({})
  dispatch(action.updateState(testJson))
  dispatch(action.initSessionAction())
  dispatch(action.addViewerConfig(1, makeImageViewerConfig(0)))
  Session.subscribe(() => {
    Session.label2dList.updateState(Session.getState())
  })
  dispatch(action.loadItem(0, -1))
  dispatch(action.goToItem(0))
  for (const { points, closed } of shapes) {
    dispatch(
      addPolygon2dLabel(
        0,
        -1,
        [0],
        points.map(([x, y]) => makeSimplePathPoint2D(x, y, PathPointType.LINE)),
        closed
      )
    )
  }
  const list = Session.label2dList
  list.setDisplayScale(1, makeImageViewerConfig(0) as ImageViewerConfigType)
  // Adding a label selects it; picking must work on lines nobody hovered.
  for (const label of list.labelList) {
    expect(label).toBeInstanceOf(Polygon2D)
    label.setSelected(false)
    label.setHighlighted(false)
    expect(label.highlighted).toBe(false)
  }
  return list
}

/**
 * Pick at an image coordinate.
 *
 * @param list label list
 * @param x x
 * @param y y
 * @param multiplier thickness multiplier
 * @param bodiesOnly W/E body-only pick
 * @param minStrokePx minimum band width
 */
function pick(
  list: Label2DList,
  x: number,
  y: number,
  multiplier = 1,
  bodiesOnly = false,
  minStrokePx = PICK_STROKE_PX
): number[] {
  return list.pickPolyline(
    new Vector2D(x, y),
    makeContext(),
    multiplier,
    false,
    bodiesOnly,
    minStrokePx
  )
}

const LINE = {
  points: [
    [100, 100],
    [300, 100]
  ] as Array<[number, number]>,
  closed: false
}

describe("polyline picking", () => {
  test("a thin line keeps the 10 px click band", () => {
    const list = setup([LINE])
    // Visible stroke at multiplier 0.5 is 1.5 px; the band is still 10 px.
    expect(pick(list, 200, 104, 0.5)).toEqual([0, 0])
    expect(pick(list, 200, 106, 0.5)).toEqual([-1, 0])
  })

  test("a thick line uses its visible width when wider than the band", () => {
    const list = setup([LINE])
    // Multiplier 5 draws 15 px, so 7 px off the centre is still on the line.
    expect(pick(list, 200, 107, 5)).toEqual([0, 0])
  })

  test("W/E grabs any line within 20 px", () => {
    const list = setup([LINE])
    expect(pick(list, 200, 118, 1, true)).toEqual([-1, 0])
    expect(pick(list, 200, 118, 1, true, 40)).toEqual([0, 0])
  })

  test("an endpoint of a line that is not hovered can be grabbed", () => {
    const list = setup([LINE])
    // 4 px beyond the end: off the butt-capped body, inside the handle.
    // Handles are 1-based: start 1, segment midpoint 2, end 3.
    expect(pick(list, 304, 100)).toEqual([0, 3])
  })

  test("the hidden midpoint of an unhovered line selects the line body", () => {
    const list = setup([LINE])
    expect(pick(list, 200, 100)).toEqual([0, 0])
    list.labelList[0].setHighlighted(true, 0)
    expect(pick(list, 200, 100)).toEqual([0, 2])
  })

  test("a vertex wins over the body so a press reshapes, not moves", () => {
    const list = setup([LINE])
    expect(pick(list, 101, 100)).toEqual([0, 1])
  })
})

describe("closed polygon picking", () => {
  const SQUARE = {
    points: [
      [0, 0],
      [400, 0],
      [400, 400],
      [0, 400]
    ] as Array<[number, number]>,
    closed: true
  }

  test("clicking inside selects it in selecting mode", () => {
    const list = setup([SQUARE])
    dispatch(action.changeModeToSelecting())
    expect(pick(list, 200, 200)).toEqual([0, 0])
  })

  test("the interior is not a hit while annotating", () => {
    const list = setup([SQUARE])
    dispatch(action.changeModeToAnnotating())
    expect(pick(list, 200, 200)).toEqual([-1, 0])
  })

  test("W/E never grabs a polygon by its interior", () => {
    const list = setup([SQUARE])
    dispatch(action.changeModeToSelecting())
    expect(pick(list, 200, 200, 1, true, 40)).toEqual([-1, 0])
  })

  test("a line inside a polygon stays clickable", () => {
    const list = setup([LINE, SQUARE])
    dispatch(action.changeModeToSelecting())
    expect(pick(list, 200, 103)).toEqual([0, 0])
    expect(pick(list, 200, 300)).toEqual([1, 0])
  })
})

describe("overlapping lines", () => {
  test("the line drawn on top beats a vertex of the line underneath", () => {
    // The lower line (index 0) ends 5 px from a point on the upper line's
    // body. Pressing there must pick the upper line, as the colour-coded
    // control canvas did, not start reshaping the line underneath.
    const LOWER = {
      points: [
        [0, 105],
        [200, 105]
      ] as Array<[number, number]>,
      closed: false
    }
    const list = setup([LOWER, LINE])
    expect(pick(list, 200, 100)).toEqual([1, 0])
  })

  test("a vertex of the top line still wins over its own body", () => {
    const LOWER = {
      points: [
        [0, 105],
        [400, 105]
      ] as Array<[number, number]>,
      closed: false
    }
    const list = setup([LOWER, LINE])
    expect(pick(list, 300, 100)).toEqual([1, 3])
  })
})

describe("syncing lines with the saved state", () => {
  /**
   * Dispatch a view-only change (a pan), which keeps every saved label.
   */
  function pan(): void {
    const config = Session.getState().user
      .viewerConfigs[1] as ImageViewerConfigType
    const panned: ImageViewerConfigType = {
      ...config,
      displayLeft: config.displayLeft + 10
    }
    dispatch(action.changeViewerConfig(1, panned))
  }

  test("an uncommitted midpoint promotion is reset on the next update", () => {
    const list = setup([LINE])
    const label = list.labelList[0] as Polygon2D
    expect(label.snapPoints).toHaveLength(3)
    // Press the segment midpoint (handle 2) and release without moving and
    // without committing: the midpoint has become a vertex locally only.
    label.setSelected(true)
    label.setHighlighted(true, 2)
    label.onMouseDown(new Vector2D(200, 100), 2)
    label.onMouseUp(new Vector2D(200, 100))
    expect(label.snapPoints).toHaveLength(5)
    expect(label.hasLocalGeometryChanges()).toBe(true)

    pan()

    const synced = list.labelList[0] as Polygon2D
    expect(synced.snapPoints).toHaveLength(3)
    expect(synced.hasLocalGeometryChanges()).toBe(false)
  })

  test("an unchanged line is not rebuilt by a pan", () => {
    const list = setup([LINE])
    const points = (list.labelList[0] as Polygon2D).snapPoints
    pan()
    expect((list.labelList[0] as Polygon2D).snapPoints).toBe(points)
  })

  test("cached bounds and path follow a point moved in place", () => {
    const list = setup([LINE])
    const label = list.labelList[0] as Polygon2D
    expect(label.bounds()).toEqual([100, 100, 200, 0])
    const path = label.getPath()
    // Move a vertex without going through any method that invalidates.
    const end = label.snapPoints[2]
    end.x = 350
    expect(label.bounds()).toEqual([100, 100, 250, 0])
    expect(label.getPath()).not.toBe(path)
    expect(pick(list, 340, 100)).toEqual([0, 0])
  })
})
