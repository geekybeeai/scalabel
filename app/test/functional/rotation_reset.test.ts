import * as actionConsts from "../../src/const/action"
import { changeSelect } from "../../src/functional/common"
import {
  makeImageViewerConfig,
  makeItem,
  makeState,
  makeTask
} from "../../src/functional/states"
import { ChangeSelectAction } from "../../src/types/action"
import { ImageViewerConfigType, State } from "../../src/types/state"

function stateWithRotation(rotation: number): State {
  const state = makeState({
    task: makeTask({
      items: [
        makeItem({ index: 0 }),
        makeItem({ index: 1 }),
        makeItem({ index: 2 })
      ]
    })
  })
  const config: ImageViewerConfigType = {
    ...makeImageViewerConfig(0),
    rotation
  }
  state.user.viewerConfigs[0] = config
  state.user.select.item = 0
  return state
}

function selectAction(state: State, item: number): ChangeSelectAction {
  return {
    type: actionConsts.CHANGE_SELECT,
    actionId: "test-action",
    sessionId: state.session.id,
    userId: "",
    timestamp: 0,
    select: { item }
  }
}

function rotationOf(state: State): number {
  const config = state.user.viewerConfigs[0] as ImageViewerConfigType
  return config.rotation ?? 0
}

describe("view rotation is remembered per item", () => {
  test("navigating to another item shows IT unrotated", () => {
    const state = stateWithRotation(90)
    const newState = changeSelect(state, selectAction(state, 1))
    expect(newState.user.select.item).toBe(1)
    expect(rotationOf(newState)).toBe(0)
  })

  test("navigating back restores the first item's rotation", () => {
    const state = stateWithRotation(90)
    const onItem1 = changeSelect(state, selectAction(state, 1))
    const backOnItem0 = changeSelect(onItem1, selectAction(onItem1, 0))
    expect(rotationOf(backOnItem0)).toBe(90)
  })

  test("each item keeps its own rotation independently", () => {
    const state = stateWithRotation(90) // item 0 at 90
    let s = changeSelect(state, selectAction(state, 1))
    // rotate item 1 to 180 (what changeViewerConfig would write)
    const config: ImageViewerConfigType = {
      ...(s.user.viewerConfigs[0] as ImageViewerConfigType),
      rotation: 180
    }
    s = {
      ...s,
      user: { ...s.user, viewerConfigs: { ...s.user.viewerConfigs, 0: config } }
    }
    const onItem0 = changeSelect(s, selectAction(s, 0))
    expect(rotationOf(onItem0)).toBe(90)
    const onItem1 = changeSelect(onItem0, selectAction(onItem0, 1))
    expect(rotationOf(onItem1)).toBe(180)
  })

  test("resetting on another item does NOT clear the first item's memory", () => {
    const state = stateWithRotation(90) // item 0 at 90
    let s = changeSelect(state, selectAction(state, 2)) // now on item 2, 0°
    // click Reset rotation on item 2 (writes rotation 0 for the current view)
    const config: ImageViewerConfigType = {
      ...(s.user.viewerConfigs[0] as ImageViewerConfigType),
      rotation: 0
    }
    s = {
      ...s,
      user: { ...s.user, viewerConfigs: { ...s.user.viewerConfigs, 0: config } }
    }
    const backOnItem0 = changeSelect(s, selectAction(s, 0))
    expect(rotationOf(backOnItem0)).toBe(90)
  })

  test("a select change on the SAME item keeps the rotation", () => {
    const state = stateWithRotation(180)
    const newState = changeSelect(state, selectAction(state, 0))
    expect(rotationOf(newState)).toBe(180)
  })
})
