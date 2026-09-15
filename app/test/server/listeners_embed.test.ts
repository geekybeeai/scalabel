import { Request, Response } from "express"
import * as fs from "fs-extra"
import * as path from "path"

import { serverConfig } from "../../src/server/defaults"
import { FileStorage } from "../../src/server/file_storage"
import { Listeners } from "../../src/server/listeners"
import { getTestDir } from "../../src/server/path"
import { ProjectStore } from "../../src/server/project_store"
import { RedisCache } from "../../src/server/redis_cache"
import { RedisClient } from "../../src/server/redis_client"
import { UserManager } from "../../src/server/user_manager"

jest.mock("../../src/server/redis_client")

// The corrector spawns the compiled worker bundle, which unit tests must not
// depend on. Stand in with a merge of the first two labels, so the handler's
// use of the result is observable.
const correctAnnotationsMock = jest.fn(
  async (items: Array<{ labels?: unknown[] }>) =>
    items.map((item) => {
      const labels = item.labels ?? []
      return labels.length >= 2
        ? { ...item, labels: [labels[0], ...labels.slice(2)] }
        : item
    })
)
jest.mock("../../src/server/annotation_fix", () => ({
  correctAnnotations: async (items: Array<{ labels?: unknown[] }>) =>
    await correctAnnotationsMock(items)
}))

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

function fakeReq(body: unknown): Request {
  return { method: "POST", body, query: {} } as unknown as Request
}

function makeListeners(dataDir: string): {
  listeners: Listeners
  projectStore: ProjectStore
} {
  const storage = new FileStorage(dataDir)
  const client = new RedisClient(serverConfig.redis)
  const redisStore = new RedisCache(serverConfig.redis, storage, client)
  const projectStore = new ProjectStore(storage, redisStore)
  const userManager = new UserManager(projectStore, false)
  const listeners = new Listeners(projectStore, userManager, serverConfig)
  return { listeners, projectStore }
}

