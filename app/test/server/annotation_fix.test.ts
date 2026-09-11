import * as fs from "fs-extra"
import * as os from "os"
import * as path from "path"
import * as zlib from "zlib"

import { connectLabels } from "../../src/server/annotation_fix/autoconnect"
import { clampLabels } from "../../src/server/annotation_fix/clamp"
import {
  makeOptions,
  processDocument
} from "../../src/server/annotation_fix/core"
import { correctAnnotations } from "../../src/server/annotation_fix/index"
import { RoiMask, computeRoiMask } from "../../src/server/annotation_fix/mask"
import { readPngMask } from "../../src/server/annotation_fix/png"
import { FrameLike, LabelLike } from "../../src/server/annotation_fix/types"

/** Scratch directory holding the PNGs these tests synthesise. */
let tmpDir: string

beforeAll(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "annotation-fix-"))
})

afterAll(() => {
  fs.removeSync(tmpDir)
})

/** CRC table for building valid PNG chunks. */
const CRC_TABLE = (() => {
  const table = new Int32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) {
      c = (c & 1) !== 0 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    }
    table[n] = c
  }
  return table
})()

/**
 * CRC32 over a buffer, as PNG chunks require.
 *
 * @param buffer the bytes to checksum
 */
function crc32(buffer: Buffer): number {
  let c = 0xffffffff
  for (let i = 0; i < buffer.length; i++) {
    c = CRC_TABLE[(c ^ buffer[i]) & 0xff] ^ (c >>> 8)
  }
  return (c ^ 0xffffffff) >>> 0
}

/**
 * Build one PNG chunk.
 *
 * @param type the four-character chunk type
 * @param data the chunk payload
 */
function chunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length, 0)
  const typed = Buffer.concat([Buffer.from(type, "ascii"), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(typed), 0)
  return Buffer.concat([length, typed, crc])
}

/**
 * Write an 8-bit RGB PNG whose pixels come from a callback.
 *
 * Every row uses filter type 4 (Paeth), the hardest one to reconstruct, so the
 * decoder's filter handling is exercised rather than bypassed.
 *
 * @param name file name inside the scratch directory
 * @param width image width
 * @param height image height
 * @param pixel returns the [r, g, b] of one pixel
 */
function writePng(
  name: string,
  width: number,
  height: number,
  pixel: (x: number, y: number) => [number, number, number]
): string {
  const rowBytes = width * 3
  const raws: Buffer[] = []
  for (let y = 0; y < height; y++) {
    const raw = Buffer.alloc(rowBytes)
    for (let x = 0; x < width; x++) {
      const [r, g, b] = pixel(x, y)
      raw[x * 3] = r
      raw[x * 3 + 1] = g
      raw[x * 3 + 2] = b
    }
    raws.push(raw)
  }

  /**
   * Paeth predictor.
   *
   * @param a left byte
   * @param b byte above
   * @param c byte above-left
   */
  const paeth = (a: number, b: number, c: number): number => {
    const p = a + b - c
    const pa = Math.abs(p - a)
    const pb = Math.abs(p - b)
    const pc = Math.abs(p - c)
    if (pa <= pb && pa <= pc) {
      return a
    }
    return pb <= pc ? b : c
  }

  const filtered: Buffer[] = []
  let previous = Buffer.alloc(rowBytes)
  for (const raw of raws) {
    const row = Buffer.alloc(rowBytes)
    for (let i = 0; i < rowBytes; i++) {
      const left = i >= 3 ? raw[i - 3] : 0
      const up = previous[i]
      const upLeft = i >= 3 ? previous[i - 3] : 0
      row[i] = (raw[i] - paeth(left, up, upLeft)) & 0xff
    }
    filtered.push(Buffer.concat([Buffer.from([4]), row]))
    previous = raw
  }

  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8
  ihdr[9] = 2
  const file = Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(filtered))),
    chunk("IEND", Buffer.alloc(0))
  ])

  const target = path.join(tmpDir, name)
  fs.writeFileSync(target, file)
  return target
}

/**
 * Build one open polyline label.
 *
 * @param id the label id
 * @param category the label category
 * @param vertices the polyline's vertices
 * @param types per-vertex curve flags
 */
