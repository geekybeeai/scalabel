/** @jest-environment node */
import * as fs from "fs"
import * as os from "os"
import * as path from "path"

import {
  computeRoiMask,
  RoiMask,
  roundHalfEven
} from "../../../src/server/annotation_fix/mask"

// eslint-disable-next-line @typescript-eslint/no-var-requires
const sharp = require("sharp") as typeof import("sharp").default

/** A grey rectangle to paint onto the black canvas. */
interface Rect {
  /** left edge */
  left: number
  /** top edge */
  top: number
  /** width */
  width: number
  /** height */
  height: number
  /** grey level */
  value: number
}

/**
 * Write a PNG: black canvas with the given rectangles painted in order.
 *
 * @param file output path
 * @param width canvas width
 * @param height canvas height
 * @param rects rectangles to paint
 */
async function writePng(
  file: string,
  width: number,
  height: number,
  rects: Rect[]
): Promise<void> {
  await sharp({
    create: { width, height, channels: 3, background: { r: 0, g: 0, b: 0 } }
  })
    .composite(
      rects.map((r) => ({
        input: {
          create: {
            width: r.width,
            height: r.height,
            channels: 3,
            background: { r: r.value, g: r.value, b: r.value }
          }
        },
        left: r.left,
        top: r.top
      }))
    )
    .png()
    .toFile(file)
}

/**
 * Build a mask directly from an ASCII picture ('#' inside).
 *
 * @param rows the picture
 */
function maskFromRows(rows: string[]): RoiMask {
  const height = rows.length
  const width = rows[0].length
  const data = new Uint8Array(width * height)
  rows.forEach((row, y) => {
    for (let x = 0; x < width; x++) {
      data[y * width + x] = row[x] === "#" ? 1 : 0
    }
  })
  return new RoiMask(width, height, data)
}

let dir: string
beforeAll(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "roi-mask-"))
})
afterAll(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

test("roundHalfEven matches Python round()", () => {
  expect(roundHalfEven(2.5)).toBe(2)
  expect(roundHalfEven(3.5)).toBe(4)
  expect(roundHalfEven(2.4)).toBe(2)
  expect(roundHalfEven(2.6)).toBe(3)
  expect(roundHalfEven(-0.5)).toBe(0)
  expect(roundHalfEven(-1.5)).toBe(-2)
})

describe("computeRoiMask", () => {
  test("thresholds max(R,G,B), fills holes and keeps the largest component", async () => {
    const file = path.join(dir, "blob.png")
    await writePng(file, 12, 10, [
      { left: 2, top: 2, width: 6, height: 6, value: 200 }, // blob
      { left: 4, top: 4, width: 2, height: 2, value: 0 }, // hole inside blob
      { left: 10, top: 0, width: 1, height: 1, value: 200 }, // speck
      { left: 0, top: 9, width: 3, height: 1, value: 5 } // below threshold
    ])
    const roi = await computeRoiMask(file)
    expect(roi.width).toBe(12)
    expect(roi.height).toBe(10)
    expect(roi.contains(4, 4)).toBe(true) // hole filled
    expect(roi.contains(10, 0)).toBe(false) // speck dropped
    expect(roi.contains(1, 9)).toBe(false) // dark pixel is padding
    expect(roi.contains(2, 2)).toBe(true)
    expect(roi.contains(7, 7)).toBe(true)
    expect(roi.contains(8, 8)).toBe(false)
    expect(roi.coverage).toBeCloseTo(36 / 120, 6)
  })

  test("a single channel above threshold counts as imagery", async () => {
    const file = path.join(dir, "blue.png")
    await sharp({
      create: {
        width: 4,
        height: 4,
        channels: 3,
        background: { r: 0, g: 0, b: 0 }
      }
    })
      .composite([
        {
          input: {
            create: {
              width: 2,
              height: 2,
              channels: 3,
              background: { r: 0, g: 0, b: 40 }
            }
          },
          left: 1,
          top: 1
        }
      ])
      .png()
      .toFile(file)
    const roi = await computeRoiMask(file)
    expect(roi.contains(1, 1)).toBe(true)
    expect(roi.contains(0, 0)).toBe(false)
  })
})

describe("RoiMask geometry", () => {
  const roi = maskFromRows(["......", ".###..", ".###..", ".###..", "......"])

  test("contains rounds half-to-even and rejects off-canvas points", () => {
    expect(roi.contains(1, 1)).toBe(true)
    expect(roi.contains(0.5, 1)).toBe(false) // rounds to 0
    expect(roi.contains(3.5, 1)).toBe(false) // half-even rounds to 4, outside
    expect(roi.contains(2.5, 1)).toBe(true) // half-even rounds to 2, inside
    expect(roi.contains(-1, 1)).toBe(false)
    expect(roi.contains(6, 1)).toBe(false)
    expect(roi.contains(1, 5)).toBe(false)
  })

  test("nearestInside returns the point itself when inside", () => {
    expect(roi.nearestInside(2, 2)).toEqual([2, 2, 0])
  })

  test("nearestInside finds the closest pixel with the true distance", () => {
    const [nx, ny, d] = roi.nearestInside(5, 2)
    expect([nx, ny]).toEqual([3, 2])
    expect(d).toBeCloseTo(2, 9)
    const [ox, oy, od] = roi.nearestInside(0.25, 0)
    expect([ox, oy]).toEqual([1, 1])
    expect(od).toBeCloseTo(Math.hypot(0.75, 1), 9)
  })

  test("nearestInside handles points off the canvas", () => {
    const [nx, ny, d] = roi.nearestInside(-3, 2)
    expect([nx, ny]).toEqual([1, 2])
    expect(d).toBeCloseTo(4, 9)
  })

  test("nearestInside keeps searching past the first ring hit", () => {
    // From (0,0): (3,3) sits on ring 3 at distance 4.243, but (4,0) on ring 4
    // is closer at distance 4. The search must not stop at the first ring.
    const tricky = maskFromRows(["....#", ".....", ".....", "...#."])
    const [nx, ny, d] = tricky.nearestInside(0, 0)
    expect([nx, ny]).toEqual([4, 0])
    expect(d).toBeCloseTo(4, 9)
  })
})
