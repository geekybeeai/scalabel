import * as action from "../../src/action/common"
import { makePolygon2dLabel } from "../../src/action/polygon2d"
import Session, { dispatch, getState } from "../../src/common/session"
import { updateTracks } from "../../src/common/session_setup"
import { Label2DList } from "../../src/drawable/2d/label2d_list"
import { commit2DLabels } from "../../src/drawable/states"
import { makeSimplePathPoint2D } from "../../src/functional/states"
import { LabelType, PathPointType, ShapeType } from "../../src/types/state"
import { setupTestStore } from "../components/util"
import { testJson } from "../test_states/test_track_polygon2d_objects"

const SELF_INTERSECTING_POINTS = [
  makeSimplePathPoint2D(200, 100, PathPointType.LINE),
  makeSimplePathPoint2D(400, 300, PathPointType.LINE),
  makeSimplePathPoint2D(200, 300, PathPointType.LINE),
  makeSimplePathPoint2D(400, 100, PathPointType.LINE)
]

/**
 * Reset the test session with polygon tracking enabled or disabled.
 *
 * @param tracking whether the fixture should use tracking behavior
 */
function setupPolygonStore(tracking: boolean): void {
  const state = {
    ...testJson,
    task: {
      ...testJson.task,
      config: { ...testJson.task?.config, tracking }
    }
  }
  setupTestStore(state)
  Session.tracks = {}
  Session.images.length = 0
  for (const _item of getState().task.items) {
    Session.images.push({ [-1]: new Image(1000, 1000) })
  }
  dispatch(action.goToItem(0))
}

test("committing an imported self-intersecting polygon keeps the label", () => {
  setupPolygonStore(false)
  const [label, shapes] = makePolygon2dLabel(
    -1,
    [0],
    SELF_INTERSECTING_POINTS,
    true,
    false
  )
  dispatch(action.addLabel(0, label, shapes))

  const before = getState()
  const labelId = Object.keys(before.task.items[0].labels)[0]
  const shapeIds = [...before.task.items[0].labels[labelId].shapes]
  const label2dList = new Label2DList()
  label2dList.updateState(before)

  commit2DLabels([label2dList.get(0)])

  const after = getState()
  expect(after.task.items[0].labels[labelId]).toBeDefined()
  for (const shapeId of shapeIds) {
    expect(after.task.items[0].shapes[shapeId]).toBeDefined()
  }
})

test("committing an existing self-intersecting polygon preserves its track", () => {
  setupPolygonStore(true)
  const frames: number[] = []
  const labels: LabelType[] = []
  const shapes: ShapeType[][] = []

  for (let frame = 0; frame < getState().task.items.length; frame++) {
    const [label, frameShapes] = makePolygon2dLabel(
      -1,
      [0],
      SELF_INTERSECTING_POINTS,
      true
    )
    label.track = "self-intersecting-track"
    frames.push(frame)
    labels.push(label)
    shapes.push(frameShapes)
  }
  dispatch(action.addTrack(frames, labels[0].type, labels, shapes))

  const before = getState()
  const trackId = Object.keys(before.task.tracks)[0]
  const originalTrackLabels = { ...before.task.tracks[trackId].labels }
  updateTracks(before)

  // Rebuild the drawable as the editor does when an imported prediction is
  // selected, then commit it without changing its self-crossing geometry.
  const label2dList = new Label2DList()
  label2dList.updateState(before)
  const drawable = label2dList.get(0)

  commit2DLabels([drawable])

  const after = getState()
  expect(after.task.tracks[trackId]).toBeDefined()
  expect(after.task.tracks[trackId].labels).toEqual(originalTrackLabels)
  for (const [itemIndex, labelId] of Object.entries(originalTrackLabels)) {
    expect(after.task.items[Number(itemIndex)].labels[labelId]).toBeDefined()
  }
})
