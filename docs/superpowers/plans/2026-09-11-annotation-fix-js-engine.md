# Annotation Auto-Correct JS Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python `tools/annotation_fix` subprocess with a TypeScript engine compiled into `app/dist/annotation_fix_worker.js`, so the "Auto-correct annotations" checkbox works after `npm install && npm run build` with no Python.

**Architecture:** A new `app/src/server/annotation_fix/` package mirrors the Python modules (mask → clamp → autoconnect → core → stdio). A second server webpack entry emits the worker script; `annotation_fix.ts` spawns `process.execPath` on it over the existing stdin/stdout JSON protocol. Worker, status, listeners and dashboard code is untouched.

**Tech Stack:** TypeScript (strict), `sharp` 0.35 for PNG decoding, Jest + ts-jest, webpack with `webpack-node-externals`.

**Spec:** `docs/superpowers/specs/2026-09-11-annotation-fix-js-engine-design.md`

## Global Constraints

- `sharp` `^0.35.4` requires Node `>=20.9.0`; the Dockerfile base becomes `node:20-bookworm-slim`.
- Report JSON field names must equal the Python `to_dict()` output exactly (see spec §3).
- Correction rules are ported verbatim: `DEFAULT_TOLERANCE = 15`, `DEFAULT_MIN_ANGLE = 0`, `DEFAULT_INSET = 1.5`, `DEFAULT_FLAG_DISTANCE = 50`, `DEFAULT_THRESHOLD = 10`, mergeable pair `{curb_road_edge, without_curb_road_edge}`.
- Python `round()` is round-half-to-even; the engine must use a `roundHalfEven` helper wherever Python rounds a coordinate to a pixel index.
- The worker writes exactly one JSON value to stdout; all diagnostics go to stderr.
- All new Jest test files start with `/** @jest-environment node */` (the project default is jsdom).
- `tsconfig.json` uses `module: es6` without `esModuleInterop`, so `sharp` (a CommonJS callable export) is loaded with `require`, not `import`.
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## File map

| Path | Responsibility |
|---|---|
| `app/src/server/annotation_fix/autoconnect.ts` | endpoint pairing and splicing (create) |
| `app/src/server/annotation_fix/mask.ts` | ROI mask from a PNG; `contains` / `nearestInside` (create) |
| `app/src/server/annotation_fix/clamp.ts` | pull vertices inside the mask (create) |
| `app/src/server/annotation_fix/core.ts` | options, reports, `processFrame`, `processDocument` (create) |
| `app/src/server/annotation_fix/stdio.ts` | worker entry: stdin → stdout, `--preflight` (create) |
| `app/src/server/annotation_fix.ts` | spawn the Node worker instead of Python (modify) |
| `webpack.config.js` | second server entry (modify) |
| `package.json` | add `sharp` (modify) |
| `Dockerfile` | Node 20, no Python (modify) |
| `tools/annotation_fix/{stdio,api,core}.py`, `tools/annotation_fix/README.md`, `tools/requirements.txt` | say the server no longer uses them (modify) |
| `tools/annotation_fix_parity.js` | run both engines, diff (create) |
| `app/test/server/annotation_fix/*.test.ts` | unit tests (create) |

---

### Task 1: Auto-connect

**Files:**
- Create: `app/src/server/annotation_fix/autoconnect.ts`
- Test: `app/test/server/annotation_fix/autoconnect.test.ts`

**Interfaces:**
- Consumes: `LabelExport`, `PolygonExportType` from `app/src/types/export.ts`.
- Produces: `connectLabels(labels: LabelExport[], tolerance?, minAngle?, mergeable?) => [LabelExport[], ConnectResult]`, `splice(...)`, `Connection`, `ConnectResult`, `connectionToDict(c)`, `DEFAULT_TOLERANCE`, `DEFAULT_MIN_ANGLE`, `DEFAULT_MERGEABLE_CATEGORIES`.

- [ ] **Step 1: Write the failing tests**

`app/test/server/annotation_fix/autoconnect.test.ts`:

```ts
/** @jest-environment node */
import {
  connectLabels,
  splice
} from "../../../src/server/annotation_fix/autoconnect"
import { LabelExport } from "../../../src/types/export"

/** Build an open polyline label. */
function line(
  id: string,
  category: string,
  vertices: Array<[number, number]>,
  types?: string,
  closed = false
): LabelExport {
  return {
    id,
    category,
    attributes: {},
    manualShape: true,
    box2d: null,
    box3d: null,
    poly2d: [{ vertices, types: types ?? "L".repeat(vertices.length), closed }]
  }
}

describe("splice", () => {
  const a: Array<[number, number]> = [[0, 0], [10, 0]]
  const b: Array<[number, number]> = [[10, 0], [20, 0]]

  test("A.end -> B.start drops B's junction vertex", () => {
    expect(splice(a, "LC", false, b, "CL", true)).toEqual([
      [[0, 0], [10, 0], [20, 0]],
      "LCL"
    ])
  })
  test("A.start -> B.end prepends B without its last vertex", () => {
    expect(splice(b, "CL", true, a, "LC", false)).toEqual([
      [[0, 0], [10, 0], [20, 0]],
      "LCL"
    ])
  })
  test("A.start -> B.start reverses A", () => {
    const rev: Array<[number, number]> = [[10, 0], [0, 0]]
    expect(splice(rev, "CL", true, b, "CL", true)).toEqual([
      [[0, 0], [10, 0], [20, 0]],
      "LCL"
    ])
  })
  test("A.end -> B.end reverses B", () => {
    const revB: Array<[number, number]> = [[20, 0], [10, 0]]
    expect(splice(a, "LC", false, revB, "LC", false)).toEqual([
      [[0, 0], [10, 0], [20, 0]],
      "LCL"
    ])
  })
})

describe("connectLabels", () => {
  test("merges same-category lines within tolerance, keeps lower index", () => {
    const labels = [
      line("a", "lane", [[0, 0], [100, 0]]),
      line("b", "lane", [[105, 0], [200, 0]])
    ]
    const [out, result] = connectLabels(labels)
    expect(out).toHaveLength(1)
    expect(out[0].id).toBe("a")
    expect(out[0].poly2d?.[0].vertices).toEqual([[0, 0], [100, 0], [200, 0]])
    expect(out[0].poly2d?.[0].types).toBe("LLL")
    expect(result.connections).toHaveLength(1)
    expect(result.connections[0]).toMatchObject({
      keptId: "a",
      absorbedId: "b",
      category: "lane",
      junction: [100, 0],
      gap: 5
    })
    expect(result.labelsBefore).toBe(2)
    expect(result.labelsAfter).toBe(1)
  })

  test("refuses lines further apart than tolerance", () => {
    const labels = [
      line("a", "lane", [[0, 0], [100, 0]]),
      line("b", "lane", [[116, 0], [200, 0]])
    ]
    const [out, result] = connectLabels(labels)
    expect(out).toHaveLength(2)
    expect(result.connections).toHaveLength(0)
  })

  test("allows the listed cross-category pair only", () => {
    const allowed = [
      line("a", "curb_road_edge", [[0, 0], [100, 0]]),
      line("b", "without_curb_road_edge", [[101, 0], [200, 0]])
    ]
    expect(connectLabels(allowed)[0]).toHaveLength(1)

    const refused = [
      line("a", "white_line", [[0, 0], [100, 0]]),
      line("b", "yellow_line", [[101, 0], [200, 0]])
    ]
    expect(connectLabels(refused)[0]).toHaveLength(2)
  })

  test("resolves chains across passes and consumes each endpoint once", () => {
    const labels = [
      line("a", "lane", [[0, 0], [100, 0]]),
      line("b", "lane", [[103, 0], [200, 0]]),
      line("c", "lane", [[204, 0], [300, 0]])
    ]
    const [out, result] = connectLabels(labels)
    expect(out).toHaveLength(1)
    expect(out[0].poly2d?.[0].vertices).toEqual([
      [0, 0],
      [100, 0],
      [200, 0],
      [300, 0]
    ])
    expect(result.connections.map((c) => c.absorbedId)).toEqual(["b", "c"])
  })

  test("shortest gap wins when three endpoints converge", () => {
    const labels = [
      line("far", "lane", [[-100, 0], [-6, 0]]),
      line("hub", "lane", [[0, 0], [100, 0]]),
      line("near", "lane", [[-2, 0], [-2, -100]])
    ]
    const [out, result] = connectLabels(labels)
    // hub.start joins near.start (gap 2); far.end then finds nothing within 15
    // of a free endpoint except the already-consumed junction.
    expect(result.connections[0]).toMatchObject({ keptId: "hub", absorbedId: "near", gap: 2 })
    expect(out.map((l) => l.id)).toEqual(["far", "hub"])
  })

  test("ignores closed rings, multi-polygon labels and single vertices", () => {
    const labels = [
      line("closed", "lane", [[0, 0], [100, 0], [50, 50]], "LLL", true),
      line("open", "lane", [[101, 0], [200, 0]]),
      { ...line("multi", "lane", [[201, 0], [300, 0]]), poly2d: [
        { vertices: [[201, 0], [300, 0]], types: "LL", closed: false },
        { vertices: [[400, 0], [500, 0]], types: "LL", closed: false }
      ] },
      line("dot", "lane", [[301, 0]], "L")
    ]
    const [out] = connectLabels(labels)
    expect(out).toHaveLength(4)
  })

  test("minAngle rejects a sharp fork and accepts a straight continuation", () => {
    const straight = [
      line("a", "lane", [[0, 0], [100, 0]]),
      line("b", "lane", [[105, 0], [200, 0]])
    ]
    const [outStraight, resultStraight] = connectLabels(straight, 15, 150)
    expect(outStraight).toHaveLength(1)
    expect(resultStraight.connections[0].angle).toBeCloseTo(180, 5)

    const fork = [
      line("a", "lane", [[0, 0], [100, 0]]),
      line("b", "lane", [[105, 0], [50, 80]])
    ]
    expect(connectLabels(fork, 15, 150)[0]).toHaveLength(2)
  })

  test("does not mutate labels that were not merged", () => {
    const untouched = line("solo", "lane", [[0, 0], [100, 0]])
    const before = JSON.stringify(untouched)
    connectLabels([untouched])
    expect(JSON.stringify(untouched)).toBe(before)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/server/annotation_fix/autoconnect.test.ts`
