import { makeState } from "../../src/functional/states"
import { Size2D } from "../../src/math/size2d"
import { Vector2D } from "../../src/math/vector2d"
import { Context2D } from "../../src/drawable/util"
import { DrawMode } from "../../src/drawable/2d/label2d"
import { Label2DList } from "../../src/drawable/2d/label2d_list"
import { Polygon2D } from "../../src/drawable/2d/polygon2d"

/** Build the smallest canvas double that records visual style values. */
function makeRecordingContext(): {
  context: Context2D
  lineWidths: number[]
  radii: number[]
} {
  const lineWidths: number[] = []
  const radii: number[] = []
  let lineWidth = 1
  const context = {
    save: jest.fn(),
    restore: jest.fn(),
    beginPath: jest.fn(),
    closePath: jest.fn(),
    moveTo: jest.fn(),
    lineTo: jest.fn(),
    bezierCurveTo: jest.fn(),
    stroke: jest.fn(),
    fill: jest.fn(),
    setLineDash: jest.fn(),
    arc: jest.fn((_x: number, _y: number, radius: number) => {
      radii.push(radius)
    }),
    /** Return the latest assigned line width. */
    get lineWidth(): number {
      return lineWidth
    },
    /** Record a line width assignment. */
    set lineWidth(value: number) {
      lineWidth = value
      lineWidths.push(value)
    },
    strokeStyle: "",
    fillStyle: "",
    lineDashOffset: 0
  } as unknown as Context2D
  return { context, lineWidths, radii }
}

test("renders polyline edges and vertices in compensated backing pixels", () => {
  const labelList = new Label2DList()
  const polygon = new Polygon2D(labelList, false)
  polygon.initTemp(makeState(), new Vector2D(10, 10))
  polygon.onMouseUp(new Vector2D(10, 10))
  polygon.onMouseMove(new Vector2D(100, 10), new Size2D(1000, 1000), -1, 0)
  polygon.onMouseUp(new Vector2D(100, 10))

  const { context, lineWidths, radii } = makeRecordingContext()
  polygon.draw(
    context,
    1,
    DrawMode.VIEW,
    false,
    true,
    undefined,
    4,
    1,
    false,
    0.5
  )

  expect(lineWidths).toContain(2)
  expect(lineWidths).toContain(0.75)
  expect(radii).toContain(2)
})

test("keeps visible polyline edges readable at intermediate and high zoom", () => {
  const labelList = new Label2DList()
  const polygon = new Polygon2D(labelList, false)
  polygon.initTemp(makeState(), new Vector2D(10, 10))
  polygon.onMouseUp(new Vector2D(10, 10))
  polygon.onMouseMove(new Vector2D(100, 10), new Size2D(1000, 1000), -1, 0)
  polygon.onMouseUp(new Vector2D(100, 10))

  const intermediate = makeRecordingContext()
  polygon.draw(
    intermediate.context,
    1,
    DrawMode.VIEW,
    false,
    true,
    undefined,
    4,
    1,
    false,
    1
  )

  const high = makeRecordingContext()
  polygon.draw(
    high.context,
    1,
    DrawMode.VIEW,
    false,
    true,
    undefined,
    16,
    1,
    false,
    1
  )

  expect(intermediate.lineWidths).toContain(2)
  expect(high.lineWidths).toContain(1)
})