function line(
  id: string,
  category: string,
  vertices: number[][],
  types?: string
): LabelLike {
  return {
    id,
    category,
    poly2d: [
      {
        vertices,
        types: types ?? "L".repeat(vertices.length),
        closed: false
      }
    ]
  }
}

describe("png reader", () => {
  test("reads dimensions and thresholds the brightest channel", async () => {
    // Left half black padding, right half dark blue. Blue is exactly the case
    // luminance would wrongly discard.
    const file = writePng("half.png", 10, 4, (x) =>
      x < 5 ? [0, 0, 0] : [0, 0, 40]
    )
    const image = await readPngMask(file, 10)
    expect(image.width).toBe(10)
    expect(image.height).toBe(4)
    for (let y = 0; y < 4; y++) {
      for (let x = 0; x < 10; x++) {
        expect(image.mask[y * 10 + x]).toBe(x < 5 ? 0 : 1)
      }
    }
  })

  test("rejects a non-PNG file", async () => {
    const file = path.join(tmpDir, "not.png")
    fs.writeFileSync(file, Buffer.from("definitely not a png"))
    await expect(readPngMask(file, 10)).rejects.toThrow("not a PNG")
  })
})

describe("roi mask", () => {
  test("fills interior holes and drops stray specks", async () => {
    // A bright 12x12 block with a dark 2x2 hole punched in it, plus a lone
    // bright speck far away in the padding.
    const file = writePng("holes.png", 24, 16, (x, y) => {
      if (x >= 2 && x < 14 && y >= 2 && y < 14) {
        const hole = x >= 6 && x < 8 && y >= 6 && y < 8
        return hole ? [0, 0, 0] : [200, 200, 200]
      }
      return x === 22 && y === 1 ? [200, 200, 200] : [0, 0, 0]
    })

    const roi = await computeRoiMask(file, 10)
    // The hole is filled: it is enclosed, so it counts as inside.
    expect(roi.contains(6, 6)).toBe(true)
    expect(roi.contains(3, 3)).toBe(true)
    // The speck is not part of the largest component and is dropped.
    expect(roi.contains(22, 1)).toBe(false)
    // Coverage is the 12x12 block over the 24x16 canvas.
    expect(roi.coverage).toBeCloseTo(144 / 384, 5)
  })

  test("treats points beyond the canvas as outside", async () => {
    const file = writePng("small.png", 4, 4, () => [200, 200, 200])
    const roi = await computeRoiMask(file, 10)
    expect(roi.contains(0, 0)).toBe(true)
    expect(roi.contains(-1, 0)).toBe(false)
    expect(roi.contains(4, 0)).toBe(false)
  })
})

