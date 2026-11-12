import { makeState } from "../../src/functional/states"
import { Size2D } from "../../src/math/size2d"
import { Vector2D } from "../../src/math/vector2d"
import { Context2D } from "../../src/drawable/util"
import { Box2D } from "../../src/drawable/2d/box2d"
import { DrawMode } from "../../src/drawable/2d/label2d"
import { Label2DList } from "../../src/drawable/2d/label2d_list"

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
    strokeRect: jest.fn(),
    rect: jest.fn(),
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
    fillStyle: ""
  } as unknown as Context2D
  return { context, lineWidths, radii }
}

test("renders box borders and handles in compensated backing pixels", () => {
  const labelList = new Label2DList()
  const box = new Box2D(labelList)
  box.initTemp(makeState(), new Vector2D(10, 10))
  box.onMouseDown(new Vector2D(10, 10), 5)
  box.onMouseMove(new Vector2D(100, 100), new Size2D(1000, 1000), -1, 5)
  box.onMouseUp(new Vector2D(100, 100))

  const { context, lineWidths, radii } = makeRecordingContext()
  box.draw(
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

  expect(lineWidths).toContain(1)
  expect(radii).toContain(2)
  expect(radii).toContain(3)
})
