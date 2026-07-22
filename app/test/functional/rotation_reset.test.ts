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
      items: [makeItem({ index: 0 }), makeItem({ index: 1 })]
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

describe("view rotation is scoped to the current item", () => {
  test("navigating to another item resets rotation to 0", () => {
    const state = stateWithRotation(90)
    const newState = changeSelect(state, selectAction(state, 1))
    const config = newState.user.viewerConfigs[0] as ImageViewerConfigType
    expect(newState.user.select.item).toBe(1)
    expect(config.rotation ?? 0).toBe(0)
  })

  test("a select change on the SAME item keeps the rotation", () => {
    const state = stateWithRotation(180)
    const newState = changeSelect(state, selectAction(state, 0))
    const config = newState.user.viewerConfigs[0] as ImageViewerConfigType
    expect(config.rotation).toBe(180)
  })
})
