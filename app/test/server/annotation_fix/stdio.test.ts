/** @jest-environment node */
import { run } from "../../../src/server/annotation_fix/stdio"

test("a request without a document is rejected", async () => {
  expect(await run({})).toEqual({
    ok: false,
    error: "request is missing 'document'"
  })
})

test("different categories snap without merging or changing category", async () => {
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
    const labels = (
      response.document as Array<{
        labels: Array<{
          id: string
          category: string
          poly2d: Array<{ vertices: Array<[number, number]> }>
        }>
      }>
    )[0].labels
    expect(labels.map(({ id, category }) => ({ id, category }))).toEqual([
      { id: "a", category: "curb_road_edge" },
      { id: "b", category: "without_curb_road_edge" }
    ])
    expect(labels[0].poly2d[0].vertices[1]).toEqual([100, 0])
    expect(labels[1].poly2d[0].vertices[0]).toEqual([100, 0])
    expect(response.report.summary.totalMerged).toBe(0)
    expect(response.report.summary.totalSnapped).toBe(1)
    expect(response.report.frames[0]).toMatchObject({
      merged: 0,
      snapped: 1,
      labelsBefore: 2,
      labelsAfter: 2,
      snaps: [
        {
          anchorId: "a",
          movedId: "b",
          anchorCategory: "curb_road_edge",
          movedCategory: "without_curb_road_edge",
          junction: [100, 0],
          gap: 1
        }
      ]
    })
  }
})

test("a processing exception becomes ok:false", async () => {
  const response = await run({ document: [{ name: "x", labels: [null] }] })
  expect(response.ok).toBe(false)
})
