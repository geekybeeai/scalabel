import {
  findLassoHits,
  LassoLine,
  lineHitsLasso,
  pointInPolygon,
  Pt,
  segmentsIntersect
} from "../../src/drawable/2d/freeform_select_geometry"

// A 10x10 square lasso.
const SQUARE: Pt[] = [
  { x: 0, y: 0 },
  { x: 10, y: 0 },
  { x: 10, y: 10 },
  { x: 0, y: 10 }
]

describe("freeform_select_geometry", () => {
  describe("pointInPolygon", () => {
    test("inside", () => {
      expect(pointInPolygon({ x: 5, y: 5 }, SQUARE)).toBe(true)
    })
    test("outside", () => {
      expect(pointInPolygon({ x: 15, y: 5 }, SQUARE)).toBe(false)
    })
  })

  describe("segmentsIntersect", () => {
    test("crossing", () => {
      expect(
        segmentsIntersect(
          { x: 0, y: 0 },
          { x: 10, y: 10 },
          { x: 0, y: 10 },
          { x: 10, y: 0 }
        )
      ).toBe(true)
    })
    test("disjoint", () => {
      expect(
        segmentsIntersect(
          { x: 0, y: 0 },
          { x: 1, y: 0 },
          { x: 0, y: 5 },
          { x: 1, y: 5 }
        )
      ).toBe(false)
    })
  })

  describe("lineHitsLasso", () => {
    test("fully enclosed", () => {
      const line: LassoLine = {
        id: "a",
        pts: [
          { x: 2, y: 2 },
          { x: 8, y: 8 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("crossing with both endpoints outside", () => {
      const line: LassoLine = {
        id: "b",
        pts: [
          { x: -5, y: 5 },
          { x: 15, y: 5 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("fully outside", () => {
      const line: LassoLine = {
        id: "c",
        pts: [
          { x: 20, y: 20 },
          { x: 30, y: 30 }
        ],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(false)
    })
    test("single-point line inside", () => {
      const line: LassoLine = {
        id: "d",
        pts: [{ x: 5, y: 5 }],
        closed: false
      }
      expect(lineHitsLasso(line, SQUARE)).toBe(true)
    })
    test("degenerate lasso never hits", () => {
      const line: LassoLine = {
        id: "e",
        pts: [{ x: 5, y: 5 }],
        closed: false
      }
      expect(
        lineHitsLasso(line, [
          { x: 0, y: 0 },
          { x: 10, y: 0 }
        ])
      ).toBe(false)
    })
  })

  describe("findLassoHits", () => {
    test("returns only ids that hit", () => {
      const lines: LassoLine[] = [
        { id: "in", pts: [{ x: 5, y: 5 }], closed: false },
        { id: "out", pts: [{ x: 99, y: 99 }], closed: false }
      ]
      expect(findLassoHits(lines, SQUARE)).toEqual(["in"])
    })
  })
})
