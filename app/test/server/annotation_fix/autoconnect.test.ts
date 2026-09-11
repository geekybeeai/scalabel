/** @jest-environment node */
import {
  connectLabels,
  splice
} from "../../../src/server/annotation_fix/autoconnect"
import { LabelExport } from "../../../src/types/export"

/**
 * Build an open polyline label.
 *
 * @param id label id
 * @param category label category
 * @param vertices vertices
 * @param types per-vertex types
 * @param closed closed flag
 */
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
  const a: Array<[number, number]> = [
    [0, 0],
    [10, 0]
  ]
  const b: Array<[number, number]> = [
    [10, 0],
    [20, 0]
  ]

  test("A.end -> B.start drops B's junction vertex", () => {
    expect(splice(a, "LC", false, b, "CL", true)).toEqual([
      [
        [0, 0],
        [10, 0],
        [20, 0]
      ],
      "LCL"
    ])
  })
  test("A.start -> B.end prepends B without its last vertex", () => {
    expect(splice(b, "CL", true, a, "LC", false)).toEqual([
      [
        [0, 0],
        [10, 0],
        [20, 0]
      ],
      "LCL"
    ])
  })
  test("A.start -> B.start reverses A", () => {
    const rev: Array<[number, number]> = [
      [10, 0],
      [0, 0]
    ]
    expect(splice(rev, "CL", true, b, "CL", true)).toEqual([
      [
        [0, 0],
        [10, 0],
        [20, 0]
      ],
      "LCL"
    ])
  })
  test("A.end -> B.end reverses B", () => {
    const revB: Array<[number, number]> = [
      [20, 0],
      [10, 0]
    ]
    expect(splice(a, "LC", false, revB, "LC", false)).toEqual([
      [
        [0, 0],
        [10, 0],
        [20, 0]
      ],
      "LCL"
    ])
  })
})

describe("connectLabels", () => {
  test("merges same-category lines within tolerance, keeps lower index", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 0],
        [200, 0]
      ])
    ]
    const [out, result] = connectLabels(labels)
    expect(out).toHaveLength(1)
    expect(out[0].id).toBe("a")
    expect(out[0].poly2d?.[0].vertices).toEqual([
      [0, 0],
      [100, 0],
      [200, 0]
    ])
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
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [116, 0],
        [200, 0]
      ])
    ]
    const [out, result] = connectLabels(labels)
    expect(out).toHaveLength(2)
    expect(result.connections).toHaveLength(0)
  })

  test("allows the listed cross-category pair only", () => {
    const allowed = [
      line("a", "curb_road_edge", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "without_curb_road_edge", [
        [101, 0],
        [200, 0]
      ])
    ]
    expect(connectLabels(allowed)[0]).toHaveLength(1)

    const refused = [
      line("a", "white_line", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "yellow_line", [
        [101, 0],
        [200, 0]
      ])
    ]
    expect(connectLabels(refused)[0]).toHaveLength(2)
  })

  test("resolves chains across passes and consumes each endpoint once", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [103, 0],
        [200, 0]
      ]),
      line("c", "lane", [
        [204, 0],
        [300, 0]
      ])
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
      line("far", "lane", [
        [-100, 0],
        [-6, 0]
      ]),
      line("hub", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("near", "lane", [
        [-2, 0],
        [-2, -100]
      ])
    ]
    const [out, result] = connectLabels(labels)
    // hub.start joins near.start (gap 2). far.end then has no free endpoint
    // within 15 px: the junction it was close to is consumed.
    expect(result.connections[0]).toMatchObject({
      keptId: "hub",
      absorbedId: "near",
      gap: 2
    })
    expect(out.map((l) => l.id)).toEqual(["far", "hub"])
  })
})

describe("connectLabels eligibility and guards", () => {
  test("ignores closed rings, multi-polygon labels and single vertices", () => {
    const labels: LabelExport[] = [
      line(
        "closed",
        "lane",
        [
          [0, 0],
          [100, 0],
          [50, 50]
        ],
        "LLL",
        true
      ),
      line("open", "lane", [
        [101, 0],
        [200, 0]
      ]),
      {
        ...line("multi", "lane", [
          [201, 0],
          [300, 0]
        ]),
        poly2d: [
          {
            vertices: [
              [201, 0],
              [300, 0]
            ],
            types: "LL",
            closed: false
          },
          {
            vertices: [
              [400, 0],
              [500, 0]
            ],
            types: "LL",
            closed: false
          }
        ]
      },
      line("dot", "lane", [[301, 0]], "L")
    ]
    const [out] = connectLabels(labels)
    expect(out).toHaveLength(4)
  })

  test("minAngle rejects a sharp fork and accepts a straight continuation", () => {
    const straight = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 0],
        [200, 0]
      ])
    ]
    const [outStraight, resultStraight] = connectLabels(straight, 15, 150)
    expect(outStraight).toHaveLength(1)
    expect(resultStraight.connections[0].angle).toBeCloseTo(180, 5)

    const fork = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 0],
        [50, 80]
      ])
    ]
    expect(connectLabels(fork, 15, 150)[0]).toHaveLength(2)
  })

  test("does not mutate labels that were not merged", () => {
    const untouched = line("solo", "lane", [
      [0, 0],
      [100, 0]
    ])
    const before = JSON.stringify(untouched)
    connectLabels([untouched])
    expect(JSON.stringify(untouched)).toBe(before)
  })
})