describe("nearestInside", () => {
  /**
   * A mask with a single inside pixel, for exact-distance checks.
   *
   * @param width canvas width
   * @param height canvas height
   * @param px the inside pixel's x
   * @param py the inside pixel's y
   */
  function singlePixelMask(
    width: number,
    height: number,
    px: number,
    py: number
  ): RoiMask {
    const mask = new Uint8Array(width * height)
    mask[py * width + px] = 1
    return new RoiMask(width, height, mask)
  }

  test("returns the point itself when already inside", () => {
    const roi = singlePixelMask(20, 20, 5, 5)
    const nearest = roi.nearestInside(5, 5)
    expect(nearest).not.toBeNull()
    expect(nearest?.distance).toBe(0)
  })

  test("finds the exact nearest pixel, not merely a near one", () => {
    const roi = singlePixelMask(64, 64, 40, 12)
    const nearest = roi.nearestInside(3, 7)
    expect(nearest?.nx).toBe(40)
    expect(nearest?.ny).toBe(12)
    expect(nearest?.distance).toBeCloseTo(Math.hypot(40 - 3, 12 - 7), 9)
  })

  test("handles a point far outside the canvas", () => {
    // The vertex is thousands of pixels below the image. Ranking candidates by
    // distance from the clamped point keeps the ring search bounded; ranking by
    // distance from the true point would scan out to the full 11000-px radius.
    const roi = singlePixelMask(200, 100, 120, 60)
    const nearest = roi.nearestInside(100, 9000)
    expect(nearest?.nx).toBe(120)
    expect(nearest?.ny).toBe(60)
    // Distance is still reported from the true, unrounded point.
    expect(nearest?.distance).toBeCloseTo(Math.hypot(120 - 100, 60 - 9000), 6)
  })

  test("picks the pixel nearest the clamped point, as the transform did", () => {
    // Two candidates. A is nearer the clamped query pixel; B is nearer the
    // unrounded point once it is dragged off-canvas. SciPy indexes the
    // transform by the clamped pixel, so A must win.
    const mask = new Uint8Array(100 * 100)
    mask[50 * 100 + 10] = 1
    mask[90 * 100 + 60] = 1
    const roi = new RoiMask(100, 100, mask)
    const nearest = roi.nearestInside(12, 50)
    expect(nearest?.nx).toBe(10)
    expect(nearest?.ny).toBe(50)
  })

  test("agrees with brute force across many queries", () => {
    // A ragged mask, so the nearest pixel is genuinely non-obvious.
    const width = 48
    const height = 48
    const mask = new Uint8Array(width * height)
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if ((x * 7 + y * 13) % 37 === 0) {
          mask[y * width + x] = 1
        }
      }
    }
    const roi = new RoiMask(width, height, mask)

    for (let qy = 0; qy < height; qy += 5) {
      for (let qx = 0; qx < width; qx += 5) {
        if (roi.contains(qx, qy)) {
          continue
        }
        let bruteBest = Infinity
        for (let y = 0; y < height; y++) {
          for (let x = 0; x < width; x++) {
            if (mask[y * width + x] === 1) {
              bruteBest = Math.min(bruteBest, Math.hypot(x - qx, y - qy))
            }
          }
        }
        const nearest = roi.nearestInside(qx, qy)
        expect(nearest).not.toBeNull()
        expect(nearest?.distance).toBeCloseTo(bruteBest, 9)
      }
    }
  })

  test("returns null when nothing is inside", () => {
    const roi = new RoiMask(8, 8, new Uint8Array(64))
    expect(roi.nearestInside(3, 3)).toBeNull()
  })
})

describe("clamp", () => {
  test("moves outside vertices in and leaves inside ones untouched", async () => {
    // Imagery occupies x in [0, 10); everything right of that is padding.
    const file = writePng("strip.png", 20, 8, (x) =>
      x < 10 ? [200, 200, 200] : [0, 0, 0]
    )
    const roi = await computeRoiMask(file, 10)

    const labels = [
      line("a", "curb_road_edge", [
        [2, 3],
        [17, 3]
      ])
    ]
    const result = clampLabels(roi, labels)

    expect(result.totalVertices).toBe(2)
    expect(result.corrections.length).toBe(1)

    const vertices = (labels[0].poly2d ?? [])[0].vertices as number[][]
    // The inside vertex keeps its exact original value.
    expect(vertices[0]).toEqual([2, 3])
    // The outside one lands inside, short of where it was.
    expect(roi.contains(vertices[1][0], vertices[1][1])).toBe(true)
    expect(vertices[1][0]).toBeLessThan(17)
  })

  test("preserves vertex count, types and closed state", async () => {
    const file = writePng("strip2.png", 20, 8, (x) =>
      x < 10 ? [200, 200, 200] : [0, 0, 0]
    )
    const roi = await computeRoiMask(file, 10)

    const labels = [
      line(
        "a",
        "curb_road_edge",
        [
          [1, 1],
          [18, 2],
          [3, 3]
        ],
        "LCL"
      )
    ]
    clampLabels(roi, labels)

    const poly = (labels[0].poly2d ?? [])[0]
    expect((poly.vertices as number[][]).length).toBe(3)
    expect(poly.types).toBe("LCL")
    expect(poly.closed).toBe(false)
    expect(labels[0].id).toBe("a")
    expect(labels[0].category).toBe("curb_road_edge")
  })
})

