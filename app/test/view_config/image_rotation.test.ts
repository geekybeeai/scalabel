import { rotatePoint, unrotatePoint } from "../../src/view_config/image"
import { Vector2D } from "../../src/math/vector2d"

const W = 200
const H = 100
const POINTS: Array<[number, number]> = [
  [0, 0],
  [W, 0],
  [0, H],
  [W, H],
  [50, 30]
]

describe("image rotation helpers", () => {
  test("unrotatePoint inverts rotatePoint for every angle", () => {
    for (const rotation of [0, 90, 180, 270]) {
      for (const [x, y] of POINTS) {
        const back = unrotatePoint(
          rotatePoint(new Vector2D(x, y), rotation, W, H),
          rotation,
          W,
          H
        )
        expect(back.x).toBeCloseTo(x)
        expect(back.y).toBeCloseTo(y)
      }
    }
  })

  test("90 CW maps original corners into the H x W display frame", () => {
    // (0,0) -> (H,0); (0,H) -> (0,0); (W,H) -> (0,W)
    expect(rotatePoint(new Vector2D(0, 0), 90, W, H)).toEqual(
      new Vector2D(H, 0)
    )
    expect(rotatePoint(new Vector2D(0, H), 90, W, H)).toEqual(
      new Vector2D(0, 0)
    )
    expect(rotatePoint(new Vector2D(W, H), 90, W, H)).toEqual(
      new Vector2D(0, W)
    )
  })

  test("rotation 0 is identity", () => {
    expect(rotatePoint(new Vector2D(12, 34), 0, W, H)).toEqual(
      new Vector2D(12, 34)
    )
  })
})
