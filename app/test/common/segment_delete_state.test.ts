import { isCutMode, setCutMode } from "../../src/common/cut_state"
import {
  armSegmentDelete,
  getPickData,
  getPreviewData,
  getSegmentDeletePhase,
  isSegmentDeleteActive,
  onSegmentDeleteChange,
  recordFirstPick,
  recordSecondPick,
  resetSegmentDelete
} from "../../src/common/segment_delete_state"
import { DeleteSitePick } from "../../src/drawable/2d/polyline_cut_geometry"
import { PathPointType } from "../../src/types/state"

const PICK1: DeleteSitePick = {
  kind: "interior",
  site: {
    segmentIndex: 0,
    point: { x: 50, y: 0 },
    snappedVertexIndex: null,
    distance: 0
  }
}
const PICK2: DeleteSitePick = { kind: "end", endpointIndex: 2 }
const POINTS = [
  { x: 0, y: 0, pointType: PathPointType.LINE },
  { x: 100, y: 0, pointType: PathPointType.LINE },
  { x: 200, y: 0, pointType: PathPointType.LINE }
]

describe("segment_delete_state", () => {
  beforeEach(() => {
    resetSegmentDelete()
    setCutMode(false)
  })

  test("walks the phases and exposes the data", () => {
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(isSegmentDeleteActive()).toBe(false)

    armSegmentDelete()
    expect(getSegmentDeletePhase()).toBe("awaitFirst")
    expect(getPickData()).toBeNull()

    recordFirstPick({
      itemIndex: 0,
      labelId: "lineA",
      pick1: PICK1,
      pick1Point: { x: 50, y: 0 },
      points: POINTS
    })
    expect(getSegmentDeletePhase()).toBe("awaitSecond")
    expect(getPickData()?.labelId).toBe("lineA")
    expect(getPreviewData()).toBeNull()

    recordSecondPick(PICK1, PICK2, POINTS)
    expect(getSegmentDeletePhase()).toBe("preview")
    expect(getPreviewData()?.second).toBe(PICK2)
    expect(getPreviewData()?.doomed).toBe(POINTS)

    resetSegmentDelete()
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(getPickData()).toBeNull()
    expect(getPreviewData()).toBeNull()
  })

  test("notifies listeners on every transition, not on no-ops", () => {
    let calls = 0
    const off = onSegmentDeleteChange(() => {
      calls += 1
    })
    armSegmentDelete()
    expect(calls).toBe(1)
    resetSegmentDelete()
    expect(calls).toBe(2)
    resetSegmentDelete() // already inactive: no notification
    expect(calls).toBe(2)
    off()
    armSegmentDelete()
    expect(calls).toBe(2)
  })

  test("arming disarms the cut tool, and arming the cut tool resets this", () => {
    setCutMode(true)
    armSegmentDelete()
    expect(isCutMode()).toBe(false)
    expect(getSegmentDeletePhase()).toBe("awaitFirst")

    setCutMode(true)
    expect(getSegmentDeletePhase()).toBe("inactive")
    expect(isCutMode()).toBe(true)
  })
})
