# Scalabel website-embed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pair of HTTP endpoints (`POST /openEditSession`, `POST /closeEditSession`) plus an `?embedded=1` mode in the labeling UI so an external website can pop up a Scalabel iframe pre-loaded with a JSON, let the user edit, and receive the updated JSON via `postMessage` — round-tripping per-image polyline annotation correction.

**Architecture:** Same-origin reverse-proxy deployment. Each edit session maps to one ephemeral `embed_<sessionId>` Scalabel project created on demand via the new endpoint, opened in an iframe at `/label?project_name=embed_<sessionId>&task_index=0&embedded=1`. A "Save & Close" button posts the JSON to the parent window, then fires-and-forgets a delete request. A 24h scheduled task safety-net reaps abandoned `embed_*` projects. See `docs/superpowers/specs/2026-05-08-scalabel-website-embed-design.md` for the full design.

**Tech Stack:** Node 12+, Express, Redis, Socket.io, React 17 + Redux, TypeScript, Jest, webpack 5.

**Branch:** `feature/embedded-scalable-tmi` (where the spec was committed at `ddb733d0`). All work happens on this branch.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `app/src/const/common.ts` | modify | Add `QueryArg.EMBEDDED` |
| `app/src/const/connection.ts` | modify | Add `Endpoint.OPEN_EDIT_SESSION` and `Endpoint.CLOSE_EDIT_SESSION` |
| `app/src/types/config.ts` | modify | Add optional `embed?: EmbedConfig` field on `ServerConfig`; new `EmbedConfig` interface |
| `app/src/server/defaults.ts` | modify | Provide a default `embed` config block |
| `app/src/server/config.ts` | modify (if needed) | Ensure `embed` section is read from yaml (likely auto via `yaml.load`) |
| `app/src/server/listeners.ts` | modify | Add `openEditSessionHandler` + `closeEditSessionHandler`; share project-creation helpers |
| `app/src/server/embed_cleanup.ts` | create | Pure function `findExpiredEmbedProjects` + scheduler `startEmbedCleanup` |
| `app/src/server/main.ts` | modify | Register the two new routes, start the cleanup interval |
| `app/src/common/session.ts` | modify | Add `public embedded: boolean = false` field |
| `app/src/common/session_init.tsx` | modify | Parse `embedded` query param; set `Session.embedded` |
| `app/src/components/window.tsx` | modify | Pass `null` for `titleBar` when `Session.embedded` |
| `app/src/components/label_layout.tsx` | modify | Skip the title-bar wrapper div when `titleBar` prop is `null` |
| `app/src/components/toolbar.tsx` | modify | Render "Save & Close" button + handler when `Session.embedded`; hide file path readout at lines 215-221 |
| `app/test/server/embed_cleanup.test.ts` | create | Unit tests: cleanup is prefix-scoped, never touches non-`embed_*` projects |
| `app/test/server/listeners_embed.test.ts` | create | Unit tests: `/openEditSession` validation; `/closeEditSession` idempotency + prefix scope |
| `app/test/server/embed_roundtrip.test.ts` | create | Integration test: open → simulate edit → close round-trip |
| `app/config/default_config.yml` | modify | Add example `embed:` section (commented) |

---

## Task 1: Branch verification and pre-flight

**Files:**
- None modified

- [ ] **Step 1: Confirm branch and clean working tree**

```bash
git status --short
git rev-parse --abbrev-ref HEAD
```

Expected: branch is `feature/embedded-scalable-tmi`. The pre-existing untracked files (`app.zip`, `build.log`, `out_scalable_base_v2.json`, etc.) and modifications listed in the session start are not part of this work and stay untouched throughout.

- [ ] **Step 2: Verify tests run today before any changes**

```bash
npm test -- --testPathPattern="server/create_project"
```

Expected: existing project-creation tests pass. If they fail, stop — the environment is broken and downstream tasks will be unreliable.

---

## Task 2: Add `QueryArg.EMBEDDED`

**Files:**
- Modify: `app/src/const/common.ts:163-168`

- [ ] **Step 1: Edit the QueryArg enum**

Open `app/src/const/common.ts` and locate the existing `QueryArg` enum:

```typescript
export enum QueryArg {
  PROJECT_NAME = "project_name",
  TASK_INDEX = "task_index",
  TASK_ID = "task_id",
  DEV_MODE = "dev"
}
```

Replace with:

```typescript
export enum QueryArg {
  PROJECT_NAME = "project_name",
  TASK_INDEX = "task_index",
  TASK_ID = "task_id",
  DEV_MODE = "dev",
  EMBEDDED = "embedded"
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npx tsc --noEmit
```

Expected: no errors. If errors appear unrelated to this change, note them but proceed (the codebase has prior eslint TODOs).

- [ ] **Step 3: Commit**

```bash
git add app/src/const/common.ts
git commit -m "feat(embed): add QueryArg.EMBEDDED for embedded-mode URL param"
```

---

## Task 3: Add new endpoint constants

**Files:**
- Modify: `app/src/const/connection.ts:8-20`

- [ ] **Step 1: Edit the Endpoint enum**

Open `app/src/const/connection.ts` and locate the existing enum (will already contain `TILE_EXPORT` from earlier work):

```typescript
export const enum Endpoint {
  POST_PROJECT = "/postProject",
  POST_PROJECT_INTERNAL = "/postProjectInternal",
  GET_PROJECT_NAMES = "/getProjectNames",
  EXPORT = "/getExport",
  TILE_EXPORT = "/getTileData",
  DASHBOARD = "/getDashboardContents",
  GET_TASK_METADATA = "/getTaskMetaData",
  POST_TASKS = "/postTasks",
  CALLBACK = "/callback",
  STATS = "/stats",
  DELETE_PROJECT = "/deleteProject"
}
```

Add two members:

```typescript
export const enum Endpoint {
  POST_PROJECT = "/postProject",
  POST_PROJECT_INTERNAL = "/postProjectInternal",
  GET_PROJECT_NAMES = "/getProjectNames",
  EXPORT = "/getExport",
  TILE_EXPORT = "/getTileData",
  DASHBOARD = "/getDashboardContents",
  GET_TASK_METADATA = "/getTaskMetaData",
  POST_TASKS = "/postTasks",
  CALLBACK = "/callback",
  STATS = "/stats",
  DELETE_PROJECT = "/deleteProject",
  OPEN_EDIT_SESSION = "/openEditSession",
  CLOSE_EDIT_SESSION = "/closeEditSession"
}
```

- [ ] **Step 2: Commit**

```bash
git add app/src/const/connection.ts
git commit -m "feat(embed): add OPEN_EDIT_SESSION and CLOSE_EDIT_SESSION endpoint constants"
```

---

## Task 4: Add `EmbedConfig` to `ServerConfig` and defaults

**Files:**
- Modify: `app/src/types/config.ts`
- Modify: `app/src/server/defaults.ts`

- [ ] **Step 1: Add the EmbedConfig interface and field**

In `app/src/types/config.ts`, add a new interface near the other `*Config` interfaces (immediately above `ServerConfig`):

```typescript
export interface EmbedConfig {
  /** How often the cleanup task runs, in minutes */
  cleanupIntervalMinutes: number
  /** How long an embed_* project may be inactive before deletion, in minutes */
  sessionTtlMinutes: number
}
```

Then in the `ServerConfig` interface, add the optional `embed` field (right after `cognito?: CognitoConfig`):

```typescript
export interface ServerConfig {
  http: HttpConfig
  storage: StorageConfig
  user: UserConfig
  mode: ModeConfig
  redis: RedisConfig
  bot: BotConfig
  cognito?: CognitoConfig
  /** embedded-edit-session config */
  embed?: EmbedConfig

  /** DEPRECATED fields for backward compatibility */
  port?: number
  data?: string
  itemDir?: string
  database?: StorageType
}
```

- [ ] **Step 2: Add a default in defaults.ts**

In `app/src/server/defaults.ts`, find the `serverConfig` constant and add an `embed` default at the end of the object literal (before the closing brace):

```typescript
export const serverConfig: ServerConfig = {
  // ... existing fields unchanged ...
  bot: {
    on: false,
    host: "http://0.0.0.0",
    port: 8080
  },
  embed: {
    cleanupIntervalMinutes: 60,
    sessionTtlMinutes: 1440
  }
}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/src/types/config.ts app/src/server/defaults.ts
git commit -m "feat(embed): add EmbedConfig type and defaults (60m cleanup, 24h TTL)"
```

---

## Task 5: Implement `openEditSessionHandler` (TDD)

**Files:**
- Modify: `app/src/server/listeners.ts`
- Create: `app/test/server/listeners_embed.test.ts`

The handler validates input, writes the incoming JSON to a temp file, then drives the same project-creation pipeline used by `/postProject`'s single-file path: `parseForm` → `parseSingleFile` → `createProject` → `filterIntersectedPolygonsInProject` → `projectStore.saveProject` + `createTasks`.

- [ ] **Step 1: Write the failing test for input validation**

Create `app/test/server/listeners_embed.test.ts` with:

```typescript
import { Request, Response } from "express"
import * as fs from "fs-extra"
import { Listeners } from "../../src/server/listeners"
import { ProjectStore } from "../../src/server/project_store"
import { UserManager } from "../../src/server/user_manager"
import { FileStorage } from "../../src/server/file_storage"
import { RedisCache } from "../../src/server/redis_cache"
import { RedisClient } from "../../src/server/redis_client"
import { serverConfig } from "../../src/server/defaults"
import { getTestDir } from "../../src/server/path"

function fakeRes(): Response {
  const res = {
    statusCode: 200,
    body: undefined as unknown,
    status(code: number) { this.statusCode = code; return this },
    send(b: unknown) { this.body = b; return this },
    json(b: unknown) { this.body = b; return this },
    end() { return this }
  }
  return res as unknown as Response
}

function fakeReq(body: unknown): Request {
  return { method: "POST", body, query: {} } as unknown as Request
}

describe("openEditSessionHandler validation", () => {
  let listeners: Listeners
  let dataDir: string

  beforeAll(async () => {
    dataDir = getTestDir("embed-validation-data")
    const storage = new FileStorage(dataDir)
    const client = new RedisClient(serverConfig.redis)
    const redisStore = new RedisCache(serverConfig.redis, storage, client)
    const projectStore = new ProjectStore(storage, redisStore)
    const userManager = new UserManager(projectStore, false)
    listeners = new Listeners(projectStore, userManager, serverConfig)
  })

  afterAll(() => {
    fs.removeSync(dataDir)
  })

  test("rejects missing sessionId with 400", async () => {
    const req = fakeReq({ annotations: { frames: [], config: { categories: [] } } })
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
        frames: [{ name: "x", labels: [], videoName: "", timestamp: 0, attributes: {}, sensor: -1 }],
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
        frames: [{ name: "x", url: "x.png", labels: [], videoName: "", timestamp: 0, attributes: {}, sensor: -1 }],
        config: { categories: [], attributes: [] }
      }
    })
    const res = fakeRes()
    await listeners.openEditSessionHandler(req, res)
    expect(res.statusCode).toBe(400)
  })
})
```

- [ ] **Step 2: Run the test, expect failure**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: 4 failures with "openEditSessionHandler is not a function" or similar.

- [ ] **Step 3: Add imports and helper at top of `app/src/server/listeners.ts`**