Expected: FAIL — cannot find module `autoconnect`.

- [ ] **Step 3: Implement `autoconnect.ts`**

```ts
/**
 * Batch endpoint connection for imported polylines.
 *
 * The offline equivalent of the editor's drag-an-endpoint-onto-another
 * gesture: `Label2DList.findNearestEndpoint` finds a target within 15 screen
 * px and `Polygon2D.mergeWith` splices the two lines into one label. Here the
 * same rules run over export frames before import.
 *
 * Differences from the interactive path, kept from the Python original:
 * tolerance is in IMAGE pixels (no zoom offline), and only same-category
 * pairs merge — plus the one listed cross-category pair that describes the
 * same physical feature.
 */

import { LabelExport, PolygonExportType } from "../../types/export"

/** Matches the editor's 15 px snap radius, reinterpreted in image space. */
export const DEFAULT_TOLERANCE = 15.0

/** Straightness guard in degrees; 0 disables it. */
export const DEFAULT_MIN_ANGLE = 0.0

/**
 * Category pairs describing the SAME physical feature, allowed to merge
 * across the class boundary. A road edge changes curb status partway along
 * constantly, splitting one edge into two labels whose ends touch. Paint
 * markings are deliberately absent: a yellow line meeting a white one is a
 * real class boundary.
 */
export const DEFAULT_MERGEABLE_CATEGORIES: ReadonlyArray<ReadonlySet<string>> =
  [new Set(["curb_road_edge", "without_curb_road_edge"])]

/** One merge of two polylines. */
export interface Connection {
  /** id of the label that survived */
  keptId: string
  /** id of the label spliced into it */
  absorbedId: string
  /** category of the surviving label */
  category: string
  /** where the two lines were joined */
  junction: [number, number]
  /** distance between the two endpoints before the merge */
  gap: number
  /** junction angle in degrees, only when the guard was active */
  angle?: number
}

/** Outcome of auto-connecting one frame. */
export interface ConnectResult {
  /** merges applied, in order */
  connections: Connection[]
  /** label count before */
  labelsBefore: number
  /** label count after */
  labelsAfter: number
}

/**
 * Serialise a connection for the run report, rounding like the Python
 * report does.
 *
 * @param c the connection
 */
export function connectionToDict(c: Connection): { [key: string]: unknown } {
  const payload: { [key: string]: unknown } = {
    keptId: c.keptId,
    absorbedId: c.absorbedId,
    category: c.category,
    junction: c.junction.map((v) => round3(v)),
    gap: round3(c.gap)
  }
  if (c.angle !== undefined) {
    payload.angle = Math.round(c.angle * 100) / 100
  }
  return payload
}

/**
 * Round to three decimals.
 *
 * @param v value
 */
function round3(v: number): number {
  return Math.round(v * 1000) / 1000
}

/**
 * Whether two categories may be joined: identical always, different only
 * when the pair is listed.
 *
 * @param a first category
 * @param b second category
 * @param mergeable allowed cross-category pairs
 */
function categoriesCompatible(
  a: string,
  b: string,
  mergeable: ReadonlyArray<ReadonlySet<string>>
): boolean {
  if (a === b) {
    return true
  }
  return mergeable.some((pair) => pair.size === 2 && pair.has(a) && pair.has(b))
}

/**
 * A label's single open poly2d, or null if it is not eligible. Closed rings
 * never participate; multi-polygon labels are skipped.
 *
 * @param label the label
 */
function openPolyline(label: LabelExport): PolygonExportType | null {
  const polys = label.poly2d ?? []
  if (polys.length !== 1) {
    return null
  }
  const poly = polys[0]
  if (poly.closed) {
    return null
  }
  const vertices = poly.vertices ?? []
  if (vertices.length < 2) {
    return null
  }
  return poly
}

/**
 * Coordinates of a polyline's start or end vertex.
 *
 * @param poly the polyline
 * @param isStart start (true) or end (false)
 */
function endpoint(poly: PolygonExportType, isStart: boolean): [number, number] {
  const v = isStart ? poly.vertices[0] : poly.vertices[poly.vertices.length - 1]
  return [Number(v[0]), Number(v[1])]
}

/**
 * Unit vector pointing outward from the given endpoint.
 *
 * @param poly the polyline
 * @param isStart which endpoint
 */
function direction(
  poly: PolygonExportType,
  isStart: boolean
): [number, number] | null {
  const vs = poly.vertices
  if (vs.length < 2) {
    return null
  }
  const tip = isStart ? vs[0] : vs[vs.length - 1]
  const neighbour = isStart ? vs[1] : vs[vs.length - 2]
  const dx = Number(tip[0]) - Number(neighbour[0])
  const dy = Number(tip[1]) - Number(neighbour[1])
  const norm = Math.hypot(dx, dy)
  if (norm < 1e-9) {
    return null
  }
  return [dx / norm, dy / norm]
}

/**
 * Angle in degrees at a prospective junction: 180 means the lines continue
 * straight through each other.
 *
 * @param polyA first polyline
 * @param startA endpoint of A
 * @param polyB second polyline
 * @param startB endpoint of B
 */
function junctionAngle(
  polyA: PolygonExportType,
  startA: boolean,
  polyB: PolygonExportType,
  startB: boolean
): number | null {
  const dirA = direction(polyA, startA)
  const dirB = direction(polyB, startB)
  if (dirA === null || dirB === null) {
    return null
  }
  // Both point outward from their tips, so continuation shows as opposition.
  const cosine = Math.min(
    1,
    Math.max(-1, -(dirA[0] * dirB[0] + dirA[1] * dirB[1]))
  )
  return 180 - (Math.acos(cosine) * 180) / Math.PI
}

/**
 * Join two vertex runs at the touching endpoints, mirroring
 * `Polygon2D.mergeWith`. B's duplicate junction vertex is dropped and the
 * per-vertex types string is spliced identically.
 *
 * @param verticesA A's vertices
 * @param typesA A's types
 * @param startA whether A joins at its start
 * @param verticesB B's vertices
 * @param typesB B's types
 * @param startB whether B joins at its start
 */
export function splice(
  verticesA: Array<[number, number]>,
  typesA: string,
  startA: boolean,
  verticesB: Array<[number, number]>,
  typesB: string,
  startB: boolean
): [Array<[number, number]>, string] {
  const a = verticesA.slice()
  const b = verticesB.slice()
  const reverse = (s: string): string => s.split("").reverse().join("")

  if (!startA && startB) {
    return [a.concat(b.slice(1)), typesA + typesB.slice(1)]
  }
  if (startA && !startB) {
    return [b.slice(0, -1).concat(a), typesB.slice(0, -1) + typesA]
  }
  if (startA && startB) {
    return [a.reverse().concat(b.slice(1)), reverse(typesA) + typesB.slice(1)]
  }
  return [a.concat(b.reverse().slice(1)), typesA + reverse(typesB).slice(1)]
}

/** A free endpoint of an eligible line. */
interface Endpoint {
  /** index into the working list */
  index: number
  /** start or end */
  isStart: boolean
  /** the polyline */
  poly: PolygonExportType
  /** endpoint coordinates */
  point: [number, number]
}

/** Best candidate pair found in one pass. */
interface Candidate {
  /** endpoint gap */
  gap: number
  /** index of A */
  idxA: number
  /** A joins at its start */
  startA: boolean
  /** index of B */
  idxB: number
  /** B joins at its start */
  startB: boolean
  /** junction angle when the guard was active */
  angle: number | null
}

/**
 * Merge polylines whose endpoints nearly touch.
 *
 * Pairs are taken shortest-gap first and each endpoint is consumed at most
 * once. After every merge the candidates are rebuilt from scratch, so chains
 * resolve across passes. Survivors are returned in their original order;
 * labels are mutated in place (vertices/types of the kept label).
 *
 * @param labels the frame's labels
 * @param tolerance maximum endpoint gap in image pixels
 * @param minAngle minimum junction angle in degrees, 0 disables
 * @param mergeable allowed cross-category pairs
 */
export function connectLabels(
  labels: LabelExport[],
  tolerance: number = DEFAULT_TOLERANCE,
  minAngle: number = DEFAULT_MIN_ANGLE,
  mergeable: ReadonlyArray<ReadonlySet<string>> = DEFAULT_MERGEABLE_CATEGORIES
): [LabelExport[], ConnectResult] {
  const result: ConnectResult = {
    connections: [],
    labelsBefore: labels.length,
    labelsAfter: labels.length
  }
  const working = labels.slice()
  const absorbed = new Set<LabelExport>()

  for (;;) {
    const endpoints: Endpoint[] = []
    working.forEach((label, index) => {
      if (absorbed.has(label)) {
        return
      }
      const poly = openPolyline(label)
      if (poly === null) {
        return
      }
      for (const isStart of [true, false]) {
        endpoints.push({ index, isStart, poly, point: endpoint(poly, isStart) })
      }
    })

    let best: Candidate | null = null
    for (let i = 0; i < endpoints.length; i++) {
      const ea = endpoints[i]
      const labelA = working[ea.index]
      for (let j = i + 1; j < endpoints.length; j++) {
        const eb = endpoints[j]
        if (ea.index === eb.index) {
          continue // Self-closing is an editor gesture, not a batch one.
        }
        const labelB = working[eb.index]
        if (
          !categoriesCompatible(
            String(labelA.category ?? ""),
            String(labelB.category ?? ""),
            mergeable
          )
        ) {
          continue
        }
        const gap = Math.hypot(
          ea.point[0] - eb.point[0],
          ea.point[1] - eb.point[1]
        )
        if (gap > tolerance) {
          continue
        }
        let angle: number | null = null
        if (minAngle > 0) {
          angle = junctionAngle(ea.poly, ea.isStart, eb.poly, eb.isStart)
          if (angle !== null && angle < minAngle) {
            continue
          }
        }
        if (best === null || gap < best.gap) {
          best = {
            gap,
            idxA: ea.index,
            startA: ea.isStart,
            idxB: eb.index,
            startB: eb.isStart,
            angle
          }
        }
      }
    }

    if (best === null) {
      break
    }

    const labelA = working[best.idxA]
    const labelB = working[best.idxB]
    const polyA = openPolyline(labelA)
    const polyB = openPolyline(labelB)
    if (polyA === null || polyB === null) {
      break
    }

    const junction = endpoint(polyA, best.startA)
    const [vertices, types] = splice(
      polyA.vertices,
      String(polyA.types ?? ""),
      best.startA,
      polyB.vertices,
      String(polyB.types ?? ""),
      best.startB
    )
    polyA.vertices = vertices
    polyA.types = types

    absorbed.add(labelB)
    const connection: Connection = {
      keptId: String(labelA.id ?? ""),
      absorbedId: String(labelB.id ?? ""),
      category: String(labelA.category ?? ""),
      junction,
      gap: best.gap
    }
    if (best.angle !== null) {
      connection.angle = best.angle
    }
    result.connections.push(connection)
  }

  const survivors = working.filter((label) => !absorbed.has(label))
  result.labelsAfter = survivors.length
  return [survivors, result]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/server/annotation_fix/autoconnect.test.ts`