describe("openEditSessionHandler validation", () => {
  let listeners: Listeners
  let dataDir: string

  beforeAll(() => {
    dataDir = getTestDir("embed-validation-data")
    listeners = makeListeners(dataDir).listeners
  })

  afterAll(() => {
    fs.removeSync(dataDir)
  })

  test("rejects missing sessionId with 400", async () => {
    const req = fakeReq({
      annotations: { frames: [], config: { categories: [] } }
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })

  test("rejects malformed UUID with 400", async () => {
    const req = fakeReq({ sessionId: "not-a-uuid", annotations: {} })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })

  test("rejects payload missing frames[0].url with 400", async () => {
    const req = fakeReq({
      sessionId: "550e8400-e29b-41d4-a716-446655440000",
      annotations: {
        frames: [
          {
            name: "x",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          }
        ],
        config: { categories: [{ name: "lane" }], attributes: [] }
      }
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })

  test("rejects payload with empty categories with 400", async () => {
    const req = fakeReq({
      sessionId: "550e8400-e29b-41d4-a716-446655440000",
      annotations: {
        frames: [
          {
            name: "x",
            url: "x.png",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          }
        ],
        config: { categories: [], attributes: [] }
      }
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })

  test("rejects more than one frame with 400", async () => {
    const req = fakeReq({
      sessionId: "550e8400-e29b-41d4-a716-446655440002",
      annotations: {
        frames: [
          {
            name: "a.png",
            url: "a.png",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          },
          {
            name: "b.png",
            url: "b.png",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          }
        ],
        config: { categories: [{ name: "lane" }], attributes: [] }
      }
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })
})

describe("openEditSessionHandler creates the project", () => {
  let listeners: Listeners
  let projectStore: ProjectStore
  let dataDir: string

  beforeAll(() => {
    dataDir = getTestDir("embed-create-data")
    const made = makeListeners(dataDir)
    listeners = made.listeners
    projectStore = made.projectStore
  })

  afterAll(() => {
    fs.removeSync(dataDir)
  })

  test("creates an embed_<sessionId> project from valid payload", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440000"
    const req = fakeReq({
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
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)

    expect(res.statusCode).toBe(200)
    expect((res.body as { labelUrl: string }).labelUrl).toContain(
      `embed_${sessionId}`
    )
    expect((res.body as { labelUrl: string }).labelUrl).toContain("embedded=1")
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(
      true
    )
  })

  test("returns 409 on duplicate sessionId", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440001"
    const body = {
      sessionId,
      annotations: {
        frames: [
          {
            name: "x.png",
            url: "x.png",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          }
        ],
        config: { categories: [{ name: "lane" }], attributes: [] }
      }
    }
    const res1 = fakeRes()
    await listeners.openEditSessionHandler(fakeReq(body), res1)
    expect(res1.statusCode).toBe(200)

    const res2 = fakeRes()
    await listeners.openEditSessionHandler(fakeReq(body), res2)
    expect(res2.statusCode).toBe(409)
  })

  test("rejects a non-boolean autoCorrect with 400", async () => {
    const res = fakeRes()
    await listeners.openEditSessionHandler(
      fakeReq({
        sessionId: "550e8400-e29b-41d4-a716-446655440002",
        autoCorrect: "yes",
        annotations: {
          frames: [{ name: "x.png", url: "x.png", labels: [] }],
          config: { categories: [{ name: "lane" }], attributes: [] }
        }
      }),
      res
    )
    expect(res.statusCode).toBe(400)
    expect(res.body).toContain("autoCorrect")
  })

  test("autoCorrect: true corrects the frames before the session opens", async () => {
    const line = (id: string, x0: number, x1: number): unknown => ({
      id,
      category: "lane",
      attributes: {},
      manualShape: true,
      box2d: null,
      box3d: null,
      poly2d: [
        {
          vertices: [
            [x0, 0],
            [x1, 0]
          ],
          types: "LL",
          closed: false
        }
      ]
    })
    const frame = {
      name: "x.png",
      url: "x.png",
      labels: [line("a", 0, 100), line("b", 104, 200)],
      videoName: "",
      timestamp: 0,
      attributes: {},
      sensor: -1
    }
    const config = { categories: [{ name: "lane" }], attributes: [] }
    correctAnnotationsMock.mockClear()

    const offRes = fakeRes()
    await listeners.openEditSessionHandler(
      fakeReq({
        sessionId: "550e8400-e29b-41d4-a716-446655440003",
        annotations: { frames: [frame], config }
      }),
      offRes
    )
    expect(offRes.statusCode).toBe(200)
    expect(correctAnnotationsMock).not.toHaveBeenCalled()

    const onRes = fakeRes()
    await listeners.openEditSessionHandler(
      fakeReq({
        sessionId: "550e8400-e29b-41d4-a716-446655440004",
        autoCorrect: true,
        annotations: { frames: [frame], config }
      }),
      onRes
    )
    expect(onRes.statusCode).toBe(200)
    expect(correctAnnotationsMock).toHaveBeenCalledTimes(1)

    // The session's task was built from the corrector's output (one merged
    // label), not from the two original lines. Read the task file directly:
    // loadState goes through the automocked Redis client, which returns
    // nothing useful here.
    const task = fs.readJsonSync(
      path.join(
        dataDir,
        "projects",
        "embed_550e8400-e29b-41d4-a716-446655440004",
        "tasks",
        "000000.json"
      )
    ) as { items: Array<{ labels: Record<string, unknown> }> }
    expect(Object.keys(task.items[0].labels)).toHaveLength(1)
  })
})

describe("closeEditSessionHandler", () => {
  let listeners: Listeners
  let projectStore: ProjectStore
  let dataDir: string

  beforeAll(() => {
    dataDir = getTestDir("embed-close-data")
    const made = makeListeners(dataDir)
    listeners = made.listeners
    projectStore = made.projectStore
  })

  afterAll(() => {
    fs.removeSync(dataDir)
  })

  test("returns 400 for malformed sessionId", async () => {
    const req = {
      method: "POST",
      query: { sessionId: "nope" }
    } as unknown as Request
    const res = fakeRes()
    await listeners.closeEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })

  test("returns 404 when project does not exist", async () => {
    const req = {
      method: "POST",
      query: { sessionId: "550e8400-e29b-41d4-a716-446655440099" }
    } as unknown as Request
    const res = fakeRes()
    await listeners.closeEditSessionHandler(req, res)
    expect(res.statusCode).toBe(404)
  })

  test("deletes the embed_<sessionId> project on success", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440010"
    const openReq = fakeReq({
      sessionId,
      annotations: {
        frames: [
          {
            name: "x.png",
            url: "x.png",
            labels: [],
            videoName: "",
            timestamp: 0,
            attributes: {},
            sensor: -1
          }
        ],
        config: { categories: [{ name: "lane" }], attributes: [] }
      }
    })
    await listeners.openEditSessionHandler(openReq, fakeRes())
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(
      true
    )

    const closeReq = {
      method: "POST",
      query: { sessionId }
    } as unknown as Request
    const res = fakeRes()
    await listeners.closeEditSessionHandler(closeReq, res)
    expect(res.statusCode).toBe(204)
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(
      false
    )
  })

  test("is idempotent: 404 on second close", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440011"
    await listeners.openEditSessionHandler(
      fakeReq({
        sessionId,
        annotations: {
          frames: [
            {
              name: "x.png",
              url: "x.png",
              labels: [],
              videoName: "",
              timestamp: 0,
              attributes: {},
              sensor: -1
            }
          ],
          config: { categories: [{ name: "lane" }], attributes: [] }
        }
      }),
      fakeRes()
    )
    await listeners.closeEditSessionHandler(
      { method: "POST", query: { sessionId } } as unknown as Request,
      fakeRes()
    )

    const res = fakeRes()
    await listeners.closeEditSessionHandler(
      { method: "POST", query: { sessionId } } as unknown as Request,
      res
    )
    expect(res.statusCode).toBe(404)
  })
})
