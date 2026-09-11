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
            poly2d: [
              {
                vertices: [
                  [0, 0],
                  [100, 0]
                ],
                types: "LL",
                closed: false
              }
            ]
          },
          {
            id: "b",
            category: "without_curb_road_edge",
            poly2d: [
              {
                vertices: [
                  [101, 0],
                  [200, 0]
                ],
                types: "LL",
                closed: false
              }
            ]
          }
        ]
      }
    ],
    image_root: "/nonexistent",
    clamp: false
  })
  expect(response.ok).toBe(true)
  if (response.ok) {
    expect(
      (response.document as Array<{ labels: unknown[] }>)[0].labels
    ).toHaveLength(1)
    expect(response.report.summary.totalMerged).toBe(1)
  }
})

test("a processing exception becomes ok:false", async () => {
  const response = await run({ document: [{ name: "x", labels: [null] }] })
  expect(response.ok).toBe(false)
})