Expected: PASS (9 tests).

- [ ] **Step 5: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server/annotation_fix app/test/server/annotation_fix
git add app/src/server/annotation_fix/autoconnect.ts app/test/server/annotation_fix/autoconnect.test.ts
git commit -m "feat(annotation-fix): port polyline auto-connect to TypeScript

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: ROI mask

**Files:**
- Modify: `package.json` (add `sharp`)
- Create: `app/src/server/annotation_fix/mask.ts`
- Test: `app/test/server/annotation_fix/mask.test.ts`

**Interfaces:**
- Produces: `class RoiMask { width; height; data: Uint8Array; coverage; contains(x,y): boolean; nearestInside(x,y): [nx, ny, distance] }`, `computeRoiMask(imagePath, threshold?) => Promise<RoiMask>`, `roundHalfEven(v)`, `DEFAULT_THRESHOLD`.

- [ ] **Step 1: Install sharp**

Run: `npm install sharp@^0.35.4`
Expected: `package.json` gains `"sharp": "^0.35.4"` under `dependencies`; `node -e "require('sharp')"` exits 0.

- [ ] **Step 2: Write the failing tests**

`app/test/server/annotation_fix/mask.test.ts`:

```ts
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
const sharp = require("sharp") as typeof import("sharp")

/** A white rectangle to draw onto the black canvas. */
interface Rect {
  left: number
  top: number
  width: number
  height: number
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
      create: { width: 4, height: 4, channels: 3, background: { r: 0, g: 0, b: 0 } }
    })
      .composite([
        {
          input: {
            create: { width: 2, height: 2, channels: 3, background: { r: 0, g: 0, b: 40 } }
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
  const roi = maskFromRows([
    "......",
    ".###..",
    ".###..",
    ".###..",
    "......"
  ])

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
    const tricky = maskFromRows([
      "....#",
      ".....",
      ".....",
      "...#."
    ])
    const [nx, ny, d] = tricky.nearestInside(0, 0)
    expect([nx, ny]).toEqual([4, 0])
    expect(d).toBeCloseTo(4, 9)
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npx jest app/test/server/annotation_fix/mask.test.ts`
Expected: FAIL — cannot find module `mask`.

- [ ] **Step 4: Implement `mask.ts`**