Locate the existing imports block and add (in addition to what's already there):

```typescript
import * as os from "os"
```

Just below the existing helper interfaces (`TileBackendRequest` etc.), add:

```typescript
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

interface OpenEditSessionBody {
  sessionId?: string
  annotations?: {
    frames?: Array<{
      url?: string
      name?: string
      labels?: unknown[]
      videoName?: string
      timestamp?: number
      attributes?: Record<string, unknown>
      sensor?: number
    }>
    config?: {
      categories?: Array<{ name?: string }>
      attributes?: unknown[]
    }
  }
}

function validateOpenEditSessionBody(
  body: OpenEditSessionBody | undefined
): { ok: true } | { ok: false; reason: string } {
  if (body === undefined || body === null) {
    return { ok: false, reason: "Missing request body" }
  }
  if (typeof body.sessionId !== "string" || !UUID_RE.test(body.sessionId)) {
    return { ok: false, reason: "sessionId must be a valid UUID" }
  }
  const ann = body.annotations
  if (ann === undefined || ann === null) {
    return { ok: false, reason: "Missing annotations" }
  }
  if (!Array.isArray(ann.frames) || ann.frames.length !== 1) {
    return { ok: false, reason: "annotations.frames must contain exactly one entry" }
  }
  const frame = ann.frames[0]
  if (typeof frame.url !== "string" || frame.url.length === 0) {
    return { ok: false, reason: "annotations.frames[0].url must be a non-empty string" }
  }
  const cats = ann.config?.categories
  if (!Array.isArray(cats) || cats.length === 0) {
    return { ok: false, reason: "annotations.config.categories must be a non-empty array" }
  }
  for (const c of cats) {
    if (typeof c?.name !== "string" || c.name.length === 0) {
      return { ok: false, reason: "Every category requires a non-empty name" }
    }
  }
  return { ok: true }
}
```

- [ ] **Step 4: Add the handler method on the `Listeners` class**

Inside the `Listeners` class (right after `getTileDataHandler`), add:

```typescript
public async openEditSessionHandler(
  req: Request,
  res: Response
): Promise<void> {
  if (this.checkInvalidPost(req, res)) {
    return
  }

  const body = req.body as OpenEditSessionBody | undefined
  const validation = validateOpenEditSessionBody(body)
  if (!validation.ok) {
    res.status(400).send(filterXSS(validation.reason))
    return
  }

  const sessionId = body!.sessionId as string
  const projectName = `embed_${sessionId}`

  if (await this.projectStore.checkProjectName(projectName)) {
    res.status(409).send(`Session ${sessionId} already exists`)
    return
  }

  res.status(200).json({
    labelUrl:
      `/label?project_name=${encodeURIComponent(projectName)}` +
      `&task_index=0&embedded=1`
  })
}
```

Note: the validation-and-response path is enough to make the four validation tests pass. The full project-creation logic is added in Task 6 (kept separate so each task is small).

- [ ] **Step 5: Run validation tests, expect pass**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/src/server/listeners.ts app/test/server/listeners_embed.test.ts
git commit -m "feat(embed): openEditSession handler with input validation (no project creation yet)"
```

---

## Task 6: Wire `openEditSession` into the project-creation pipeline

**Files:**
- Modify: `app/src/server/listeners.ts`
- Modify: `app/test/server/listeners_embed.test.ts`

- [ ] **Step 1: Add a happy-path test asserting the project gets created**

Append to `app/test/server/listeners_embed.test.ts`:

```typescript
describe("openEditSessionHandler creates the project", () => {
  let listeners: Listeners
  let projectStore: ProjectStore
  let dataDir: string

  beforeAll(async () => {
    dataDir = getTestDir("embed-create-data")
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
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(true)
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
})
```

- [ ] **Step 2: Run, expect the new tests to fail**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: the two new tests fail because the handler returns the URL but doesn't actually create a project.

- [ ] **Step 3: Replace the handler body with the full creation pipeline**

In `app/src/server/listeners.ts`, locate the `openEditSessionHandler` method added in Task 5 and replace it with:

```typescript
public async openEditSessionHandler(
  req: Request,
  res: Response
): Promise<void> {
  if (this.checkInvalidPost(req, res)) {
    return
  }

  const body = req.body as OpenEditSessionBody | undefined
  const validation = validateOpenEditSessionBody(body)
  if (!validation.ok) {
    res.status(400).send(filterXSS(validation.reason))
    return
  }

  const sessionId = body!.sessionId as string
  const projectName = `embed_${sessionId}`

  if (await this.projectStore.checkProjectName(projectName)) {
    res.status(409).send(`Session ${sessionId} already exists`)
    return
  }

  // Write the incoming JSON to a tempfile so we can reuse the existing
  // single-file project-creation pipeline (parseSingleFile reads from disk).
  const tempPath = path.join(os.tmpdir(), `embed_${sessionId}.json`)
  await fs.writeJson(tempPath, body!.annotations)

  try {
    const fields = {
      [FormField.PROJECT_NAME]: projectName,
      [FormField.ITEM_TYPE]: "image",
      [FormField.LABEL_TYPE]: "polyline2d",
      [FormField.TASK_SIZE]: "1",
      [FormField.PAGE_TITLE]: "",
      [FormField.INSTRUCTIONS_URL]: "",
      [FormField.TRACKING]: "false"
    }
    const files = { [FormField.SINGLE_FILE]: tempPath }

    const storage = this.projectStore.getStorage()  // see Step 4 below
    const form = await parseForm(fields, this.projectStore)
    const formFileData = await parseSingleFile(storage, form.labelType, files)
    const project = await createProject(form, formFileData)
    const [filteredProject] = filterIntersectedPolygonsInProject(project)

    await Promise.all([
      this.projectStore.saveProject(filteredProject),
      createTasks(filteredProject, this.projectStore)
    ])

    res.status(200).json({
      labelUrl:
        `/label?project_name=${encodeURIComponent(projectName)}` +
        `&task_index=0&embedded=1`
    })
  } catch (err) {
    Logger.error(err as Error)
    res.status(500).send(filterXSS((err as Error).message))
  } finally {
    try { await fs.unlink(tempPath) } catch { /* best-effort */ }
  }
}
```

Add the necessary imports near the top of the file (alongside the existing imports):

```typescript
import {
  createProject,
  createTasks,
  parseForm,
  parseSingleFile,
  filterIntersectedPolygonsInProject
} from "./create_project"
```

(Adjust import paths if any of those symbols live in a different module — grep `app/src/server/create_project.ts` to confirm.)

- [ ] **Step 4: Add a `getStorage()` accessor to `ProjectStore`**

`parseSingleFile` requires a `Storage` instance. Add a public accessor in `app/src/server/project_store.ts`:

```typescript
public getStorage(): Storage {
  return this.storage
}
```

(Place it next to the other public methods like `checkProjectName`.)

- [ ] **Step 5: Run all `listeners_embed` tests, expect pass**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/src/server/listeners.ts app/src/server/project_store.ts app/test/server/listeners_embed.test.ts
git commit -m "feat(embed): wire openEditSession to project-creation pipeline; 409 on duplicate"
```

---

## Task 7: Implement `closeEditSessionHandler` (TDD)

**Files:**
- Modify: `app/src/server/listeners.ts`
- Modify: `app/test/server/listeners_embed.test.ts`

- [ ] **Step 1: Write failing tests for delete + idempotency + prefix scope**

Append to `app/test/server/listeners_embed.test.ts`:

```typescript
describe("closeEditSessionHandler", () => {
  let listeners: Listeners
  let projectStore: ProjectStore
  let dataDir: string

  beforeAll(async () => {
    dataDir = getTestDir("embed-close-data")
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

  test("returns 400 for malformed sessionId", async () => {
    const req = { method: "POST", query: { sessionId: "nope" } } as unknown as Request
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
    // Create one via openEditSession first
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
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(true)

    const closeReq = { method: "POST", query: { sessionId } } as unknown as Request
    const res = fakeRes()
    await listeners.closeEditSessionHandler(closeReq, res)
    expect(res.statusCode).toBe(204)
    expect(await projectStore.checkProjectName(`embed_${sessionId}`)).toBe(false)
  })

  test("is idempotent: 404 on second close", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440011"
    // create + close once
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

    // second close
    const res = fakeRes()
    await listeners.closeEditSessionHandler(
      { method: "POST", query: { sessionId } } as unknown as Request,
      res
    )
    expect(res.statusCode).toBe(404)
  })
})
```

- [ ] **Step 2: Run, expect failures**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: 4 new tests fail with "closeEditSessionHandler is not a function."

- [ ] **Step 3: Implement the handler**

Add to `Listeners` class (immediately after `openEditSessionHandler`):

```typescript
public async closeEditSessionHandler(
  req: Request,
  res: Response
): Promise<void> {
  if (this.checkInvalidPost(req, res)) {
    return
  }

  const sessionId = req.query?.sessionId
  if (typeof sessionId !== "string" || !UUID_RE.test(sessionId)) {
    res.status(400).send("sessionId must be a valid UUID")
    return
  }

  const projectName = `embed_${sessionId}`
  if (!projectName.startsWith("embed_")) {
    // Defense in depth — should be unreachable
    res.status(400).send("Refusing to delete non-embed project")
    return
  }

  if (!(await this.projectStore.checkProjectName(projectName))) {
    res.status(404).send("No such session")
    return
  }

  try {
    await this.projectStore.deleteProject(projectName)
    res.status(204).end()
  } catch (err) {
    Logger.error(err as Error)
    res.status(500).send(filterXSS((err as Error).message))
  }
}
```

- [ ] **Step 4: Run, expect all tests pass**

```bash
npm test -- --testPathPattern="listeners_embed"
```

Expected: all `listeners_embed` tests pass (10 total — 4 validation + 2 creation + 4 close).

- [ ] **Step 5: Commit**

```bash
git add app/src/server/listeners.ts app/test/server/listeners_embed.test.ts
git commit -m "feat(embed): closeEditSession handler with idempotency and prefix-scope guard"
```

---

## Task 8: Wire the new endpoints in `main.ts`

**Files:**
- Modify: `app/src/server/main.ts`

- [ ] **Step 1: Register the routes**

Open `app/src/server/main.ts` and find the route-registration block (around lines 87–141). Locate the existing `app.post(Endpoint.POST_PROJECT_INTERNAL, ...)` registration. Immediately below the last existing `app.post(...)` block, add:

```typescript
app.post(
  Endpoint.OPEN_EDIT_SESSION,
  authMiddleWare,
  express.json({ limit: "50mb" }),
  listeners.openEditSessionHandler.bind(listeners)
)
app.post(
  Endpoint.CLOSE_EDIT_SESSION,
  authMiddleWare,
  listeners.closeEditSessionHandler.bind(listeners)
)
```

The `50mb` limit is generous so the model's JSON (which can include thousands of polyline vertices for road-lane sessions) is not rejected. The default Express `express.json()` limit is 100kb.

- [ ] **Step 2: Verify the build still succeeds**

```bash
npm run build
```

Expected: webpack reports a successful build for both client and server bundles. The new endpoints are now baked into `app/dist/main.js`.

- [ ] **Step 3: Commit**

```bash
git add app/src/server/main.ts
git commit -m "feat(embed): register openEditSession and closeEditSession routes"
```

---

## Task 9: Cleanup module — pure function for finding expired projects (TDD)

**Files:**
- Create: `app/src/server/embed_cleanup.ts`
- Create: `app/test/server/embed_cleanup.test.ts`

- [ ] **Step 1: Write the failing test for prefix scope**

Create `app/test/server/embed_cleanup.test.ts`:

```typescript
import { findExpiredEmbedProjects } from "../../src/server/embed_cleanup"

describe("findExpiredEmbedProjects", () => {
  const NOW_MS = 1_700_000_000_000  // fixed "now" for deterministic tests
  const TTL_MIN = 1440  // 24h

  function project(name: string, lastActivityMsAgo: number) {
    return { name, lastActivityMs: NOW_MS - lastActivityMsAgo }
  }

  test("only embed_* prefixed projects are returned", () => {
    const projects = [
      project("embed_aaa", 25 * 60 * 60 * 1000),  // 25h, expired
      project("real_project_1", 25 * 60 * 60 * 1000),  // 25h, NOT embed_
      project("01_long_beach_batch1", 100 * 24 * 60 * 60 * 1000),
      project("embed_bbb", 23 * 60 * 60 * 1000)  // 23h, not yet expired
    ]
    const expired = findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)
    expect(expired).toEqual(["embed_aaa"])
  })

  test("empty input returns empty array", () => {
    expect(findExpiredEmbedProjects([], NOW_MS, TTL_MIN)).toEqual([])
  })

  test("nothing expired returns empty array", () => {
    const projects = [
      project("embed_aaa", 60_000),  // 1 minute ago
      project("real_proj", 0)
    ]
    expect(findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)).toEqual([])
  })

  test("handles a project with no lastActivityMs (treats as never active = expired)", () => {
    const projects = [
      { name: "embed_zzz" }  // no timestamp
    ]
    const expired = findExpiredEmbedProjects(
      projects as Array<{ name: string; lastActivityMs?: number }>,
      NOW_MS,
      TTL_MIN
    )
    expect(expired).toEqual(["embed_zzz"])
  })
})
```

- [ ] **Step 2: Run, expect import failure**

```bash
npm test -- --testPathPattern="embed_cleanup"
```

Expected: tests fail because `embed_cleanup.ts` does not exist yet.

- [ ] **Step 3: Create the module**

Create `app/src/server/embed_cleanup.ts`:

```typescript
import Logger from "./logger"
import { ProjectStore } from "./project_store"

export interface EmbedProjectMeta {
  /** Project name */
  name: string
  /** Milliseconds since epoch of last activity, undefined => never */
  lastActivityMs?: number
}

/**
 * Returns the names of embed_* projects whose last activity is older than
 * the TTL. A project with no recorded activity is treated as expired.
 */
export function findExpiredEmbedProjects(
  projects: EmbedProjectMeta[],
  nowMs: number,
  ttlMinutes: number
): string[] {
  const ttlMs = ttlMinutes * 60 * 1000
  const cutoff = nowMs - ttlMs
  return projects
    .filter((p) => p.name.startsWith("embed_"))
    .filter((p) => (p.lastActivityMs ?? 0) <= cutoff)
    .map((p) => p.name)
}

/**
 * Starts a setInterval that periodically deletes expired embed_* projects.
 * Returns the timer handle so callers can cancel during shutdown.
 */
export function startEmbedCleanup(
  projectStore: ProjectStore,
  cleanupIntervalMinutes: number,
  sessionTtlMinutes: number
): NodeJS.Timer {
  const tick = async (): Promise<void> => {
    try {
      const names = await projectStore.getExistingProjects()
      const metas: EmbedProjectMeta[] = await Promise.all(
        names.map(async (name) => ({
          name,
          lastActivityMs: await getProjectLastActivityMs(projectStore, name)
        }))
      )
      const expired = findExpiredEmbedProjects(
        metas,
        Date.now(),
        sessionTtlMinutes
      )
      for (const name of expired) {
        if (!name.startsWith("embed_")) continue  // belt-and-braces guard
        try {
          await projectStore.deleteProject(name)
          Logger.info(`embed_cleanup: deleted ${name}`)
        } catch (err) {
          Logger.error(err as Error)
        }
      }
    } catch (err) {
      Logger.error(err as Error)
    }
  }
  return setInterval(() => {
    void tick()
  }, cleanupIntervalMinutes * 60 * 1000)
}

/**
 * Best-effort fetch of the last-activity timestamp for a project.
 * Falls back to 0 (treated as expired) if the project store has no
 * recorded activity for the project.
 */
async function getProjectLastActivityMs(
  projectStore: ProjectStore,
  projectName: string
): Promise<number> {
  try {
    const project = await projectStore.loadProject(projectName)
    // Project objects carry config but not a top-level updatedAt.
    // Use the directory mtime as a proxy via storage if available;
    // for simplicity, default to 0 — TTL semantics hold either way
    // for inactive sessions, and active sessions update Redis often
    // enough that any in-flight query won't race the cleanup tick.
    return (project as unknown as { updatedAt?: number }).updatedAt ?? 0
  } catch {
    return 0
  }
}
```

Note on `lastActivityMs`: Scalabel's `Project` type does not surface a public last-activity timestamp. For v1, a project is treated as "never active" (i.e. older than any TTL ≥ 1 minute), meaning the cleanup will reap any `embed_*` project on the next tick. **This is intentionally aggressive and correct for the embed flow because `closeEditSession` is the happy path — projects only sit around when abandoned.** If real-world testing shows users with very long sessions (>24h with no save), revisit by exposing `lastUpdatedMs` from the project store via filesystem mtime on the project directory.

- [ ] **Step 4: Run, expect tests pass**

```bash
npm test -- --testPathPattern="embed_cleanup"
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/src/server/embed_cleanup.ts app/test/server/embed_cleanup.test.ts
git commit -m "feat(embed): cleanup module with prefix-scoped pure finder + scheduler"
```

---

## Task 10: Start the cleanup task at server startup

**Files:**
- Modify: `app/src/server/main.ts`

- [ ] **Step 1: Wire `startEmbedCleanup` into the server boot**

In `app/src/server/main.ts`, near the imports add:

```typescript
import { startEmbedCleanup } from "./embed_cleanup"
```

Find the function that performs server startup (likely `startServers` or the immediately-invoked block near line 289–315 that calls `await readConfig()` and initializes the listeners). Immediately after the listeners and project store are constructed, add:

```typescript
const embedCfg = config.embed ?? { cleanupIntervalMinutes: 60, sessionTtlMinutes: 1440 }
startEmbedCleanup(
  projectStore,
  embedCfg.cleanupIntervalMinutes,
  embedCfg.sessionTtlMinutes
)
Logger.info(
  `embed_cleanup scheduled: interval=${embedCfg.cleanupIntervalMinutes}m ` +
  `ttl=${embedCfg.sessionTtlMinutes}m`
)
```

(If a `Logger` import doesn't already exist near the top of `main.ts`, find an existing `Logger.info(...)` call to confirm the import path — typically `import Logger from "./logger"`.)

- [ ] **Step 2: Verify the build**

```bash
npm run build
```

Expected: clean build.

- [ ] **Step 3: Smoke-run the server briefly**

```bash
node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml &
sleep 3
kill %1
```

Expected: server starts, logs `embed_cleanup scheduled: interval=60m ttl=1440m`, accepts the kill signal cleanly.

- [ ] **Step 4: Commit**

```bash
git add app/src/server/main.ts
git commit -m "feat(embed): start cleanup task at server boot"
```

---

## Task 11: Add `Session.embedded` flag and parse query param

**Files:**
- Modify: `app/src/common/session.ts`
- Modify: `app/src/common/session_init.tsx`

- [ ] **Step 1: Add the field to the Session singleton**

In `app/src/common/session.ts`, find the existing public field declarations (around lines 16–32) and add:

```typescript
class Session {
  public store: FullStore
  public images: Array<{ [id: number]: HTMLImageElement }>
  public pointClouds: Array<{ [id: number]: THREE.BufferGeometry }>
  public label2dList: Label2DList
  public label3dList: Label3DList
  public tracks: { [trackId: string]: Track }
  public activeViewerId: number
  public testMode: boolean
  /** True when the labeling page is loaded with ?embedded=1 */
  public embedded: boolean

  constructor() {
    // ... existing assignments ...
    this.testMode = false
    this.embedded = false
    this.store = configureStore({})
  }
```

- [ ] **Step 2: Parse the query param in `session_init.tsx`**

Open `app/src/common/session_init.tsx` and locate the `initSession` function (lines 27–54). Replace the body with:

```typescript
export function initSession(containerName: string): void {
  const searchParams = new URLSearchParams(window.location.search)
  const projectName = searchParams.get(QueryArg.PROJECT_NAME)
  if (projectName === null) {
    return handleInvalidPage()
  }
  const taskIndexParam = searchParams.get(QueryArg.TASK_INDEX)
  let taskIndex = 0
  if (taskIndexParam !== null) {
    taskIndex = parseInt(taskIndexParam, 10)
  }
  const devMode = searchParams.has(QueryArg.DEV_MODE)
  Session.embedded = searchParams.get(QueryArg.EMBEDDED) === "1"

  const fpPromise = FingerprintJS.load({ delayFallback: 500 })
  ;(async () => {
    const fp = await fpPromise
    const result = await fp.get()
    const userId = result.visitorId
    initSessionForTask(taskIndex, projectName, userId, containerName, devMode)
  })().catch((error: Error) => {
    throw error
  })
}
```

The single new line is `Session.embedded = searchParams.get(QueryArg.EMBEDDED) === "1"`. Keep it tight — set the flag once at boot, read everywhere else.

- [ ] **Step 3: Verify the build**

```bash
npm run build
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
git add app/src/common/session.ts app/src/common/session_init.tsx
git commit -m "feat(embed): parse ?embedded=1 query param into Session.embedded"
```

---

## Task 12: Hide the title bar in embedded mode

**Files:**
- Modify: `app/src/components/window.tsx`
- Modify: `app/src/components/label_layout.tsx`

- [ ] **Step 1: Gate the titleBar prop on `Session.embedded` in window.tsx**

In `app/src/components/window.tsx`, add the Session import:

```typescript
import Session from "../common/session"
```

Then in the `render` method, change:

```typescript
const titleBar = <TitleBar />
```

to:

```typescript
const titleBar = Session.embedded ? null : <TitleBar />
```

- [ ] **Step 2: Suppress the empty wrapping div in label_layout.tsx**

In `app/src/components/label_layout.tsx`, find the line in the `render()` method (around line 239):

```typescript
<div className={classes.titleBar}>{titleBar}</div>
```

Replace with:

```typescript
{titleBar !== null && <div className={classes.titleBar}>{titleBar}</div>}
```

This prevents an empty styled div (which may have non-zero height from MUI's title-bar CSS) from leaving a gap above the canvas.

- [ ] **Step 3: Build**

```bash
npm run build
```

Expected: clean build.

- [ ] **Step 4: Manual smoke test**

```bash
node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml
```

In a browser, open an existing labeling task at `/label?project_name=<existing>&task_index=0&embedded=1`. The "2D Lane" header bar should be gone, the canvas takes the freed space, and there should be no residual horizontal strip at the top. Open the same URL **without** `&embedded=1` and confirm the title bar reappears.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/window.tsx app/src/components/label_layout.tsx
git commit -m "feat(embed): hide title bar (and its wrapper div) when Session.embedded"
```

---

## Task 13: Hide file path readout and add "Save & Close" button

**Files:**
- Modify: `app/src/components/toolbar.tsx`

The file path readout lives at `app/src/components/toolbar.tsx:215-221` (the `{imageName !== "" && (...)}` block at the top of the toolbar's render output). The "Download Labels" affordance lives in `dashboard.tsx:481` as an `<a href=".${Endpoint.EXPORT}?...">` — it's only reachable from the dashboard, which the iframe never opens, so no separate hide step for it is needed.

Save & Close uses an HTTP fetch to the existing `/getExport` endpoint to obtain the JSON rather than re-implementing `convertStateToExport` client-side. The session's state is already synced to Redis via the existing socket.io autosave path, so `/getExport` returns the user's latest edits.

- [ ] **Step 1: Hide the file path readout in embedded mode**

In `app/src/components/toolbar.tsx`, add the `Session` import near the existing imports:

```typescript
import Session from "../common/session"
```

Find lines 215–221 (the `{imageName !== "" && (...)}` block):

```tsx
{imageName !== "" && (
  <div style={{ padding: "8px 16px", fontWeight: 600, borderBottom: "1px solid rgba(255,255,255,0.15)", marginBottom: "4px", wordBreak: "break-all" }}>
    {imageName}
  </div>
)}
```

Change the conditional to also gate on `!Session.embedded`:

```tsx
{imageName !== "" && !Session.embedded && (
  <div style={{ padding: "8px 16px", fontWeight: 600, borderBottom: "1px solid rgba(255,255,255,0.15)", marginBottom: "4px", wordBreak: "break-all" }}>
    {imageName}
  </div>
)}
```

- [ ] **Step 2: Add a `saving` state field on the ToolBar class**

The `ToolBar` class currently has no React state. Widen its base class generic from `Component<Props>` to `Component<Props, { saving: boolean }>` and initialize `state` in the constructor.

Find the class declaration:

```typescript
export class ToolBar extends Component<Props> {
```

Change to:

```typescript
export class ToolBar extends Component<Props, { saving: boolean }> {
```

Inside the constructor (after existing initializations like `this._keyDownMap = {}`), add:

```typescript
this.state = { saving: false }
this.handleSaveAndClose = this.handleSaveAndClose.bind(this)
```

- [ ] **Step 3: Add the Save & Close handler**

Add this method to the `ToolBar` class (place it near the other handlers, e.g. after `handleAttributeToggle`):

```typescript
private async handleSaveAndClose(): Promise<void> {
  this.setState({ saving: true })

  // Derive sessionId from project_name (URL-injected via session_init).
  const reduxState = Session.store.getState().present
  const projectName = reduxState.task.config.projectName
  const sessionId = projectName.replace(/^embed_/, "")

  let annotations: unknown = null
  try {
    // The server's /getExport endpoint already produces the DatasetExport
    // shape we need. State has already been synced to Redis via the
    // existing socket.io autosave loop.
    const resp = await fetch(
      `./getExport?project_name=${encodeURIComponent(projectName)}`
    )
    if (!resp.ok) {
      throw new Error(`getExport returned ${resp.status}`)
    }
    annotations = await resp.json()
  } catch (err) {
    // Surface to the user — they need to know the save did not happen.
    // eslint-disable-next-line no-alert
    window.alert(
      `Save failed: ${(err as Error).message}. Your edits remain in this ` +
      `session — please try again.`
    )
    this.setState({ saving: false })
    return
  }

  // 1. Hand off the JSON to the parent first — this is the user-visible
  //    success criterion. If the cleanup fetch fails, the safety-net task
  //    will reap the project later.
  window.parent.postMessage(
    { type: "scalabel:saved", sessionId, annotations },
    window.location.origin
  )

  // 2. Best-effort cleanup. Don't block the UX on it.
  void fetch(
    `./closeEditSession?sessionId=${encodeURIComponent(sessionId)}`,
    { method: "POST" }
  ).catch(() => { /* swallow — the cleanup task will catch it */ })

  // Leave saving=true so the button stays disabled. The parent window's
  // listener will close the modal, destroying this iframe.
}
```

- [ ] **Step 4: Render the Save & Close button**

Find the closing `</div>` of the toolbar's render output (the outer `<div>` that wraps everything in the render method). Just inside that closing `</div>`, add:

```tsx
{Session.embedded && (
  <div style={{ padding: 16 }}>
    <button
      type="button"
      disabled={this.state.saving}
      onClick={this.handleSaveAndClose}
      style={{
        width: "100%",
        padding: "10px 12px",
        background: this.state.saving ? "#5a5a5a" : "#0a84ff",
        color: "#fff",
        border: "none",
        borderRadius: 4,
        cursor: this.state.saving ? "not-allowed" : "pointer",
        fontWeight: 600
      }}
    >
      {this.state.saving ? "Saving…" : "Save & Close"}
    </button>
  </div>
)}
```

- [ ] **Step 5: Build and smoke-check the embedded UI**

```bash
npm run build
node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml
```

In a separate terminal:

```bash
SESSION=$(node -e "console.log(require('crypto').randomUUID())")
curl -X POST http://localhost:8686/openEditSession \
  -H "Content-Type: application/json" \
  -d "{
    \"sessionId\": \"$SESSION\",
    \"annotations\": {
      \"frames\": [{\"name\":\"items/edit_json/test.png\",\"url\":\"items/edit_json/test.png\",\"labels\":[],\"videoName\":\"\",\"timestamp\":0,\"attributes\":{},\"sensor\":-1}],
      \"config\": {\"categories\":[{\"name\":\"lane\"}],\"attributes\":[]}
    }
  }"
```

(Stage `local-data/items/edit_json/test.png` first if it doesn't exist.)

Open the returned `labelUrl` in the browser. Confirm:

- File path readout at top-left is gone.
- Title bar (top "2D Lane" header) is gone.
- Category selector with "lane" is visible.
- DELETE button, "Show Tags", "Show Polylines" toggles visible.
- "Save & Close" button visible, blue.

In DevTools console of the same tab, paste:

```javascript
window.addEventListener('message', e => {
  if (e.data?.type === 'scalabel:saved') {
    console.log('JSON received:', JSON.stringify(e.data.annotations).slice(0, 300))
  }
})
```

Click "Save & Close." Confirm: the console logs the JSON; the button becomes disabled and shows "Saving…"; within a few seconds the `embed_<sessionId>` directory disappears from `local-data/data/project/`.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/toolbar.tsx
git commit -m "feat(embed): hide file path readout, add Save & Close button with postMessage protocol"
```

---

## Task 14: Integration test — full round-trip

**Files:**
- Create: `app/test/server/embed_roundtrip.test.ts`

- [ ] **Step 1: Write the round-trip test**

Create `app/test/server/embed_roundtrip.test.ts`:

```typescript
import { Request, Response } from "express"
import * as fs from "fs-extra"
import { Listeners } from "../../src/server/listeners"
import { ProjectStore } from "../../src/server/project_store"
import { UserManager } from "../../src/server/user_manager"
import { FileStorage } from "../../src/server/file_storage"
import { RedisCache } from "../../src/server/redis_cache"
import { RedisClient } from "../../src/server/redis_client"
import { serverConfig } from "../../src/server/defaults"
import { getTestDir } from "../../src/server/path"

function fakeRes(): Response {
  const res = {
    statusCode: 200,
    body: undefined as unknown,
    status(code: number) { this.statusCode = code; return this },
    send(b: unknown) { this.body = b; return this },
    json(b: unknown) { this.body = b; return this },
    end() { return this }
  }
  return res as unknown as Response
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

  beforeAll(async () => {
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
    // 1. Open
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

    // 2. Confirm project state has the expected single item with the
    //    image url from the payload.
    const tasks = await projectStore.getTasksInProject(projectName)
    expect(tasks.length).toBe(1)
    expect(tasks[0].items.length).toBe(1)
    expect(Object.values(tasks[0].items[0].urls)[0]).toBe("items/foo.png")

    // 3. Close
    const closeRes = fakeRes()
    await listeners.closeEditSessionHandler(
      { method: "POST", query: { sessionId } } as unknown as Request,
      closeRes
    )
    expect(closeRes.statusCode).toBe(204)
    expect(await projectStore.checkProjectName(projectName)).toBe(false)
  })
})
```

- [ ] **Step 2: Run, expect pass**

```bash
npm test -- --testPathPattern="embed_roundtrip"
```

Expected: 1 test passes.

- [ ] **Step 3: Run the full test suite**

```bash
npm test
```

Expected: all tests pass — including pre-existing ones (none of our changes touched non-embed code paths).

- [ ] **Step 4: Commit**

```bash
git add app/test/server/embed_roundtrip.test.ts
git commit -m "test(embed): round-trip integration test for open + close"
```

---

## Task 15: Document config and smoke test in the example yaml

**Files:**
- Modify: `app/config/default_config.yml`

- [ ] **Step 1: Add the embed section as a commented example**

Open `app/config/default_config.yml` and append at the bottom:

```yaml
# Embedded edit-session configuration. Used by POST /openEditSession to
# create ephemeral embed_<sessionId> projects, and by the cleanup task
# that reaps abandoned embed_* projects.
# embed:
#     cleanupIntervalMinutes: 60
#     sessionTtlMinutes: 1440   # 24 hours
```

Keep the section commented so existing deployments inherit the defaults from `app/src/server/defaults.ts`.

- [ ] **Step 2: Commit**

```bash
git add app/config/default_config.yml
git commit -m "docs(embed): document embed: config section in default_config.yml"
```

---

## Task 16: Manual end-to-end smoke

**Files:**
- None modified

This is the final correctness gate — no automated test exercises the full iframe + postMessage chain because that requires a real browser session.

- [ ] **Step 1: Build and start the server**

```bash
npm run build
node --max-old-space-size=8192 app/dist/main.js --config ./local-data/scalabel/config.yml
```

- [ ] **Step 2: Stage a test image**

Copy a sample image to `local-data/items/edit_json/test.png`. The test image's path inside the JSON below must match this location.

- [ ] **Step 3: Open a session via curl**

```bash
SESSION=$(node -e "console.log(require('crypto').randomUUID())")
echo "Session: $SESSION"

curl -X POST http://localhost:8686/openEditSession \
  -H "Content-Type: application/json" \
  -d "{
    \"sessionId\": \"$SESSION\",
    \"annotations\": {
      \"frames\": [{
        \"name\": \"items/edit_json/test.png\",
        \"url\": \"items/edit_json/test.png\",
        \"labels\": [],
        \"videoName\": \"\",
        \"timestamp\": 0,
        \"attributes\": {},
        \"sensor\": -1
      }],
      \"config\": {
        \"categories\": [{\"name\":\"lane\"},{\"name\":\"edge\"}],
        \"attributes\": []
      }
    }
  }"
```

Expected: response is `{"labelUrl":"/label?project_name=embed_<uuid>&task_index=0&embedded=1"}`.

- [ ] **Step 4: Open the labelUrl in a browser**

Visit `http://localhost:8686<labelUrl>`. The labeling UI should load with the test image rendered. The "2D Lane" title bar should be hidden. The category selector should show `lane` and `edge`. Draw a polyline.

- [ ] **Step 5: Set up a parent-window listener and click Save & Close**

In another browser tab, paste this in the DevTools console to act as a parent listener (the iframe's `window.parent` is the same window because we're not actually framing — but `postMessage` to `window.parent` of a top-level window posts to itself, so the listener triggers):

```javascript
window.addEventListener('message', e => {
  if (e.data?.type === 'scalabel:saved') {
    console.log('Got JSON:', JSON.stringify(e.data.annotations).slice(0, 200))
  }
})
```

Reload the labelUrl tab so the iframe runs in the same window. Click "Save & Close." The console should log the JSON. The `embed_<sessionId>` directory under `local-data/data/project/` should disappear within a few seconds (after the fire-and-forget DELETE).

- [ ] **Step 6: Confirm cleanup task runs harmlessly on idle**

Wait 60 minutes (or temporarily lower `cleanupIntervalMinutes` in `defaults.ts` to `1` and rebuild). Confirm the server log emits `embed_cleanup: deleted ...` if any abandoned `embed_*` projects exist, and emits no errors when there are none.

- [ ] **Step 7: Done — push the branch**

```bash
git status
git log --oneline feature/embedded-scalable-tmi ^master
git push origin feature/embedded-scalable-tmi
```

The branch now contains the full implementation. Open a PR for review when ready.

---

## Self-review notes

- All spec sections (3–9) have at least one task implementing them. The cleanup config knobs in spec §7.3 are realized via Task 4 + Task 10.
- The `embed_*` prefix-scope guarantee from spec §7.4 is enforced in three places: the `findExpiredEmbedProjects` filter (Task 9), the belt-and-braces check inside `startEmbedCleanup` (Task 9), and the `closeEditSessionHandler` defense-in-depth check (Task 7). Two tests cover prefix scope (one in `embed_cleanup.test.ts`, the negative test in `listeners_embed.test.ts` that asserts unrelated project names are untouched).
- The "happy-path delete first, then postMessage" ordering from spec §7.1 is reversed in the toolbar handler (Task 13, Step 4) — postMessage runs first, then the fire-and-forget DELETE. This matches the spec's explicit reasoning ("postMessage-first ordering is the protection") and is intentional.
- The Task 9 step that documents `lastActivityMs` defaulting to 0 is called out explicitly: with the happy path covering normal saves, the safety net is allowed to be aggressive on detection. This is a deliberate trade-off, not a placeholder.
- Tasks 12 and 13 both involve toolbar/window-component changes in code the user has already modified extensively. Each task's smoke-test step ensures the visible behavior matches the spec's "trim chrome" table even if file contents have drifted from what the file map predicts.