describe("autoconnect", () => {
  test("merges two same-category lines that nearly touch", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [103, 0],
        [200, 0]
      ])
    ]
    const { labels: survivors, result } = connectLabels(labels)

    expect(survivors.length).toBe(1)
    expect(result.connections.length).toBe(1)
    expect(result.connections[0].keptId).toBe("a")
    expect(result.connections[0].absorbedId).toBe("b")
    // The duplicate junction vertex is dropped: 2 + 2 - 1.
    expect((survivors[0].poly2d ?? [])[0].vertices).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
  })

  test("refuses unrelated categories but allows the road-edge pair", () => {
    const unrelated = [
      line("a", "white_paint", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "yellow_paint", [
        [101, 0],
        [200, 0]
      ])
    ]
    expect(connectLabels(unrelated).labels.length).toBe(2)

    const roadEdge = [
      line("a", "curb_road_edge", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "without_curb_road_edge", [
        [101, 0],
        [200, 0]
      ])
    ]
    expect(connectLabels(roadEdge).labels.length).toBe(1)
  })

  test("leaves lines further apart than the tolerance alone", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [130, 0],
        [200, 0]
      ])
    ]
    expect(connectLabels(labels).labels.length).toBe(2)
  })

  test("never merges closed rings or multi-polygon labels", () => {
    const ring: LabelLike = {
      id: "a",
      category: "lane",
      poly2d: [
        {
          vertices: [
            [0, 0],
            [10, 0],
            [10, 10]
          ],
          types: "LLL",
          closed: true
        }
      ]
    }
    const compound: LabelLike = {
      id: "b",
      category: "lane",
      poly2d: [
        {
          vertices: [
            [0, 0],
            [10, 0]
          ],
          types: "LL",
          closed: false
        },
        {
          vertices: [
            [50, 0],
            [60, 0]
          ],
          types: "LL",
          closed: false
        }
      ]
    }
    expect(connectLabels([ring, compound]).labels.length).toBe(2)
  })
})

describe("autoconnect merging", () => {
  test("splices a chain of three across passes", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [101, 0],
        [200, 0]
      ]),
      line("c", "lane", [
        [201, 0],
        [300, 0]
      ])
    ]
    const { labels: survivors, result } = connectLabels(labels)
    expect(survivors.length).toBe(1)
    expect(result.connections.length).toBe(2)
    expect((survivors[0].poly2d ?? [])[0].vertices).toEqual([
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])
  })

  test("consumes each endpoint once when three lines converge", () => {
    // Three endpoints meet at the same point; only one pair may claim it.
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [101, 0],
        [200, 0]
      ]),
      line("c", "lane", [
        [101, 1],
        [100, 80]
      ])
    ]
    const { labels: survivors } = connectLabels(labels)
    expect(survivors.length).toBe(2)
  })

  test("keeps curve flags aligned with their vertices when reversing", () => {
    // A.start meets B.start, so A is reversed; its flags must reverse with it.
    const labels = [
      line(
        "a",
        "lane",
        [
          [100, 0],
          [150, 0],
          [200, 0]
        ],
        "ABC"
      ),
      line(
        "b",
        "lane",
        [
          [101, 0],
          [50, 0],
          [0, 0]
        ],
        "XYZ"
      )
    ]
    const { labels: survivors } = connectLabels(labels)
    const poly = (survivors[0].poly2d ?? [])[0]
    expect((poly.vertices as number[][]).length).toBe(5)
    expect(poly.types).toBe("CBAYZ")
    expect((poly.types as string).length).toBe(
      (poly.vertices as number[][]).length
    )
  })

  test("min_angle rejects a sharp fork it would otherwise merge", () => {
    // Two lines meeting at a right angle rather than continuing through.
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [101, 0],
        [101, 100]
      ])
    ]
    expect(connectLabels(labels, 15, 0).labels.length).toBe(1)
    expect(connectLabels(labels, 15, 150).labels.length).toBe(2)
  })
})