```ts
/**
 * ROI mask extraction for orthomosaic images.
 *
 * The images are aerial orthomosaics composited into a rectangular canvas:
 * the captured imagery occupies a narrow footprint and every pixel outside it
 * is pure black padding. The mask produced here defines the valid region.
 *
 * Channel reduction uses max(R, G, B), NOT luminance: luminance pushes dark
 * blue-grey asphalt below the threshold and shatters the mask.
 *
 * Unlike the Python original, no full-canvas distance transform is built.
 * Nearest-inside lookups run a ring search from the vertex, which needs no
 * memory beyond the mask itself and only touches pixels near the few
 * vertices that actually fall outside.
 */

// sharp exports a callable CommonJS module; tsconfig has no esModuleInterop.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const sharp = require("sharp") as typeof import("sharp")

/**
 * Orthomosaic padding is exactly (0, 0, 0); real imagery essentially never
 * is. Measured insensitive across thresholds 2..30.
 */
export const DEFAULT_THRESHOLD = 10

/**
 * Python's round(): half to even. Coordinates on exact .5 must land on the
 * same pixel the reference implementation used.
 *
 * @param v value
 */
export function roundHalfEven(v: number): number {
  const f = Math.floor(v)
  const diff = v - f
  if (diff < 0.5) {
    return f
  }
  if (diff > 0.5) {
    return f + 1
  }
  return f % 2 === 0 ? f : f + 1
}

/**
 * A boolean ROI mask with nearest-inside lookup.
 */
export class RoiMask {
  /** mask width */
  public readonly width: number
  /** mask height */
  public readonly height: number
  /** 1 where the pixel is inside the region of interest, row-major */
  public readonly data: Uint8Array

  /**
   * @param width mask width
   * @param height mask height
   * @param data row-major 0/1 values, length width * height
   */
  constructor(width: number, height: number, data: Uint8Array) {
    this.width = width
    this.height = height
    this.data = data
  }

  /** Fraction of the canvas inside the ROI. */
  public get coverage(): number {
    let inside = 0
    for (let i = 0; i < this.data.length; i++) {
      inside += this.data[i]
    }
    return this.data.length === 0 ? 0 : inside / this.data.length
  }

  /**
   * Whether an image-space point lies inside the ROI. Points beyond the
   * canvas are outside by definition.
   *
   * @param x x coordinate
   * @param y y coordinate
   */
  public contains(x: number, y: number): boolean {
    const ix = roundHalfEven(x)
    const iy = roundHalfEven(y)
    if (ix < 0 || iy < 0 || ix >= this.width || iy >= this.height) {
      return false
    }
    return this.data[iy * this.width + ix] === 1
  }

  /**
   * Nearest in-ROI pixel to a point, with the distance to it measured from
   * the true (unrounded) point. For a point already inside, the point itself
   * with distance 0.
   *
   * Scans square rings around the clipped point. A ring's Chebyshev radius
   * under-bounds Euclidean distance, so after a hit the scan continues until
   * the ring's lower bound exceeds the best distance found.
   *
   * @param x x coordinate
   * @param y y coordinate
   */
  public nearestInside(x: number, y: number): [number, number, number] {
    const w = this.width
    const h = this.height
    const ix = Math.min(Math.max(roundHalfEven(x), 0), w - 1)
    const iy = Math.min(Math.max(roundHalfEven(y), 0), h - 1)
    if (this.data[iy * w + ix] === 1) {
      return [ix, iy, 0]
    }

    // Distance from the true point to the ring centre: ring r pixels are at
    // least r - offset from the true point.
    const offset = Math.hypot(x - ix, y - iy)
    let bestX = ix
    let bestY = iy
    let bestDist = Infinity
    const maxRadius = Math.max(w, h)

    const visit = (px: number, py: number): void => {
      if (px < 0 || py < 0 || px >= w || py >= h) {
        return
      }
      if (this.data[py * w + px] !== 1) {
        return
      }
      const d = Math.hypot(px - x, py - y)
      if (d < bestDist) {
        bestDist = d
        bestX = px
        bestY = py
      }
    }

    for (let r = 1; r <= maxRadius; r++) {
      if (bestDist !== Infinity && r - offset > bestDist) {
        break
      }
      for (let px = ix - r; px <= ix + r; px++) {
        visit(px, iy - r)
        visit(px, iy + r)
      }
      for (let py = iy - r + 1; py <= iy + r - 1; py++) {
        visit(ix - r, py)
        visit(ix + r, py)
      }
    }

    if (bestDist === Infinity) {
      // Empty mask: nothing to clamp to. Report the clipped point.
      return [ix, iy, offset]
    }
    return [bestX, bestY, bestDist]
  }
}

/** Growable stack of pixel indices for flood fills. */
class IndexStack {
  private buffer = new Int32Array(1 << 16)
  private size = 0

  /**
   * @param index pixel index to push
   */
  public push(index: number): void {
    if (this.size === this.buffer.length) {
      const grown = new Int32Array(this.buffer.length * 2)
      grown.set(this.buffer)
      this.buffer = grown
    }
    this.buffer[this.size++] = index
  }

  /** Pop the top index, or -1 when empty. */
  public pop(): number {
    return this.size === 0 ? -1 : this.buffer[--this.size]
  }
}

/**
 * Flood-fill 4-connected pixels equal to `from`, setting them to `to`.
 *
 * @param data the mask
 * @param width mask width
 * @param height mask height
 * @param seed starting pixel index
 * @param from value to replace
 * @param to replacement value
 * @param stack scratch stack
 * @returns number of pixels filled
 */
function floodFill(
  data: Uint8Array,
  width: number,
  height: number,
  seed: number,
  from: number,
  to: number,
  stack: IndexStack
): number {
  if (data[seed] !== from) {
    return 0
  }
  data[seed] = to
  stack.push(seed)
  let count = 0
  for (let i = stack.pop(); i !== -1; i = stack.pop()) {
    count++
    const x = i % width
    const y = (i - x) / width
    if (x > 0 && data[i - 1] === from) {
      data[i - 1] = to
      stack.push(i - 1)
    }
    if (x < width - 1 && data[i + 1] === from) {
      data[i + 1] = to
      stack.push(i + 1)
    }
    if (y > 0 && data[i - width] === from) {
      data[i - width] = to
      stack.push(i - width)
    }
    if (y < height - 1 && data[i + width] === from) {
      data[i + width] = to
      stack.push(i + width)
    }
  }
  return count
}

/**
 * Fill interior holes in place: background pixels not reachable from the
 * canvas border become foreground. Equivalent to
 * scipy.ndimage.binary_fill_holes with the default 4-connected structure.
 *
 * @param data the mask (0/1)
 * @param width mask width
 * @param height mask height
 */
export function fillHoles(data: Uint8Array, width: number, height: number): void {
  const stack = new IndexStack()
  // Mark border-reachable background as 2.
  for (let x = 0; x < width; x++) {
    floodFill(data, width, height, x, 0, 2, stack)
    floodFill(data, width, height, (height - 1) * width + x, 0, 2, stack)
  }
  for (let y = 0; y < height; y++) {
    floodFill(data, width, height, y * width, 0, 2, stack)
    floodFill(data, width, height, y * width + width - 1, 0, 2, stack)
  }
  // Remaining 0s are holes; 2s are the real outside.
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0) {
      data[i] = 1
    } else if (data[i] === 2) {
      data[i] = 0
    }
  }
}

/**
 * Keep only the largest 4-connected foreground component, in place. On a
 * size tie the first component in raster order wins, matching
 * scipy.ndimage.label + argmax.
 *
 * @param data the mask (0/1)
 * @param width mask width
 * @param height mask height
 */
export function keepLargestComponent(
  data: Uint8Array,
  width: number,
  height: number
): void {
  const stack = new IndexStack()
  const seeds: number[] = []
  let bestSeed = -1
  let bestSize = 0
  for (let i = 0; i < data.length; i++) {
    if (data[i] !== 1) {
      continue
    }
    const size = floodFill(data, width, height, i, 1, 2, stack)
    seeds.push(i)
    if (size > bestSize) {
      bestSize = size
      bestSeed = i
    }
  }
  for (const seed of seeds) {
    floodFill(data, width, height, seed, 2, seed === bestSeed ? 1 : 0, stack)
  }
}

/**
 * Extract the ROI mask from an orthomosaic image: threshold max(R, G, B),
 * fill interior holes, keep the largest connected component.
 *
 * @param imagePath path to the image
 * @param threshold values at or below this are padding
 */
export async function computeRoiMask(
  imagePath: string,
  threshold: number = DEFAULT_THRESHOLD
): Promise<RoiMask> {
  const { data: pixels, info } = await sharp(imagePath, {
    limitInputPixels: false
  })
    .removeAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true })

  const { width, height, channels } = info
  const data = new Uint8Array(width * height)
  for (let i = 0, p = 0; i < data.length; i++, p += channels) {
    let max = pixels[p]
    for (let c = 1; c < channels; c++) {
      if (pixels[p + c] > max) {
        max = pixels[p + c]
      }
    }
    data[i] = max > threshold ? 1 : 0
  }

  fillHoles(data, width, height)
  keepLargestComponent(data, width, height)
  return new RoiMask(width, height, data)
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx jest app/test/server/annotation_fix/mask.test.ts`
Expected: PASS (8 tests).

- [ ] **Step 6: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server/annotation_fix app/test/server/annotation_fix
git add package.json package-lock.json app/src/server/annotation_fix/mask.ts app/test/server/annotation_fix/mask.test.ts
git commit -m "feat(annotation-fix): ROI mask extraction in TypeScript via sharp

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Clamp

**Files:**
- Create: `app/src/server/annotation_fix/clamp.ts`
- Test: `app/test/server/annotation_fix/clamp.test.ts`

**Interfaces:**
- Consumes: `RoiMask` from Task 2.
- Produces: `clampVertices(roi, vertices, labelId, category, inset) => [vertices, VertexCorrection[]]`, `clampLabels(roi, labels, inset) => ClampResult`, `flaggedCorrections(result, flagDistance)`, `correctionToDict(c)`, `DEFAULT_INSET`, `DEFAULT_FLAG_DISTANCE`.

- [ ] **Step 1: Write the failing tests**

`app/test/server/annotation_fix/clamp.test.ts`:

```ts
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
  const [out, corrections] = clampVertices(halfMask(), [[1.25, 2.75], [9.4, 0]])
  expect(out).toEqual([[1.25, 2.75], [9.4, 0]])
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
  const untouchedVertices: Array<[number, number]> = [[1, 1], [2, 2]]
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
      poly2d: [{ vertices: [[3, 3], [70, 3]], types: "LL", closed: false }]
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/server/annotation_fix/clamp.test.ts`
Expected: FAIL — cannot find module `clamp`.

- [ ] **Step 3: Implement `clamp.ts`**

