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

/**
 * A deliberately vertical-tangent Bezier bridge between horizontal lines.
 * The guarded pairwise algorithm rejects each 90-degree local junction.
 *
 * @param gap endpoint gap on both sides of the curve
 */
function bridge(gap: number): LabelExport[] {
  return [
    line("left", "lane", [
      [0, 0],
      [70 - gap, 0]
    ]),
    line(
      "curve",
      "lane",
      [
        [70, 0],
        [70, 30],
        [130, 30],
        [130, 0]
      ],
      "LCCL"
    ),
    line("right", "lane", [
      [130 + gap, 0],
      [200, 0]
    ])
  ]
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
    const [out, result] = connectLabels(labels, 15, 0)
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
        [141, 0],
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
    const [out, result] = connectLabels(labels, 15, 0)
    // hub.start joins near.start (gap 2). far.end then has no free endpoint
    // within 15 px: the junction it was close to is consumed.
    expect(result.connections[0]).toMatchObject({
      keptId: "hub",
      absorbedId: "near",
      gap: 2
    })
    expect(out.map((l) => l.id)).toEqual(["far", "hub"])
  })

  test("standalone defaults retain the 15 px distance-only behavior", () => {
    const sharp = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [110, 0],
        [110, 100]
      ])
    ]
    const beyondTolerance = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [116, 0],
        [200, 0]
      ])
    ]

    expect(connectLabels(sharp)[0]).toHaveLength(1)
    expect(connectLabels(beyondTolerance)[0]).toHaveLength(2)
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

  test("minAngle accepts a gentle continuation whose gap follows both tangents", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 2],
        [199, 36]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(1)
  })
})

