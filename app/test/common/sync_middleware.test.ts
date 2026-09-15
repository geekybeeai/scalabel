import _ from "lodash"

import * as action from "../../src/action/common"
import { configureStore } from "../../src/common/configure_store"
import Session from "../../src/common/session"
import { makeSyncMiddleware } from "../../src/common/sync_middleware"
import { Synchronizer } from "../../src/common/synchronizer"
import { SyncActionMessageType } from "../../src/types/message"
import { State } from "../../src/types/state"
import { getRandomBox2dAction } from "../server/util/util"
import { testJson } from "../test_states/test_image_objects"

let state: State
let autosave: boolean
let bots: boolean
let sessionId: string
let projectName: string

beforeAll(() => {
  autosave = true
  bots = false
  sessionId = "syncSess"
  projectName = "syncProject"

  state = _.cloneDeep(testJson) as State
  state.task.config.autosave = autosave
  state.task.config.bots = bots
  state.session.id = sessionId
})

describe("Test sync middleware routes actions to synchronizer", () => {
  test("Connect then registration", () => {
    const sync = setupSync()
    const connectSpy = jest.spyOn(sync, "sendConnectionMessage")
    const registerSpy = jest.spyOn(sync, "finishRegistration")

    Session.dispatch(action.connect())
    Session.dispatch(action.registerSession(state))

    // Frontend doesn't know session id until after registration ack
    expect(connectSpy).toHaveBeenCalledTimes(1)
    expect(connectSpy).toHaveBeenCalledWith("", expect.any(Function))

    expect(registerSpy).toHaveBeenCalledTimes(1)
    expect(registerSpy).toHaveBeenCalledWith(
      state,
      autosave,
      sessionId,
      bots,
      expect.any(Function)
    )
  })

  test("Handles registration then reconnect", () => {
    const sync = setupSync()
    const connectSpy = jest.spyOn(sync, "sendConnectionMessage")
    const registerSpy = jest.spyOn(sync, "finishRegistration")

    Session.dispatch(action.registerSession(state))
    Session.dispatch(action.connect())

    expect(registerSpy).toHaveBeenCalledTimes(1)
    expect(registerSpy).toHaveBeenCalledWith(
      state,
      autosave,
      sessionId,
      bots,
      expect.any(Function)
    )

    // After initial state is registered, sessionId is available
    expect(connectSpy).toHaveBeenCalledTimes(1)
    expect(connectSpy).toHaveBeenCalledWith(sessionId, expect.any(Function))
  })

  test("Handles disconnection", () => {
    const sync = setupSync(true)
    const disconnectSpy = jest.spyOn(sync, "handleDisconnect")

    Session.dispatch(action.disconnect())

    expect(disconnectSpy).toHaveBeenCalledTimes(1)
    expect(disconnectSpy).toHaveBeenCalledWith(expect.any(Function))
  })

  test("Handles save", () => {
    const sync = setupSync(true)
    const saveSpy = jest.spyOn(sync, "save")

    Session.dispatch(action.save())

    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(saveSpy).toHaveBeenCalledWith(sessionId, bots, expect.any(Function))
  })

  test("Handles action broadcast", () => {
    const sync = setupSync(true)
    const broadcastSpy = jest.spyOn(sync, "handleBroadcast")

    const message: SyncActionMessageType = {
      taskId: "00000",
      projectName,
      sessionId,
      actions: {
        actions: [getRandomBox2dAction()],
        id: "packetId"
      },
      bot: false
    }
    Session.dispatch(action.receiveBroadcast(message))

    expect(broadcastSpy).toHaveBeenCalledTimes(1)
    expect(broadcastSpy).toHaveBeenCalledWith(
      message,
      sessionId,
      expect.any(Function)
    )
  })

  test("Queues normal actions for saving", () => {
    const sync = setupSync(true)
    const logSpy = jest.spyOn(sync, "queueActionForSaving")

    const addBoxAction = getRandomBox2dAction()
    Session.dispatch(addBoxAction)

    // 2 actions: addBox, and session status update
    expect(logSpy).toHaveBeenCalledTimes(2)
    expect(logSpy).toHaveBeenCalledWith(
      addBoxAction,
      autosave,
      sessionId,
      bots,
      expect.any(Function)
    )
  })
})

/**
 * Helper function to reset the store and the synchronizer for each test
 * Use shouldRegister for tests where initial state should already exist
 *
 * @param shouldRegister
 */
function setupSync(shouldRegister: boolean = false): Synchronizer {
  const mockSocket = {
    connected: true,
    emit: jest.fn()
  }

  const sync = new Synchronizer(mockSocket, 0, projectName, "syncUser")
  const syncMiddleware = makeSyncMiddleware(sync)
  Session.store = configureStore({}, false, syncMiddleware)

  if (shouldRegister) {
    Session.dispatch(action.registerSession(state))
  }

  return sync
}