```ts
/**
 * Pull out-of-ROI annotation vertices back inside the imagery.
 *
 * Measured on real batches the spill is tiny (median 8 px against images
 * 5000-20000 px wide): an annotator tracing a curb a hair past where imagery
 * ends. So: clamp, don't clip. Vertex count, curve types, closed state and
 * identity are preserved; only coordinates move. Corrections beyond
 * `flagDistance` are still applied but reported for review.
 */

import { LabelExport } from "../../types/export"
import { RoiMask } from "./mask"

/** Corrections beyond this many pixels are reported for review. */
export const DEFAULT_FLAG_DISTANCE = 50.0

/** Nudge clamped vertices this far inside the boundary. */
export const DEFAULT_INSET = 1.5

/** One vertex moved back inside the ROI. */
export interface VertexCorrection {
  /** owning label id */
  labelId: string
  /** owning label category */
  category: string
  /** index within the polyline */
  vertexIndex: number
  /** where it was */
  original: [number, number]
  /** where it went */
  corrected: [number, number]
  /** distance from the original to the nearest inside pixel */
  distance: number
}

/** Outcome of clamping one frame. */
export interface ClampResult {
  /** vertices examined */
  totalVertices: number
  /** vertices moved */
  corrections: VertexCorrection[]
}

/**
 * Round to three decimals.
 *
 * @param v value
 */
function round3(v: number): number {
  return Math.round(v * 1000) / 1000
}

/**
 * Serialise a correction for the run report.
 *
 * @param c the correction
 */
export function correctionToDict(c: VertexCorrection): {
  [key: string]: unknown
} {
  return {
    labelId: c.labelId,
    category: c.category,
    vertexIndex: c.vertexIndex,
    original: c.original.map(round3),
    corrected: c.corrected.map(round3),
    distance: round3(c.distance)
  }
}

/**
 * Corrections large enough to warrant a human look.
 *
 * @param result clamp result
 * @param flagDistance threshold in pixels
 */
export function flaggedCorrections(
  result: ClampResult,
  flagDistance: number
): VertexCorrection[] {
  return result.corrections.filter((c) => c.distance > flagDistance)
}

/**
 * Step a boundary point slightly inward along the incoming direction. If the
 * inset lands back outside (thin region), keep the boundary point.
 *
 * @param roi the mask
 * @param nx nearest inside x
 * @param ny nearest inside y
 * @param ox original x
 * @param oy original y
 * @param inset distance to step
 */
function insetPoint(
  roi: RoiMask,
  nx: number,
  ny: number,
  ox: number,
  oy: number,
  inset: number
): [number, number] {
  const dx = nx - ox
  const dy = ny - oy
  const norm = Math.hypot(dx, dy)
  if (norm < 1e-9) {
    return [nx, ny]
  }
  const cx = nx + (dx / norm) * inset
  const cy = ny + (dy / norm) * inset
  if (roi.contains(cx, cy)) {
    return [cx, cy]
  }
  return [nx, ny]
}

/**
 * Clamp one polyline's vertices into the ROI. Vertices already inside are
 * returned untouched with their exact original values.
 *
 * @param roi the mask
 * @param vertices the polyline's vertices
 * @param labelId owning label id, for the report
 * @param category owning label category, for the report
 * @param inset inward nudge in pixels
 */
export function clampVertices(
  roi: RoiMask,
  vertices: Array<[number, number]>,
  labelId: string = "",
  category: string = "",
  inset: number = DEFAULT_INSET
): [Array<[number, number]>, VertexCorrection[]] {
  const out: Array<[number, number]> = []
  const corrections: VertexCorrection[] = []
  vertices.forEach((vertex, index) => {
    const x = Number(vertex[0])
    const y = Number(vertex[1])
    if (roi.contains(x, y)) {
      out.push([x, y])
      return
    }
    const [nx, ny, distance] = roi.nearestInside(x, y)
    const corrected = insetPoint(roi, nx, ny, x, y, inset)
    out.push(corrected)
    corrections.push({
      labelId,
      category,
      vertexIndex: index,
      original: [x, y],
      corrected,
      distance
    })
  })
  return [out, corrections]
}

/**
 * Clamp every poly2d vertex across a frame's labels, in place. Only
 * coordinates change, and only on polylines that had a correction.
 *
 * @param roi the mask
 * @param labels the frame's labels
 * @param inset inward nudge in pixels
 */
export function clampLabels(
  roi: RoiMask,
  labels: LabelExport[],
  inset: number = DEFAULT_INSET
): ClampResult {
  const result: ClampResult = { totalVertices: 0, corrections: [] }
  for (const label of labels) {
    for (const poly of label.poly2d ?? []) {
      const vertices = poly.vertices ?? []
      result.totalVertices += vertices.length
      const [corrected, corrections] = clampVertices(
        roi,
        vertices,
        String(label.id ?? ""),
        String(label.category ?? ""),
        inset
      )
      if (corrections.length > 0) {
        poly.vertices = corrected
        result.corrections.push(...corrections)
      }
    }
  }
  return result
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/server/annotation_fix/clamp.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server/annotation_fix app/test/server/annotation_fix
git add app/src/server/annotation_fix/clamp.ts app/test/server/annotation_fix/clamp.test.ts
git commit -m "feat(annotation-fix): port ROI vertex clamping to TypeScript

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Core

**Files:**
- Create: `app/src/server/annotation_fix/core.ts`
- Test: `app/test/server/annotation_fix/core.test.ts`

**Interfaces:**
- Consumes: `computeRoiMask` (Task 2), `clampLabels`, `flaggedCorrections`, `correctionToDict` (Task 3), `connectLabels`, `connectionToDict` (Task 1).
- Produces: `Options`, `defaultOptions()`, `Frame = Partial<ItemExport>`, `Document = Frame[] | { frames?: Frame[] }`, `FrameReport`, `Report`, `reportToDict(report)`, `resolveImagePath(name, root)`, `processFrame(frame, options) => Promise<FrameReport>`, `processDocument(document, options) => Promise<[Document, Report]>`.

- [ ] **Step 1: Write the failing tests**

`app/test/server/annotation_fix/core.test.ts`:

```ts
/** @jest-environment node */
import * as fs from "fs"
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
const sharp = require("sharp") as typeof import("sharp")

