/** @jest-environment node */
import {
  clampLabels,
  clampVertices,
  correctionToDict,
  flaggedCorrections
} from "../../../src/server/annotation_fix/clamp"
import { RoiMask } from "../../../src/server/annotation_fix/mask"
import { LabelExport } from "../../../src/types/export"

/**
 * A 20x10 mask whose left half (x < 10) is inside.
 */
function halfMask(): RoiMask {
  const w = 20
  const h = 10
  const data = new Uint8Array(w * h)
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < 10; x++) {
      data[y * w + x] = 1
    }
  }
  return new RoiMask(w, h, data)
}

test("inside vertices are returned with identical values", () => {
  const [out, corrections] = clampVertices(halfMask(), [
    [1.25, 2.75],
    [9.4, 0]
  ])
  expect(out).toEqual([
    [1.25, 2.75],
    [9.4, 0]
  ])
  expect(corrections).toHaveLength(0)
})

test("outside vertices move to the nearest inside pixel plus inset", () => {
  const [out, corrections] = clampVertices(halfMask(), [[14, 5]], "L1", "lane")
  // nearest inside is (9,5); inset 1.5 along (-1,0) gives (7.5,5)
  expect(out[0][0]).toBeCloseTo(7.5, 9)
  expect(out[0][1]).toBeCloseTo(5, 9)
  expect(corrections).toHaveLength(1)
  expect(corrections[0]).toMatchObject({
    labelId: "L1",
    category: "lane",
    vertexIndex: 0,
    original: [14, 5],
    distance: 5
  })
})

test("inset falls back to the boundary point when it would leave the region", () => {
  // One-pixel-wide column at x = 5.
  const w = 12
  const h = 4
  const data = new Uint8Array(w * h)
  for (let y = 0; y < h; y++) {
    data[y * w + 5] = 1
  }
  const roi = new RoiMask(w, h, data)
  const [out] = clampVertices(roi, [[8, 1]], "", "", 1.5)
  expect(out[0]).toEqual([5, 1])
})

test("clampLabels only rewrites polylines that changed and preserves metadata", () => {
  const untouchedVertices: Array<[number, number]> = [
    [1, 1],
    [2, 2]
  ]
  const labels: LabelExport[] = [
    {
      id: 7,
      category: "lane",
      attributes: {},
      manualShape: true,
      box2d: null,
      box3d: null,
      poly2d: [{ vertices: untouchedVertices, types: "LC", closed: true }]
    },
    {
      id: "moved",
      category: "curb_road_edge",
      attributes: {},
      manualShape: true,
      box2d: null,
      box3d: null,
      poly2d: [
        {
          vertices: [
            [3, 3],
            [70, 3]
          ],
          types: "LL",
          closed: false
        }
      ]
    }
  ]
  const result = clampLabels(halfMask(), labels)
  expect(result.totalVertices).toBe(4)
  expect(result.corrections).toHaveLength(1)
  expect(labels[0].poly2d?.[0].vertices).toBe(untouchedVertices)
  expect(labels[0].poly2d?.[0].closed).toBe(true)
  expect(labels[1].poly2d?.[0].types).toBe("LL")
  expect(labels[1].poly2d?.[0].vertices).toHaveLength(2)
  expect(labels[1].poly2d?.[0].vertices[0]).toEqual([3, 3])
  expect(result.corrections[0].labelId).toBe("moved")
  expect(result.corrections[0].distance).toBe(61)
  expect(flaggedCorrections(result, 50)).toHaveLength(1)
  expect(flaggedCorrections(result, 100)).toHaveLength(0)
})

test("correctionToDict rounds to three decimals", () => {
  expect(
    correctionToDict({
      labelId: "a",
      category: "c",
      vertexIndex: 2,
      original: [1.23456, 2],
      corrected: [3.98765, 4],
      distance: 2.76543
    })
  ).toEqual({
    labelId: "a",
    category: "c",
    vertexIndex: 2,
    original: [1.235, 2],
    corrected: [3.988, 4],
    distance: 2.765
  })
})

describe("curve handles", () => {
  test("a handle out in the padding is left alone", () => {
    // Curve between two inside anchors whose handles sit outside (x >= 10).
    const vertices: Array<[number, number]> = [
      [2, 5],
      [14, 2],
      [14, 8],
      [4, 5]
    ]
    const [out, corrections] = clampVertices(
      halfMask(),
      vertices,
      "",
      "",
      1.5,
      "LCCL"
    )
    expect(out).toEqual(vertices)
    expect(corrections).toHaveLength(0)
  })

  test("an anchor that moves takes its own handle with it", () => {
    // The last anchor is outside: (14,5) -> nearest (9,5) -> inset (7.5,5),
    // an offset of (-6.5, 0). Its handle (index 2) shifts by the same offset;
    // the other anchor's handle (index 1) stays.
    const [out, corrections] = clampVertices(
      halfMask(),
      [
        [2, 5],
        [4, 2],
        [16, 2],
        [14, 5]
      ],
      "",
      "",
      1.5,
      "LCCL"
    )
    expect(out[0]).toEqual([2, 5])
    expect(out[1]).toEqual([4, 2])
    expect(out[2][0]).toBeCloseTo(9.5, 9)
    expect(out[2][1]).toBeCloseTo(2, 9)
    expect(out[3][0]).toBeCloseTo(7.5, 9)
    expect(out[3][1]).toBeCloseTo(5, 9)
    expect(corrections).toHaveLength(1)
    expect(corrections[0].vertexIndex).toBe(3)
  })

  test("handles wrap around the end of a closed shape only", () => {
    // A (index 0) is outside. In a closed ring its handles are index 1 and,
    // wrapping, index 5. In an open line index 5 belongs to B (index 3).
    const vertices: Array<[number, number]> = [
      [14, 5],
      [15, 3],
      [5, 2],
      [2, 5],
      [5, 8],
      [15, 7]
    ]
    const [ring] = clampVertices(
      halfMask(),
      vertices,
      "",
      "",
      1.5,
      "LCCLCC",
      true
    )
    expect(ring[0][0]).toBeCloseTo(7.5, 9)
    expect(ring[1][0]).toBeCloseTo(8.5, 9)
    expect(ring[5][0]).toBeCloseTo(8.5, 9)
    expect(ring[2]).toEqual([5, 2])
    expect(ring[4]).toEqual([5, 8])

    const [open] = clampVertices(
      halfMask(),
      vertices,
      "",
      "",
      1.5,
      "LCCLCC",
      false
    )
    expect(open[1][0]).toBeCloseTo(8.5, 9)
    expect(open[5]).toEqual([15, 7])
  })

  test("clampLabels reads types and closed from the polyline", () => {
    const curve: Array<[number, number]> = [
      [2, 5],
      [14, 2],
      [14, 8],
      [4, 5]
    ]
    const labels: LabelExport[] = [
      {
        id: "curve",
        category: "curb_road_edge",
        attributes: {},
        manualShape: true,
        box2d: null,
        box3d: null,
        poly2d: [{ vertices: curve, types: "LCCL", closed: false }]
      }
    ]
    const result = clampLabels(halfMask(), labels)
    expect(result.corrections).toHaveLength(0)
    expect(labels[0].poly2d?.[0].vertices).toBe(curve)
  })
})
