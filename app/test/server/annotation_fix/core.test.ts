/** @jest-environment node */
import * as fs from "fs"
import * as http from "http"
import * as os from "os"
import * as path from "path"

import {
  defaultOptions,
  Frame,
  processDocument,
  reportToDict,
  resolveImagePath
} from "../../../src/server/annotation_fix/core"
import { LabelExport } from "../../../src/types/export"

// eslint-disable-next-line @typescript-eslint/no-var-requires
const sharp = require("sharp") as typeof import("sharp").default

let dir: string
beforeAll(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "annotation-fix-core-"))
  fs.mkdirSync(path.join(dir, "items"))
  // 30x10 canvas, imagery in x < 20.
  await sharp({
    create: {
      width: 30,
      height: 10,
      channels: 3,
      background: { r: 0, g: 0, b: 0 }
    }
  })
    .composite([
      {
        input: {
          create: {
            width: 20,
            height: 10,
            channels: 3,
            background: { r: 120, g: 120, b: 120 }
          }
        },
        left: 0,
        top: 0
      }
    ])
    .png()
    .toFile(path.join(dir, "items", "frame.png"))
})
afterAll(() => {
  fs.rmSync(dir, { recursive: true, force: true })
})

/**
 * Open polyline label.
 *
 * @param id label id
 * @param category label category
 * @param vertices vertices
 */
function line(
  id: string,
  category: string,
  vertices: Array<[number, number]>
): LabelExport {
  return {
    id,
    category,
    attributes: {},
    manualShape: true,
    box2d: null,
    box3d: null,
    poly2d: [{ vertices, types: "L".repeat(vertices.length), closed: false }]
  }
}

test("resolveImagePath tries the name, root/name and root/basename", () => {
  const file = path.join(dir, "items", "frame.png")
  expect(resolveImagePath(file, "")).toBe(file)
  expect(resolveImagePath("items/frame.png", dir)).toBe(
    path.join(dir, "items/frame.png")
  )
  expect(resolveImagePath("elsewhere/frame.png", path.join(dir, "items"))).toBe(
    path.join(dir, "items", "frame.png")
  )
  expect(resolveImagePath("missing.png", dir)).toBeNull()
  expect(resolveImagePath("", dir)).toBeNull()
})

test("clamps then connects, deep-copies the input and keeps the bare-list shape", async () => {
  // a's endpoint sits in the padding at x=24. Clamped it lands at x=17.5
  // (nearest inside 19, inset 1.5), 1.5 px from b's start at x=19 -> merge.
  const frames: Frame[] = [
    {
      name: "items/frame.png",
      labels: [
        line("a", "lane", [
          [2, 5],
          [24, 5]
        ]),
        line("b", "lane", [
          [19, 5],
          [18, 8]
        ])
      ]
    }
  ]
  const snapshot = JSON.stringify(frames)
  const [out, report] = await processDocument(frames, {
    ...defaultOptions(),
    imageRoot: dir
  })
  expect(JSON.stringify(frames)).toBe(snapshot)
  expect(Array.isArray(out)).toBe(true)
  const corrected = out as Frame[]
  expect(corrected[0].labels).toHaveLength(1)
  const vertices = corrected[0].labels?.[0].poly2d?.[0].vertices ?? []
  expect(vertices).toHaveLength(3)
  expect(vertices[1][0]).toBeCloseTo(17.5, 9)
  expect(vertices[1][1]).toBeCloseTo(5, 9)
  expect(vertices[2]).toEqual([18, 8])
  const dict = reportToDict(report)
  expect(dict.summary).toEqual({
    frames: 1,
    totalClamped: 1,
    totalMerged: 1,
    totalFlagged: 0,
    missingImages: []
  })
  expect(dict.frames[0]).toMatchObject({
    name: "items/frame.png",
    imageFound: true,
    totalVertices: 4,
    clamped: 1,
    merged: 1,
    labelsBefore: 2,
    labelsAfter: 1,
    error: null
  })
  expect(dict.frames[0].roiCoverage).toBeCloseTo(20 / 30, 5)
  expect(Object.keys(dict.frames[0]).sort()).toEqual(
    [
      "clamped",
      "connections",
      "error",
      "flagged",
      "imageFound",
      "labelsAfter",
      "labelsBefore",
      "merged",
      "name",
      "roiCoverage",
      "totalVertices"
    ].sort()
  )
})