let dir: string
beforeAll(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "annotation-fix-core-"))
  fs.mkdirSync(path.join(dir, "items"))
  // 30x10 canvas, imagery in x < 20.
  await sharp({
    create: { width: 30, height: 10, channels: 3, background: { r: 0, g: 0, b: 0 } }
  })
    .composite([
      {
        input: {
          create: { width: 20, height: 10, channels: 3, background: { r: 120, g: 120, b: 120 } }
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
  expect(resolveImagePath("items/frame.png", dir)).toBe(path.join(dir, "items/frame.png"))
  expect(resolveImagePath("elsewhere/frame.png", path.join(dir, "items"))).toBe(
    path.join(dir, "items", "frame.png")
  )
  expect(resolveImagePath("missing.png", dir)).toBeNull()
  expect(resolveImagePath("", dir)).toBeNull()
})

test("clamps then connects, deep-copies the input and keeps the bare-list shape", async () => {
  // Endpoint of a sits in the padding at x=24. Clamped it lands near x=17.5
  // (nearest inside 19, inset 1.5), 2.5 px from b's start at x=20 → merge.
  // Connected first, the 4 px gap (24→20) would also merge, but the
  // junction would then be pulled apart by the clamp; ordering is verified
  // through the final vertex positions.
  const frames: Frame[] = [
    {
      name: "items/frame.png",
      labels: [
        line("a", "lane", [[2, 5], [24, 5]]),
        line("b", "lane", [[20, 5], [18, 8]])
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
      "clamped", "connections", "error", "flagged", "imageFound", "labelsAfter",
      "labelsBefore", "merged", "name", "roiCoverage", "totalVertices"
    ].sort()
  )
})

test("a missing image skips clamping but still connects", async () => {
  const doc = {
    frames: [
      {
        name: "nowhere.png",
        labels: [
          line("a", "lane", [[0, 0], [100, 0]]),
          line("b", "lane", [[104, 0], [200, 0]])
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

test("stages can be switched off", async () => {
  const frames: Frame[] = [
    {
      name: "items/frame.png",
      labels: [line("a", "lane", [[2, 5], [24, 5]]), line("b", "lane", [[25, 5], [28, 8]])]
    }
  ]
  const [out] = await processDocument(frames, {
    ...defaultOptions(),
    imageRoot: dir,
    clamp: false,
    connect: false
  })
  expect((out as Frame[])[0].labels).toHaveLength(2)
  expect((out as Frame[])[0].labels?.[0].poly2d?.[0].vertices[1]).toEqual([24, 5])
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/server/annotation_fix/core.test.ts`
Expected: FAIL — cannot find module `core`.

- [ ] **Step 3: Implement `core.ts`**

```ts
/**
 * Correct a Scalabel annotation document: the entry point the worker calls.
 *
 * Stage order is deliberate: CLAMP FIRST, THEN CONNECT. Out-of-ROI vertices
 * are frequently line endpoints in the black padding, and endpoints are
 * exactly what auto-connect reasons about. Median spill (8 px) is the same
 * order as the connect tolerance (15 px), so connecting before clamping would
 * evaluate junctions at positions about to move, and clamping afterwards
 * could pull a merged junction apart.
 */

import * as fs from "fs"
import * as path from "path"

import { ItemExport } from "../../types/export"
import {
  connectionToDict,
  connectLabels,
  DEFAULT_MIN_ANGLE,
  DEFAULT_TOLERANCE
} from "./autoconnect"
import {
  clampLabels,
  correctionToDict,
  DEFAULT_FLAG_DISTANCE,
  DEFAULT_INSET,
  flaggedCorrections
} from "./clamp"
import { computeRoiMask, DEFAULT_THRESHOLD } from "./mask"

/** One frame of an export document. */
export type Frame = Partial<ItemExport>

/** A full export document or a bare list of frames. */
export type Document = Frame[] | { frames?: Frame[]; [key: string]: unknown }

/** Knobs for one run. */
export interface Options {
  /** run the ROI clamp stage */
  clamp: boolean
  /** run the auto-connect stage */
  connect: boolean
  /** black-padding threshold */
  threshold: number
  /** connect radius in image pixels */
  tolerance: number
  /** junction angle guard in degrees, 0 disables */
  minAngle: number
  /** inward nudge for clamped vertices */
  inset: number
  /** report corrections beyond this distance */
  flagDistance: number
  /** root for relative image paths */
  imageRoot: string
}

/** The measured-sane defaults. */
export function defaultOptions(): Options {
  return {
    clamp: true,
    connect: true,
    threshold: DEFAULT_THRESHOLD,
    tolerance: DEFAULT_TOLERANCE,
    minAngle: DEFAULT_MIN_ANGLE,
    inset: DEFAULT_INSET,
    flagDistance: DEFAULT_FLAG_DISTANCE,
    imageRoot: ""
  }
}

/** What happened to one frame. */
export interface FrameReport {
  /** frame name */
  name: string
  /** whether the image resolved */
  imageFound: boolean
  /** fraction of the canvas inside the ROI */
  roiCoverage: number | null
  /** vertices examined */
  totalVertices: number
  /** vertices moved */
  clamped: number
  /** serialised large corrections */
  flagged: Array<{ [key: string]: unknown }>
  /** merges applied */
  merged: number
  /** label count before */
  labelsBefore: number
  /** label count after */
  labelsAfter: number
  /** serialised connections */
  connections: Array<{ [key: string]: unknown }>
  /** clamp-stage error, if any */
  error: string | null
}

/** Aggregate outcome across every frame. */
export interface Report {
  /** per-frame reports */
  frames: FrameReport[]
}

/** Serialised report, matching the Python `Report.to_dict()` layout. */
export interface ReportDict {
  /** aggregate counts */
  summary: {
    /** frames processed */
    frames: number
    /** vertices moved */
    totalClamped: number
    /** merges applied */
    totalMerged: number
    /** corrections beyond the review threshold */
    totalFlagged: number
    /** frames whose image could not be located */
    missingImages: string[]
  }
  /** per-frame reports */
  frames: FrameReport[]
}

/**
 * Serialise the whole report.
 *
 * @param report the report
 */
export function reportToDict(report: Report): ReportDict {
  return {
    summary: {
      frames: report.frames.length,
      totalClamped: report.frames.reduce((n, f) => n + f.clamped, 0),
      totalMerged: report.frames.reduce((n, f) => n + f.merged, 0),
      totalFlagged: report.frames.reduce((n, f) => n + f.flagged.length, 0),
      missingImages: report.frames.filter((f) => !f.imageFound).map((f) => f.name)
    },
    frames: report.frames.map((f) => ({
      ...f,
      roiCoverage:
        f.roiCoverage === null ? null : Math.round(f.roiCoverage * 1e5) / 1e5
    }))
  }
}

/**
 * Locate a frame's image: as given, under the root, or by basename under the
 * root.
 *
 * @param name frame name
 * @param imageRoot root for relative names
 */
export function resolveImagePath(name: string, imageRoot: string): string | null {
  if (name === "") {
    return null
  }
  const candidates = [name]
  if (imageRoot !== "") {
    candidates.push(path.join(imageRoot, name))
    candidates.push(path.join(imageRoot, path.basename(name)))
  }
  for (const candidate of candidates) {
    try {
      if (fs.statSync(candidate).isFile()) {
        return candidate
      }
    } catch {
      // not there
    }
  }
  return null
}

/**
 * Correct a single frame in place. A frame whose image is missing still gets
 * the connect stage, which needs no pixels.
 *
 * @param frame the frame, mutated
 * @param options run options
 */
export async function processFrame(
  frame: Frame,
  options: Options
): Promise<FrameReport> {
  const name = String(frame.name ?? "")
  const labels = frame.labels ?? []
  const report: FrameReport = {
    name,
    imageFound: true,
    roiCoverage: null,
    totalVertices: 0,
    clamped: 0,
    flagged: [],
    merged: 0,
    labelsBefore: labels.length,
    labelsAfter: labels.length,
    connections: [],
    error: null
  }

  if (options.clamp) {
    const imagePath = resolveImagePath(name, options.imageRoot)
    if (imagePath === null) {
      report.imageFound = false
    } else {
      try {
        const roi = await computeRoiMask(imagePath, options.threshold)
        report.roiCoverage = roi.coverage
        const clampResult = clampLabels(roi, labels, options.inset)
        report.totalVertices = clampResult.totalVertices
        report.clamped = clampResult.corrections.length
        report.flagged = flaggedCorrections(
          clampResult,
          options.flagDistance
        ).map(correctionToDict)
      } catch (error) {
        report.error =
          error instanceof Error
            ? `${error.name}: ${error.message}`
            : String(error)
      }
    }
  }

  if (options.connect && labels.length > 0) {
    const [survivors, connectResult] = connectLabels(
      labels,
      options.tolerance,
      options.minAngle
    )
    frame.labels = survivors
    report.merged = connectResult.connections.length
    report.labelsAfter = connectResult.labelsAfter
    report.connections = connectResult.connections.map(connectionToDict)
  }

  return report
}

/**
 * Correct every frame in a document. Accepts a full document or a bare list
 * of frames and returns the same shape. The input is deep-copied.
 *
 * @param document the document
 * @param options run options
 */
export async function processDocument(
  document: Document,
  options: Options = defaultOptions()
): Promise<[Document, Report]> {
  const copy = JSON.parse(JSON.stringify(document)) as Document
  const frames: Frame[] = Array.isArray(copy) ? copy : copy.frames ?? []
  const report: Report = { frames: [] }
  for (const frame of frames) {
    report.frames.push(await processFrame(frame, options))
  }
  return [copy, report]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/server/annotation_fix/core.test.ts`
Expected: PASS (4 tests). Derivation of the expected values: nearest inside of (24,5) on a 20-wide region is (19,5), distance 5; inset 1.5 along (−1,0) gives (17.5,5); the gap to b's start (20,5) is 2.5 ≤ 15 so they merge, and `vertices[2]` is b's second vertex `[18,8]`.

- [ ] **Step 5: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server/annotation_fix app/test/server/annotation_fix
git add app/src/server/annotation_fix/core.ts app/test/server/annotation_fix/core.test.ts
git commit -m "feat(annotation-fix): document-level correction pipeline in TypeScript

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Worker entry and build

**Files:**
- Create: `app/src/server/annotation_fix/stdio.ts`
- Modify: `webpack.config.js:94-96`
- Test: `app/test/server/annotation_fix/stdio.test.ts`

**Interfaces:**
- Consumes: `processDocument`, `defaultOptions`, `reportToDict` (Task 4); `connectLabels` (Task 1); `computeRoiMask` (Task 2).
- Produces: `run(payload) => Promise<StdioResponse>`, `preflight() => Promise<boolean>`; compiled `app/dist/annotation_fix_worker.js` that reads stdin and writes stdout, or with `--preflight` prints diagnostics.

- [ ] **Step 1: Write the failing tests**

`app/test/server/annotation_fix/stdio.test.ts`:

```ts
/** @jest-environment node */
import { run } from "../../../src/server/annotation_fix/stdio"

test("a request without a document is rejected", async () => {
  expect(await run({})).toEqual({
    ok: false,
    error: "request is missing 'document'"
  })
})

test("a document round-trips with a report", async () => {
  const response = await run({
    document: [
      {
        name: "none.png",
        labels: [
          {
            id: "a",
            category: "curb_road_edge",
            poly2d: [{ vertices: [[0, 0], [100, 0]], types: "LL", closed: false }]
          },
          {
            id: "b",
            category: "without_curb_road_edge",
            poly2d: [{ vertices: [[101, 0], [200, 0]], types: "LL", closed: false }]
          }
        ]
      }
    ],
    image_root: "/nonexistent",
    clamp: false
  })
  expect(response.ok).toBe(true)
  if (response.ok) {
    expect((response.document as Array<{ labels: unknown[] }>)[0].labels).toHaveLength(1)
    expect(response.report.summary.totalMerged).toBe(1)
  }
})

test("a processing exception becomes ok:false", async () => {
  const response = await run({ document: [{ name: "x", labels: [null] }] })
  expect(response.ok).toBe(false)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx jest app/test/server/annotation_fix/stdio.test.ts`
Expected: FAIL — cannot find module `stdio`.

- [ ] **Step 3: Implement `stdio.ts`**

```ts
/**
 * Worker entry: one correction over a pipe. Request JSON on stdin, response
 * JSON on stdout.
 *
 * The server spawns this as a short-lived child per task: nothing to start by
 * hand, no port, no stale daemon holding old code. Stdout carries ONLY the
 * response; everything else goes to stderr.
 *
 * `--preflight` instead checks that sharp loads, decodes a PNG and that the
 * pipeline merges two touching road-edge lines, then exits 0 or 1.
 */

import * as fs from "fs"
import * as os from "os"
import * as path from "path"

import {
  defaultOptions,
  Document,
  Options,
  processDocument,
  ReportDict,
  reportToDict
} from "./core"
import { computeRoiMask } from "./mask"

/** What the server sends. */
export interface StdioRequest {
  /** frames or a full document */
  document?: unknown
  /** root for relative image paths */
  image_root?: string
  /** accepted for protocol compatibility; the JS engine has no mask cache */
  cache_dir?: string | null
  /** run the clamp stage */
  clamp?: boolean
  /** run the connect stage */
  connect?: boolean
  /** black-padding threshold */
  threshold?: number
  /** connect radius */
  tolerance?: number
  /** junction angle guard */
  min_angle?: number
  /** inward nudge */
  inset?: number
  /** review threshold */
  flag_distance?: number
}

/** What the worker answers. */
export type StdioResponse =
  | { ok: true; document: Document; report: ReportDict }
  | { ok: false; error: string }

/**
 * Apply the requested corrections to one payload.
 *
 * @param payload the parsed request
 */
export async function run(payload: StdioRequest): Promise<StdioResponse> {
  const document = payload.document
  if (document === undefined || document === null) {
    return { ok: false, error: "request is missing 'document'" }
  }
  const defaults = defaultOptions()
  const options: Options = {
    clamp: payload.clamp ?? defaults.clamp,
    connect: payload.connect ?? defaults.connect,
    threshold: Number(payload.threshold ?? defaults.threshold),
    tolerance: Number(payload.tolerance ?? defaults.tolerance),
    minAngle: Number(payload.min_angle ?? defaults.minAngle),
    inset: Number(payload.inset ?? defaults.inset),
    flagDistance: Number(payload.flag_distance ?? defaults.flagDistance),
    imageRoot: String(payload.image_root ?? "")
  }
  try {
    const [corrected, report] = await processDocument(
      document as Document,
      options
    )
    return { ok: true, document: corrected, report: reportToDict(report) }
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof Error ? `${error.name}: ${error.message}` : String(error)
    }
  }
}

/**
 * Read all of stdin.
 */
async function readStdin(): Promise<string> {
  const chunks: Buffer[] = []
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk))
  }
  return Buffer.concat(chunks).toString("utf8")
}

/**
 * Check the pieces the correction depends on and report each.
 */
export async function preflight(): Promise<boolean> {
  let ok = true
  const line = (good: boolean, msg: string): void => {
    ok = ok && good
    process.stdout.write(`  ${good ? "OK  " : "FAIL"}  ${msg}\n`)
  }
  process.stdout.write("Scalabel annotation auto-correct preflight (JS)\n\n")

  let sharpVersion = "?"
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const sharp = require("sharp") as typeof import("sharp")
    sharpVersion = sharp.versions.sharp
    line(true, `sharp ${sharpVersion} (libvips ${sharp.versions.vips})`)
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "annotation-fix-preflight-"))
    const file = path.join(dir, "probe.png")
    await sharp({
      create: { width: 8, height: 8, channels: 3, background: { r: 0, g: 0, b: 0 } }
    })
      .composite([
        {
          input: {
            create: { width: 4, height: 4, channels: 3, background: { r: 200, g: 200, b: 200 } }
          },
          left: 2,
          top: 2
        }
      ])
      .png()
      .toFile(file)
    const roi = await computeRoiMask(file)
    fs.rmSync(dir, { recursive: true, force: true })
    line(Math.abs(roi.coverage - 0.25) < 1e-9, "decode a PNG and build its ROI mask")
  } catch (error) {
    line(false, `sharp unavailable: ${error instanceof Error ? error.message : String(error)}`)
  }

  const response = await run({
    document: [
      {
        name: "preflight.png",
        labels: [
          {
            id: "a",
            category: "curb_road_edge",
            poly2d: [{ vertices: [[0, 0], [100, 0]], types: "LL", closed: false }]
          },
          {
            id: "b",
            category: "without_curb_road_edge",
            poly2d: [{ vertices: [[101, 0], [200, 0]], types: "LL", closed: false }]
          }
        ]
      }
    ],
    clamp: false
  })
  const merged =
    response.ok &&
    (response.document as Array<{ labels: unknown[] }>)[0].labels.length === 1
  line(merged, "end-to-end: two touching road-edge lines merged into one")

  process.stdout.write(
    ok
      ? "\nRESULT: auto-correction will work on this machine.\n"
      : "\nRESULT: auto-correction will SILENTLY SKIP on this machine.\nProjects will still be created — with UNCORRECTED annotations.\n"
  )
  return ok
}

/**
 * Entry point.
 */
async function main(): Promise<number> {
  if (process.argv.includes("--preflight")) {
    return (await preflight()) ? 0 : 1
  }

  let payload: StdioRequest
  try {
    payload = JSON.parse(await readStdin()) as StdioRequest
  } catch (error) {
    process.stdout.write(
      JSON.stringify({
        ok: false,
        error: `invalid request JSON: ${error instanceof Error ? error.message : String(error)}`
      })
    )
    return 1
  }

  const response = await run(payload)
  process.stdout.write(JSON.stringify(response))
  return response.ok ? 0 : 1
}

// webpack rewrites `require.main`, so guard on the Jest env instead: the
// tests import `run` from this module and must not start reading stdin.
if (process.env.JEST_WORKER_ID === undefined) {
  main().then(
    (code) => {
      // Let the pipe drain before exiting.
      process.stdout.write("", () => process.exit(code))
    },
    (error) => {
      process.stderr.write(`${String(error)}\n`)
      process.exit(1)
    }
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx jest app/test/server/annotation_fix/stdio.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Add the webpack entry**

In `webpack.config.js`, change the server entry block:

```js
  entry: {
    main: __dirname + '/app/src/server/main.ts',
    annotation_fix_worker: __dirname + '/app/src/server/annotation_fix/stdio.ts',
  },
```

- [ ] **Step 6: Build and run the compiled worker**

Run:
```bash
npm run build
ls app/dist/annotation_fix_worker.js
node app/dist/annotation_fix_worker.js --preflight
echo '{"document":[{"name":"x.png","labels":[]}],"clamp":false}' | node app/dist/annotation_fix_worker.js
```
Expected: the file exists; preflight prints three `OK` lines and `RESULT: auto-correction will work`; the piped request prints `{"ok":true,"document":[{"name":"x.png","labels":[]}],"report":{"summary":{"frames":1,...}}}` and nothing else on stdout.

- [ ] **Step 7: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server/annotation_fix app/test/server/annotation_fix
git add app/src/server/annotation_fix/stdio.ts app/test/server/annotation_fix/stdio.test.ts webpack.config.js
git commit -m "feat(annotation-fix): stdio worker entry compiled to app/dist/annotation_fix_worker.js

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Switch the server to the Node worker

**Files:**
- Modify: `app/src/server/annotation_fix.ts` (header comment, lines 26-128, 169-181, 288-341)
- Modify: `Dockerfile`
- Modify: `tools/annotation_fix/stdio.py:1-8`, `tools/annotation_fix/api.py:1-11`, `tools/annotation_fix/core.py:1-6`, `tools/annotation_fix/README.md` (top), `tools/requirements.txt:1-10`

**Interfaces:**
- Consumes: `app/dist/annotation_fix_worker.js` (Task 5).
- Produces: `getWorkerScript(): string`; `correctAnnotations` unchanged in signature.

- [ ] **Step 1: Rewrite the top of `annotation_fix.ts`**

Replace everything from the file header through `getImageRoot` (lines 1-144) with:

```ts
/**
 * Runs the annotation-fix corrections at project creation, when the
 * "Auto-correct annotations" box is ticked.
 *
 * ROI clamp: the orthomosaic images pad a narrow captured footprint into a
 * large rectangle with black. Vertices that drift into the padding are pulled
 * back to the nearest valid pixel.
 *
 * Auto-connect: polyline endpoints of compatible categories that nearly touch
 * are spliced into one label, the batch equivalent of the editor's
 * drag-endpoint-onto-endpoint gesture.
 *
 * The engine lives in ./annotation_fix and is compiled by webpack into
 * app/dist/annotation_fix_worker.js. It runs as a SHORT-LIVED CHILD PROCESS
 * of the same Node binary for the duration of one task rather than as a
 * standing service: nothing to start by hand, no port to collide with, no
 * stale daemon holding old code, and a crash or OOM in the child cannot take
 * the server down. The request goes in on stdin and the corrected document
 * comes back on stdout.
 *
 * Correction is best-effort. If the worker script is missing, sharp fails to
 * load, or the child fails for any reason, the ORIGINAL annotations are
 * returned and the reason is logged. Failing an entire import because a
 * helper broke would be the wrong trade.
 */

import { spawn } from "child_process"
import * as path from "path"

import { ItemExport } from "../types/export"
import Logger from "./logger"

/** How long the child gets before it is killed. */
const DEFAULT_TIMEOUT_MS = 1800000

/** Stdout cap. Corrected documents for a large batch run to tens of MB. */
const MAX_OUTPUT_BYTES = 1024 * 1024 * 1024

/** Log hint appended to every skip. */
const DIAGNOSE_HINT =
  "Annotation auto-correct SKIPPED — the project was created with UNCORRECTED annotations. Diagnose with: node app/dist/annotation_fix_worker.js --preflight"

/**
 * Summary of what the corrections changed, for the server log.
 */
export interface AnnotationFixSummary {
  /** number of frames processed */
  frames: number
  /** vertices pulled back inside the region of interest */
  totalClamped: number
  /** polyline pairs joined into one label */
  totalMerged: number
  /** corrections large enough to warrant a human look */
  totalFlagged: number
  /** frames whose image could not be found */
  missingImages: string[]
}

/**
 * Options for one correction run.
 */
export interface AnnotationFixOptions {
  /** path to the compiled worker script */
  workerScript?: string
  /** prefix used to resolve the relative image paths in frame names */
  imageRoot?: string
  /** how long to allow before killing the child */
  timeoutMs?: number
}

/**
 * The compiled worker script. Webpack emits it beside main.js, so it is found
 * relative to this bundle; SCALABEL_ANNOTATION_FIX_WORKER overrides that for
 * unusual layouts.
 */
export function getWorkerScript(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_WORKER
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return path.join(__dirname, "annotation_fix_worker.js")
}

/**
 * Root the corrections use to resolve relative image paths.
 *
 * Frame names are stored relative to the data root, so this must be an
 * absolute path: a relative one would resolve against the child's working
 * directory and every image would come back not-found, silently skipping the
 * clamp stage while auto-connect still ran.
 */
export function getImageRoot(): string {
  const configured = process.env.SCALABEL_ANNOTATION_FIX_IMAGE_ROOT
  if (configured !== undefined && configured !== "") {
    return path.resolve(configured)
  }
  return path.join(process.cwd(), "local-data")
}
```

- [ ] **Step 2: Change the spawn**

In `runCorrector`, replace

```ts
  const python = options.python ?? getPythonExecutable()
  const toolsDir = options.toolsDir ?? getToolsDir()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS

  return await new Promise<FixResponse>((resolve, reject) => {
    const child = spawn(python, ["-m", "annotation_fix.stdio"], {
      cwd: toolsDir,
      // The package lives in toolsDir, so make it importable from there.
      env: { ...process.env, PYTHONPATH: toolsDir }
    })
```

with

```ts
  const workerScript = options.workerScript ?? getWorkerScript()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS

  return await new Promise<FixResponse>((resolve, reject) => {
    // Same binary as the server, so whatever ran main.js can run the worker.
    const child = spawn(process.execPath, [workerScript], {
      env: process.env,
      windowsHide: true
    })
```

- [ ] **Step 3: Replace the three duplicated hint strings**

In `correctAnnotations`, replace each of the three occurrences of

```ts
      Logger.warning(
        "Annotation auto-correct SKIPPED — the project was created with UNCORRECTED annotations. Diagnose with: python3 tools/annotation_fix/preflight.py"
      )
```

with

```ts
      Logger.warning(DIAGNOSE_HINT)
```

Also remove `cache_dir: options.cacheDir ?? null,` from the request object — the JS worker ignores it and the option no longer exists. Confirm `spawnSync` is no longer imported.

- [ ] **Step 4: Type-check and run the full server test suite**

Run: `npx tsc --noEmit -p tsconfig.json && npx jest app/test/server/annotation_fix`
Expected: no type errors (unused `spawnSync`/`resolvedPython` would fail `noUnusedLocals`); all annotation_fix tests pass.

- [ ] **Step 5: Update the Dockerfile**

Replace the whole file with:

```dockerfile
FROM node:20-bookworm-slim

EXPOSE 8686

WORKDIR /opt/scalabel

# Annotation auto-correct runs in a Node child process
# (app/dist/annotation_fix_worker.js) using sharp's prebuilt libvips binary,
# which npm install fetches. No Python is needed in this image.

COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --ignore-scripts; else npm install --ignore-scripts; fi

COPY . .

RUN npm run build && rm -f app/dist/tsconfig.tsbuildinfo

# Fail the build rather than ship an image whose auto-correction silently skips.
RUN node app/dist/annotation_fix_worker.js --preflight

CMD ["node", "--max-old-space-size=8192", "app/dist/main.js", "--config", "./local-data/scalabel/config.yml"]
```

Note: `npm ci --ignore-scripts` skips sharp's install script. sharp 0.35 ships its binary as an optional platform package (`@img/sharp-linux-x64`) resolved by npm itself, so no script is required; the preflight line proves it loaded.

- [ ] **Step 6: Mark the Python package as standalone**

`tools/annotation_fix/stdio.py` lines 1-8 → 

```python
"""Run one correction over a pipe: request JSON on stdin, response on stdout.

NOTE: the Scalabel server no longer spawns this. It runs the TypeScript port
in app/src/server/annotation_fix (compiled to app/dist/annotation_fix_worker.js).
This module remains as the reference implementation and as a standalone tool;
the request/response protocol below is shared with the JS worker.
```

`tools/annotation_fix/api.py` lines 1-7 →

```python
"""Optional HTTP service wrapper.

    uvicorn annotation_fix.api:app --port 8687

Not used by the Scalabel server, which runs its own TypeScript port of this
engine in a child process. Kept for ad-hoc or external use.
```

`tools/annotation_fix/core.py` line 4 → `The CLI, the stdio module and the HTTP service are thin wrappers over ``process_document``.`

`tools/requirements.txt` lines 1-10 →

```
# Dependencies for the STANDALONE Python annotation-fix tool
# (tools/annotation_fix). The Scalabel server does NOT use this package any
# more: it runs a TypeScript port in a Node child process, so nothing here is
# needed to make the "Auto-correct annotations" checkbox work.
#
#   pip install -r tools/requirements.txt
#
# Check the Python tool with:
#
#   python3 tools/annotation_fix/preflight.py
```

`tools/annotation_fix/README.md`: add as the first paragraph after the title:

```
> **Note:** the Scalabel server uses a TypeScript port of this engine
> (`app/src/server/annotation_fix`), compiled to
> `app/dist/annotation_fix_worker.js`. This Python package is the reference
> implementation and a standalone CLI; keep both in sync when changing rules.
```

- [ ] **Step 7: Full build and manual acceptance**

Run:
```bash
npm run build
node app/dist/annotation_fix_worker.js --preflight
```
Then start Redis and the server with the usual command, create a project with "Auto-correct annotations" ticked using a real item file whose images live under `local-data`, and confirm the server log shows `Annotation auto-correct: N vertices clamped, M lines merged across K frames` followed by `task 000000 ready`, and that the dashboard's task link re-enables without a reload.

- [ ] **Step 8: Lint and commit**

```bash
npx eslint -c .eslintrc.json --ext .ts app/src/server
git add app/src/server/annotation_fix.ts Dockerfile tools/annotation_fix/stdio.py tools/annotation_fix/api.py tools/annotation_fix/core.py tools/annotation_fix/README.md tools/requirements.txt
git commit -m "feat(annotation-fix): server spawns the Node worker; Python no longer required

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Parity harness

**Files:**
- Create: `tools/annotation_fix_parity.js`

**Interfaces:**
- Consumes: `app/dist/annotation_fix_worker.js`, `tools/annotation_fix/stdio.py`.

- [ ] **Step 1: Write the script**

```js
#!/usr/bin/env node
/* eslint-disable */
/**
 * Run the Python and JS annotation-fix engines on the same document and diff
 * the results.
 *
 *   node tools/annotation_fix_parity.js <export.json> [imageRoot] [--python python]
 *
 * Exit 0 when reports agree and every vertex matches to 1e-6; exit 1 with a
 * list of differences otherwise. Sub-pixel differences from nearest-pixel
 * tie-breaks are expected and are listed, not hidden.
 */
const { spawnSync } = require("child_process")
const fs = require("fs")
const path = require("path")

const args = process.argv.slice(2)
const pyFlag = args.indexOf("--python")
const python = pyFlag >= 0 ? args.splice(pyFlag, 2)[1] : "python"
const [docPath, imageRootArg] = args
if (!docPath) {
  console.error("usage: node tools/annotation_fix_parity.js <export.json> [imageRoot] [--python python]")
  process.exit(2)
}
const imageRoot = path.resolve(imageRootArg || "local-data")
const document = JSON.parse(fs.readFileSync(docPath, "utf8"))
const request = JSON.stringify({ document, image_root: imageRoot, clamp: true, connect: true })

function runEngine(cmd, cmdArgs, opts) {
  const proc = spawnSync(cmd, cmdArgs, { input: request, maxBuffer: 1 << 30, encoding: "utf8", ...opts })
  if (proc.error) throw proc.error
  if (proc.stderr) process.stderr.write(proc.stderr)
  return JSON.parse(proc.stdout)
}

const toolsDir = path.join(__dirname)
const py = runEngine(python, ["-m", "annotation_fix.stdio"], { cwd: toolsDir, env: { ...process.env, PYTHONPATH: toolsDir } })
const js = runEngine(process.execPath, [path.join(__dirname, "..", "app", "dist", "annotation_fix_worker.js")])

const diffs = []
function framesOf(doc) { return Array.isArray(doc) ? doc : doc.frames || [] }

if (!py.ok || !js.ok) {
  diffs.push(`ok: python=${py.ok} (${py.error || ""}) js=${js.ok} (${js.error || ""})`)
} else {
  const sp = py.report.summary, sj = js.report.summary
  for (const k of Object.keys(sp)) {
    if (JSON.stringify(sp[k]) !== JSON.stringify(sj[k])) diffs.push(`summary.${k}: python=${JSON.stringify(sp[k])} js=${JSON.stringify(sj[k])}`)
  }
  const fp = framesOf(py.document), fj = framesOf(js.document)
  fp.forEach((frameP, i) => {
    const frameJ = fj[i]
    const rp = py.report.frames[i], rj = js.report.frames[i]
    for (const k of ["clamped", "merged", "labelsAfter", "imageFound"]) {
      if (rp[k] !== rj[k]) diffs.push(`${frameP.name} ${k}: python=${rp[k]} js=${rj[k]}`)
    }
    if (JSON.stringify(rp.connections) !== JSON.stringify(rj.connections)) diffs.push(`${frameP.name} connections differ`)
    const lp = frameP.labels || [], lj = (frameJ && frameJ.labels) || []
    if (lp.length !== lj.length) { diffs.push(`${frameP.name} label count python=${lp.length} js=${lj.length}`); return }
    lp.forEach((labelP, li) => {
      const labelJ = lj[li]
      if (String(labelP.id) !== String(labelJ.id)) diffs.push(`${frameP.name} label ${li} id python=${labelP.id} js=${labelJ.id}`)
      ;(labelP.poly2d || []).forEach((polyP, pi) => {
        const polyJ = (labelJ.poly2d || [])[pi]
        if (!polyJ || polyP.types !== polyJ.types || polyP.vertices.length !== polyJ.vertices.length) { diffs.push(`${frameP.name} label ${labelP.id} poly ${pi} shape differs`); return }
        polyP.vertices.forEach((v, vi) => {
          const w = polyJ.vertices[vi]
          const d = Math.hypot(v[0] - w[0], v[1] - w[1])
          if (d > 1e-6) diffs.push(`${frameP.name} label ${labelP.id} v${vi}: python=[${v}] js=[${w}] (${d.toFixed(3)} px)`)
        })
      })
    })
  })
}

console.log(`python: ${JSON.stringify(py.ok ? py.report.summary : py.error)}`)
console.log(`js:     ${JSON.stringify(js.ok ? js.report.summary : js.error)}`)
if (diffs.length === 0) { console.log("PARITY: identical"); process.exit(0) }
console.log(`PARITY: ${diffs.length} difference(s)`)
for (const d of diffs) console.log("  " + d)
process.exit(1)
```

- [ ] **Step 2: Run it on real data**

Run (with a Scalabel export JSON whose frame names resolve under `local-data`):
```bash
node tools/annotation_fix_parity.js <path-to-export.json> local-data
```
Expected: both summaries printed; `PARITY: identical`, or a short list consisting only of sub-pixel vertex lines (≤ 1.5 px, from nearest-pixel tie-breaks). Any `merged`, `clamped`, `connections` or label-count difference is a bug to fix before finishing.

- [ ] **Step 3: Commit**

```bash
git add tools/annotation_fix_parity.js
git commit -m "chore(annotation-fix): parity harness comparing the Python and JS engines

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