describe("atomic curve bridges", () => {
  test.each([0, 5, 10, 25, 40])(
    "connects a complete line-curve-line component across a %d px gap",
    (gap) => {
      const [out, result] = connectLabels(bridge(gap), 40, 150)

      expect(out).toHaveLength(1)
      expect(out[0].id).toBe("left")
      expect(result.connections).toHaveLength(2)
    }
  )

  test("does not connect a bridge beyond the 40 px tolerance", () => {
    expect(connectLabels(bridge(41), 40, 150)[0]).toHaveLength(3)
  })

  test("connects a bridge regardless of every label's drawing direction", () => {
    const labels = bridge(10).map((label) => {
      const poly = label.poly2d?.[0]
      if (poly !== undefined) {
        poly.vertices.reverse()
        poly.types = poly.types.split("").reverse().join("")
      }
      return label
    })

    const [out] = connectLabels(labels, 40, 150)

    expect(out).toHaveLength(1)
    expect(out[0].poly2d?.[0]).toMatchObject({
      vertices: [
        [200, 0],
        [130, 0],
        [130, 30],
        [70, 30],
        [60, 0],
        [0, 0]
      ],
      types: "LLCCLL"
    })
  })

  test("does not reverse a survivor that joins the bridge start-to-start", () => {
    const labels = bridge(10)
    const left = labels[0].poly2d?.[0]
    if (left !== undefined) {
      left.vertices.reverse()
      left.types = left.types.split("").reverse().join("")
    }

    const [out] = connectLabels(labels, 40, 150)

    expect(out).toHaveLength(1)
    expect(out[0].poly2d?.[0].vertices).toEqual([
      [200, 0],
      [130, 0],
      [130, 30],
      [70, 30],
      [60, 0],
      [0, 0]
    ])
  })

  test("finds curve controls and tangents past duplicate coordinates", () => {
    const labels = [
      line("left", "lane", [
        [0, 0],
        [60, 0],
        [60, 0]
      ]),
      line(
        "curve",
        "lane",
        [
          [70, 0],
          [70, 0],
          [70, 30],
          [130, 30],
          [130, 0],
          [130, 0]
        ],
        "LLCCLL"
      ),
      line("right", "lane", [
        [140, 0],
        [140, 0],
        [200, 0]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(1)
  })
})

describe("atomic curve bridge graph", () => {
  test("connects a maximal line-curve-line-curve-line component", () => {
    const labels = [
      ...bridge(10),
      line(
        "curve-2",
        "lane",
        [
          [210, 0],
          [210, 30],
          [270, 30],
          [270, 0]
        ],
        "LCCL"
      ),
      line("far-right", "lane", [
        [280, 0],
        [340, 0]
      ])
    ]
    const right = labels[2].poly2d?.[0]
    if (right !== undefined) {
      right.vertices[1] = [200, 0]
    }

    const [out, result] = connectLabels(labels, 40, 150)

    expect(out).toHaveLength(1)
    expect(result.connections.map(({ absorbedId }) => absorbedId)).toEqual([
      "curve",
      "right",
      "curve-2",
      "far-right"
    ])
    expect(out[0].poly2d?.[0].types).toBe("LLCCLLCCLL")
  })

  test("leaves an incomplete bridge untouched", () => {
    expect(connectLabels(bridge(10).slice(0, 2), 40, 150)[0]).toHaveLength(2)
  })

  test("requires reciprocal-nearest endpoint matches", () => {
    const labels = bridge(10)
    labels.splice(
      1,
      0,
      line(
        "closer-curve",
        "lane",
        [
          [65, 2.5],
          [65, 32.5],
          [300, 30],
          [300, 0]
        ],
        "LCCL"
      )
    )

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(4)
  })

  test("breaks exact endpoint ties by original label index", () => {
    const labels = [
      line("chosen", "lane", [
        [0, 0],
        [60, 0]
      ]),
      line("unchosen", "lane", [
        [0, 20],
        [60, 0]
      ]),
      ...bridge(10).slice(1)
    ]

    const [out, result] = connectLabels(labels, 40, 150)

    expect(out.map(({ id }) => id)).toEqual(["chosen", "unchosen"])
    expect(result.connections.map(({ absorbedId }) => absorbedId)).toEqual([
      "curve",
      "right"
    ])
  })

  test("breaks a same-label distance tie by endpoint side", () => {
    const labels = [
      line("double-ended", "lane", [
        [60, 0],
        [-100, 0],
        [60, 0]
      ]),
      ...bridge(10).slice(1)
    ]

    const [out] = connectLabels(labels, 40, 150)

    expect(out).toHaveLength(1)
    expect(out[0].poly2d?.[0].vertices).toEqual([
      [200, 0],
      [130, 0],
      [130, 30],
      [70, 30],
      [60, 0],
      [-100, 0],
      [60, 0]
    ])
  })
})

describe("atomic curve bridge topology and output", () => {
  test("rejects a two-label self-cycle", () => {
    const labels = [
      line(
        "curve",
        "lane",
        [
          [70, 0],
          [70, 30],
          [130, 30],
          [130, 0]
        ],
        "LCCL"
      ),
      line("loop", "lane", [
        [60, 0],
        [0, 0],
        [0, 100],
        [200, 100],
        [200, 0],
        [140, 0]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(2)
  })

  test("rejects a cyclic curve-bridge component", () => {
    const labels = [
      line(
        "curve-a",
        "lane",
        [
          [100, 100],
          [100, 70],
          [100, 30],
          [100, 0]
        ],
        "LCCL"
      ),
      line("bottom", "lane", [
        [110, 0],
        [190, 0]
      ]),
      line(
        "curve-b",
        "lane",
        [
          [200, 0],
          [200, 30],
          [200, 70],
          [200, 100]
        ],
        "LCCL"
      ),
      line("top", "lane", [
        [190, 100],
        [110, 100]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(4)
  })

  test("requires both sides of a bridge to be category-compatible", () => {
    const labels = bridge(10)
    labels[2].category = "other"

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(3)
  })

  test("rejects offset and forked external approaches", () => {
    const parallel = bridge(10)
    const parallelLeft = parallel[0].poly2d?.[0]
    if (parallelLeft !== undefined) {
      parallelLeft.vertices = [
        [0, 20],
        [60, 20]
      ]
    }
    const fork = bridge(10)
    const forkLeft = fork[0].poly2d?.[0]
    if (forkLeft !== undefined) {
      forkLeft.vertices = [
        [0, -60],
        [60, 0]
      ]
    }

    expect(connectLabels(parallel, 40, 150)[0]).toHaveLength(3)
    expect(connectLabels(fork, 40, 150)[0]).toHaveLength(3)
  })

  test("preserves the lowest-index survivor's metadata, orientation and reports", () => {
    const [left, curve, right] = bridge(10)
    curve.attributes = { source: "curve-survivor" }
    curve.manualShape = false
    const originalPoly = curve.poly2d?.[0]

    const [out, result] = connectLabels([curve, right, left], 40, 150)

    expect(out).toEqual([curve])
    expect(out[0]).toMatchObject({
      id: "curve",
      attributes: { source: "curve-survivor" },
      manualShape: false
    })
    expect(originalPoly?.types).toBe("LLCCLL")
    expect(originalPoly?.vertices).toEqual([
      [0, 0],
      [70, 0],
      [70, 30],
      [130, 30],
      [130, 0],
      [200, 0]
    ])
    expect(result).toMatchObject({ labelsBefore: 3, labelsAfter: 1 })
    expect(result.connections).toHaveLength(2)
    expect(result.connections).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          keptId: "curve",
          absorbedId: "left",
          category: "lane",
          junction: [70, 0],
          gap: 10
        }),
        expect.objectContaining({
          keptId: "curve",
          absorbedId: "right",
          category: "lane",
          junction: [130, 0],
          gap: 10
        })
      ])
    )
  })

  test("minAngle zero bypasses atomic matching for legacy distance order", () => {
    const labels = bridge(10)
    labels.splice(
      1,
      0,
      line("closer", "lane", [
        [64, 0],
        [64, 50]
      ])
    )

    const [out, result] = connectLabels(labels, 40, 0)

    expect(out.map(({ id }) => id)).toEqual(["left", "curve"])
    expect(result.connections[0]).toMatchObject({
      keptId: "left",
      absorbedId: "closer",
      gap: 4
    })
    expect(result.connections.every(({ angle }) => angle === undefined)).toBe(
      true
    )
  })
})

describe("curve seam guard", () => {
  test("minAngle accepts a line joining a curve when the resulting seam is smooth", () => {
    const labels = [
      line("line", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line(
        "curve",
        "lane",
        [
          [108, 12],
          [140, 0],
          [160, 0],
          [200, 0]
        ],
        "LCCL"
      )
    ]

    const [out] = connectLabels(labels, 40, 150)

    expect(out).toHaveLength(1)
    expect(out[0].poly2d?.[0]).toMatchObject({
      vertices: [
        [0, 0],
        [100, 0],
        [140, 0],
        [160, 0],
        [200, 0]
      ],
      types: "LLCCL"
    })
  })

  test("minAngle joins a curve across a five-pixel sampling gap", () => {
    const labels = [
      line("line", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line(
        "curve",
        "lane",
        [
          [103, 4],
          [108, 13],
          [125, 25],
          [160, 30]
        ],
        "LCCL"
      )
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(1)
  })

  test("minAngle rejects a curve join that creates a sharp resulting seam", () => {
    const labels = [
      line("line", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line(
        "offset-curve",
        "lane",
        [
          [105, 20],
          [115, 20],
          [130, 20],
          [200, 20]
        ],
        "LCCL"
      )
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(2)
  })
})

describe("connectLabels eligibility and guards", () => {
  test("minAngle rejects side-by-side parallel lines regardless of drawing direction", () => {
    const forward = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 20],
        [205, 20]
      ])
    ]
    const reverse = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [205, 20],
        [105, 20]
      ])
    ]

    expect(connectLabels(forward, 40, 150)[0]).toHaveLength(2)
    expect(connectLabels(reverse, 40, 150)[0]).toHaveLength(2)
  })

  test("minAngle follows a distinct neighbour past duplicate endpoint vertices", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 0],
        [105, 0],
        [200, 0]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(1)
  })

  test("duplicate endpoint vertices do not bypass parallel-line rejection", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 20],
        [105, 20],
        [205, 20]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(2)
  })

  test("minAngle rejects a directionless endpoint but zero disables the guard", () => {
    const labels = [
      line("a", "lane", [
        [0, 0],
        [100, 0]
      ]),
      line("b", "lane", [
        [105, 0],
        [105, 0]
      ])
    ]

    expect(connectLabels(labels, 40, 150)[0]).toHaveLength(2)
    expect(connectLabels(labels, 40, 0)[0]).toHaveLength(1)
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