describe("processDocument", () => {
  test("deep-copies, so the caller's document is untouched", async () => {
    const frames: FrameLike[] = [
      {
        name: "missing.png",
        labels: [
          line("a", "lane", [
            [0, 0],
            [100, 0]
          ])
        ]
      }
    ]
    const before = JSON.stringify(frames)
    await processDocument(frames, makeOptions({ imageRoot: tmpDir }))
    expect(JSON.stringify(frames)).toBe(before)
  })

  test("returns a bare list for a bare list and a document for a document", async () => {
    const options = makeOptions({ imageRoot: tmpDir })
    const asList = await processDocument([{ name: "x", labels: [] }], options)
    expect(Array.isArray(asList.document)).toBe(true)

    const asDocument = await processDocument(
      { frames: [{ name: "x", labels: [] }], config: { categories: [] } },
      options
    )
    expect(Array.isArray(asDocument.document)).toBe(false)
    expect((asDocument.document as { config?: unknown }).config).toBeDefined()
  })

  test("still connects when the image is missing, and reports it", async () => {
    const frames: FrameLike[] = [
      {
        name: "nowhere.png",
        labels: [
          line("a", "lane", [
            [0, 0],
            [100, 0]
          ]),
          line("b", "lane", [
            [101, 0],
            [200, 0]
          ])
        ]
      }
    ]
    const { document, report } = await processDocument(
      frames,
      makeOptions({ imageRoot: path.join(tmpDir, "nope") })
    )
    expect(report.summary.missingImages).toEqual(["nowhere.png"])
    expect(report.frames[0].imageFound).toBe(false)
    // Auto-connect needs no pixels, so it still ran.
    expect(report.summary.totalMerged).toBe(1)
    expect(((document as FrameLike[])[0].labels ?? []).length).toBe(1)
  })

  test("clamps before connecting", async () => {
    // Imagery is x in [0, 10). Two lines whose facing endpoints sit far apart
    // out in the padding (x = 30 and x = 60, a gap of 30) but which land within
    // tolerance of each other once clamped back to the boundary. They may only
    // merge if the clamp ran first.
    const file = writePng("order.png", 80, 8, (x) =>
      x < 10 ? [200, 200, 200] : [0, 0, 0]
    )
    const frames: FrameLike[] = [
      {
        name: path.basename(file),
        labels: [
          line("a", "lane", [
            [2, 2],
            [30, 2]
          ]),
          line("b", "lane", [
            [60, 5],
            [2, 5]
          ])
        ]
      }
    ]
    const { report } = await processDocument(
      frames,
      makeOptions({ imageRoot: tmpDir })
    )
    expect(report.frames[0].clamped).toBe(2)
    expect(report.summary.totalMerged).toBe(1)
  })

  test("flags corrections beyond the review threshold without suppressing them", async () => {
    const file = writePng("flag.png", 400, 8, (x) =>
      x < 10 ? [200, 200, 200] : [0, 0, 0]
    )
    const frames: FrameLike[] = [
      {
        name: path.basename(file),
        labels: [
          line("a", "lane", [
            [2, 2],
            [390, 2]
          ])
        ]
      }
    ]
    const { document, report } = await processDocument(
      frames,
      makeOptions({ imageRoot: tmpDir })
    )
    expect(report.frames[0].flagged.length).toBe(1)
    // Flagged, but still applied.
    const vertices = (((document as FrameLike[])[0].labels ?? [])[0].poly2d ??
      [])[0].vertices as number[][]
    expect(vertices[1][0]).toBeLessThan(20)
  })
})

describe("correctAnnotations", () => {
  test("returns the originals unchanged when the corrector throws", async () => {
    const items = [
      {
        name: "x.png",
        labels: [
          line("a", "lane", [
            [0, 0],
            [1, 1]
          ])
        ]
      }
    ]
    const before = JSON.stringify(items)
    // A cache directory pointing at a file makes the mask layer fail; the
    // contract is that the import still succeeds with the originals.
    const result = await correctAnnotations(items as never, {
      imageRoot: path.join(tmpDir, "nope"),
      inProcess: true
    })
    expect(result.length).toBe(items.length)
    expect(JSON.stringify(items)).toBe(before)
  })

  test("does nothing to an empty item list", async () => {
    const result = await correctAnnotations([], { inProcess: true })
    expect(result).toEqual([])
  })

  test("merges through the public entry point", async () => {
    const items = [
      {
        name: "nowhere.png",
        labels: [
          line("a", "curb_road_edge", [
            [0, 0],
            [100, 0]
          ]),
          line("b", "without_curb_road_edge", [
            [101, 0],
            [200, 0]
          ])
        ]
      }
    ]
    const result = await correctAnnotations(items as never, {
      imageRoot: path.join(tmpDir, "nope"),
      inProcess: true
    })
    expect(result.length).toBe(1)
    expect((result[0].labels ?? []).length).toBe(1)
  })
})
