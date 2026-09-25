/** @jest-environment node */

import * as action from "../../src/action/common"
import { configureStore } from "../../src/common/configure_store"
import { drawHistory } from "../../src/common/draw_history"
import Session, { getState } from "../../src/common/session"
import {
  isPreviewing,
  setAppliedIds,
  startPreview,
  undoStampOrHistory
} from "../../src/common/stamp_state"
import { LabelTypeName } from "../../src/const/common"
import { commitStamp } from "../../src/drawable/2d/polyline_stamp"
import { DEFAULT_STAMP_OPTIONS } from "../../src/drawable/2d/polyline_stamp_geometry"
import { makeLabel, makePathPoint2D } from "../../src/functional/states"
import { PathPointType, State } from "../../src/types/state"
import { testJson } from "../test_states/test_image_objects"

/** Add a straight guide long enough to produce several marks. */
function seedGuide(): void {
  const labelId = "stamp-guide"
  const shapes = [0, 200].map((x) =>
    makePathPoint2D({
      x,
      y: 50,
      pointType: PathPointType.LINE,
      label: [labelId]
    })
  )
  const label = makeLabel(
    {
      id: labelId,
      item: 0,
      type: LabelTypeName.POLYLINE_2D,
      shapes: shapes.map((shape) => shape.id)
    },
    false
  )
  Session.dispatch(action.addLabel(0, label, shapes))
}

test("one undo removes every label created by a stamp run", () => {
  Session.store = configureStore(testJson as Partial<State>)
  drawHistory.reset()
  seedGuide()

  const beforeIds = Object.keys(getState().task.items[0].labels)
  startPreview("stamp-guide")
  const result = commitStamp("stamp-guide", {
    ...DEFAULT_STAMP_OPTIONS,
    period: 40
  })

  expect(result.count).toBeGreaterThan(1)
  expect(Object.keys(getState().task.items[0].labels)).toHaveLength(
    beforeIds.length + result.count
  )
  expect(result.labelIds).toBeDefined()
  expect(
    (result.labelIds ?? []).every(
      (id) => getState().task.items[0].labels[id] !== undefined
    )
  ).toBe(true)
  expect(drawHistory.canUndo()).toBe(true)

  setAppliedIds(result.labelIds ?? [])
  expect(undoStampOrHistory()).toBe(true)
  expect(isPreviewing()).toBe(false)
  expect(Object.keys(getState().task.items[0].labels).sort()).toEqual(
    beforeIds.sort()
  )
})