test("a missing image skips clamping but still connects", async () => {
  const doc = {
    frames: [
      {
        name: "nowhere.png",
        labels: [
          line("a", "lane", [
            [0, 0],
            [100, 0]
          ]),
          line("b", "lane", [
            [104, 0],
            [200, 0]
          ])
        ]
      }
    ] as Frame[]
  }
  const [out, report] = await processDocument(doc, {
    ...defaultOptions(),
    imageRoot: dir
  })
  expect(Array.isArray(out)).toBe(false)
  const frames = (out as { frames: Frame[] }).frames
  expect(frames[0].labels).toHaveLength(1)
  const dict = reportToDict(report)
  expect(dict.summary.missingImages).toEqual(["nowhere.png"])
  expect(dict.frames[0]).toMatchObject({
    imageFound: false,
    roiCoverage: null,
    clamped: 0,
    merged: 1
  })
})

describe("remote images", () => {
  let server: http.Server
  let base: string

  beforeAll(async () => {
    server = http.createServer((req, res) => {
      // Signed URLs carry a query string; route on the path alone.
      const pathname = new URL(req.url ?? "/", "http://x").pathname
      if (pathname === "/frame.png") {
        res.writeHead(200, { "Content-Type": "image/png" })
        fs.createReadStream(path.join(dir, "items", "frame.png")).pipe(res)
      } else if (pathname === "/moved.png") {
        res.writeHead(302, { Location: "/frame.png" })
        res.end()
      } else {
        res.writeHead(404)
        res.end("nope")
      }
    })
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve))
    const address = server.address() as { port: number }
    base = `http://127.0.0.1:${address.port}`
  })
  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()))
  })

  test("downloads the frame url when the image is not on disk", async () => {
    const frames: Frame[] = [
      {
        name: "not-here.png",
        url: `${base}/moved.png?sig=abc`,
        labels: [
          line("a", "lane", [
            [2, 5],
            [24, 5]
          ])
        ]
      }
    ]
    const [out, report] = await processDocument(frames, {
      ...defaultOptions(),
      imageRoot: dir
    })
    const dict = reportToDict(report)
    expect(dict.frames[0]).toMatchObject({
      imageFound: true,
      clamped: 1,
      error: null
    })
    expect(dict.frames[0].roiCoverage).toBeCloseTo(20 / 30, 5)
    const v = (out as Frame[])[0].labels?.[0].poly2d?.[0].vertices ?? []
    expect(v[1][0]).toBeCloseTo(17.5, 9)
    expect(
      fs
        .readdirSync(os.tmpdir())
        .filter((n) => n.startsWith("annotation-fix-image-"))
    ).toHaveLength(0)
  })

  test("a failed download skips the clamp but still connects", async () => {
    const frames: Frame[] = [
      {
        name: "not-here.png",
        url: `${base}/missing.png`,
        labels: [
          line("a", "lane", [
            [0, 0],
            [100, 0]
          ]),
          line("b", "lane", [
            [104, 0],
            [200, 0]
          ])
        ]
      }
    ]
    const [out, report] = await processDocument(frames, {
      ...defaultOptions(),
      imageRoot: dir
    })
    const dict = reportToDict(report)
    expect(dict.frames[0].imageFound).toBe(false)
    expect(dict.frames[0].error).toContain("HTTP 404")
    expect(dict.frames[0].merged).toBe(1)
    expect((out as Frame[])[0].labels).toHaveLength(1)
  })

  test("fetchRemote=false never touches the network", async () => {
    const frames: Frame[] = [
      { name: "not-here.png", url: `${base}/frame.png`, labels: [] }
    ]
    const [, report] = await processDocument(frames, {
      ...defaultOptions(),
      imageRoot: dir,
      fetchRemote: false
    })
    expect(report.frames[0].imageFound).toBe(false)
    expect(report.frames[0].error).toBeNull()
  })
})

test("stages can be switched off", async () => {
  const frames: Frame[] = [
    {
      name: "items/frame.png",
      labels: [
        line("a", "lane", [
          [2, 5],
          [24, 5]
        ]),
        line("b", "lane", [
          [25, 5],
          [28, 8]
        ])
      ]
    }
  ]
  const [out] = await processDocument(frames, {
    ...defaultOptions(),
    imageRoot: dir,
    clamp: false,
    connect: false
  })
  expect((out as Frame[])[0].labels).toHaveLength(2)
  expect((out as Frame[])[0].labels?.[0].poly2d?.[0].vertices[1]).toEqual([
    24, 5
  ])
})
