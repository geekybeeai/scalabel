import { Request, Response } from "express"
import * as fs from "fs-extra"

import { serverConfig } from "../../src/server/defaults"
import { FileStorage } from "../../src/server/file_storage"
import { Listeners } from "../../src/server/listeners"
import { getTestDir } from "../../src/server/path"
import { ProjectStore } from "../../src/server/project_store"
import { RedisCache } from "../../src/server/redis_cache"
import { RedisClient } from "../../src/server/redis_client"
import { UserManager } from "../../src/server/user_manager"

jest.mock("../../src/server/redis_client")

interface TestResponse extends Response {
  /** captured status code */
  statusCode: number
  /** captured response body */
  body: unknown
}

function fakeRes(): TestResponse {
  const res = {
    statusCode: 200,
    body: undefined as unknown,
    status(code: number): TestResponse {
      this.statusCode = code
      return this as unknown as TestResponse
    },
    send(b: unknown): TestResponse {
      this.body = b
      return this as unknown as TestResponse
    },
    json(b: unknown): TestResponse {
      this.body = b
      return this as unknown as TestResponse
    },
    end(): TestResponse {
      return this as unknown as TestResponse
    }
  }
  return res as unknown as TestResponse
}

describe("embed round-trip", () => {
  let listeners: Listeners
  let projectStore: ProjectStore
  let dataDir: string
  const sessionId = "550e8400-e29b-41d4-a716-446655442222"
  const projectName = `embed_${sessionId}`

  const samplePayload = {
    sessionId,
    annotations: {
      frames: [
        {
          name: "items/foo.png",
          url: "items/foo.png",
          labels: [],
          videoName: "",
          timestamp: 0,
          attributes: {},
          sensor: -1
        }
      ],
      config: {
        categories: [{ name: "lane" }, { name: "edge" }],
        attributes: []
      }
    }
  }

  beforeAll(() => {
    dataDir = getTestDir("embed-roundtrip-data")
    const storage = new FileStorage(dataDir)
    const client = new RedisClient(serverConfig.redis)
    const redisStore = new RedisCache(serverConfig.redis, storage, client)
    projectStore = new ProjectStore(storage, redisStore)
    const userManager = new UserManager(projectStore, false)
    listeners = new Listeners(projectStore, userManager, serverConfig)
  })

  afterAll(() => {
    fs.removeSync(dataDir)
  })

  test("full open → close round-trip", async () => {
    const openRes = fakeRes()
    await listeners.openEditSessionHandler(
      { method: "POST", body: samplePayload, query: {} } as unknown as Request,
      openRes
    )
    expect(openRes.statusCode).toBe(200)

    const labelUrl = (openRes.body as { labelUrl: string }).labelUrl
    expect(labelUrl).toBe(
      `/label?project_name=${encodeURIComponent(projectName)}` +
        `&task_index=0&embedded=1`
    )
    expect(await projectStore.checkProjectName(projectName)).toBe(true)

    // Confirm the project has the expected single item with the image url
    // from the payload.
    const tasks = await projectStore.getTasksInProject(projectName)
    expect(tasks.length).toBe(1)
    expect(tasks[0].items.length).toBe(1)
    expect(Object.values(tasks[0].items[0].urls)[0]).toBe("items/foo.png")

    // Close
    const closeRes = fakeRes()
    await listeners.closeEditSessionHandler(
      { method: "POST", query: { sessionId } } as unknown as Request,
      closeRes
    )
    expect(closeRes.statusCode).toBe(204)
    expect(await projectStore.checkProjectName(projectName)).toBe(false)
  })
})
