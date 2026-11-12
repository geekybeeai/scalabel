import { Label2DList } from "../../src/drawable/2d/label2d_list"
import { Polygon2D } from "../../src/drawable/2d/polygon2d"
import { Context2D } from "../../src/drawable/util"

test("forwards the actual effective canvas ratio to every drawing pass", () => {
  const labelList = new Label2DList()
  const polygon = new Polygon2D(labelList, false)
  const draw = jest.spyOn(polygon, "draw").mockImplementation(() => {})
  labelList.labelList.push(polygon)
  const labelContext = Object.create(null) as Context2D
  const controlContext = Object.create(null) as Context2D

  labelList.redraw(
    labelContext,
    controlContext,
    1,
    false,
    false,
    undefined,
    4,
    undefined,
    undefined,
    undefined,
    true,
    1,
    false,
    0.5
  )

  const calls = draw.mock.calls as unknown as unknown[][]
  expect(draw).toHaveBeenCalledTimes(2)
  expect(calls[0][9]).toBe(0.5)
  expect(calls[1][9]).toBe(0.5)
})
