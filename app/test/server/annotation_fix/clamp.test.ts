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
