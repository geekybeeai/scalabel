import * as fs from "fs-extra"

import { createProject, createTasks } from "../../src/server/create_project"
import { serverConfig } from "../../src/server/defaults"
import { convertStateToExport } from "../../src/server/export"
import { FileStorage } from "../../src/server/file_storage"
import { getTestDir } from "../../src/server/path"
import { ProjectStore } from "../../src/server/project_store"
import { RedisCache } from "../../src/server/redis_cache"
import { RedisClient } from "../../src/server/redis_client"
import { CreationForm, FormFileData, Project } from "../../src/types/project"
import { State, TaskType } from "../../src/types/state"
import {
  sampleFormFileData,
  sampleFormImage,
  sampleFormVideo,
  sampleProjectAutolabel,
  sampleProjectAutolabel3dBox,
  sampleProjectAutolabelPolygon,
  sampleProjectImage,
  sampleProjectSensors,
  sampleProjectVideo,
  sampleTasksImage,
  // SampleTasksVideo,
  sampleVideoFormFileData
} from "../test_states/test_creation_objects"
import {
  sampleStateExportImage,
  sampleStateExportImage3dBox,
  sampleStateExportImagePolygon
} from "../test_states/test_export_objects"

jest.mock("../../src/server/redis_client")

let projectStore: ProjectStore
let dataDir: string

beforeAll(() => {
  dataDir = getTestDir("create-project-data")
  const storage = new FileStorage(dataDir)
  const client = new RedisClient(serverConfig.redis)
  jest.spyOn(client, "get").mockResolvedValue(null)
  const redisStore = new RedisCache(serverConfig.redis, storage, client)
  projectStore = new ProjectStore(storage, redisStore)
})

afterAll(() => {
  fs.removeSync(dataDir)
})

describe("test project.json creation", () => {
  test("standalone lane projects offer the manual striping categories", async () => {
    const original = [
      "double_yellow_line",
      "yellow_line",
      "continuous_white_line",
      "dashed_white_line",
      "curb_road_edge",
      "without_curb_road_edge",
      "crosswalk_line",
      "center_turn_lane"
    ]
    const treeCategories = original.map((name) => ({ name }))
    const project = await createProject(
      {
        ...sampleFormImage,
        projectName: "standalone_lanes",
        labelType: "polyline2d"
      },
      { ...sampleFormFileData, categories: treeCategories }
    )
    const expected = [
      ...original,
      "bike_lane_line",
      "cat_track_line",
      "elephant_track_line"
    ]
    expect(project.config.categories).toEqual(expected)
    expect(project.config.treeCategories.map(({ name }) => name)).toEqual(
      expected
    )

    const [task] = await createTasks(project, undefined, 0, 0, true)
    task.config.categories = original
    task.config.treeCategories = treeCategories
    await projectStore.saveTasks([task])

    const { config } = (
      await projectStore.loadState("standalone_lanes", task.config.taskId)
    ).task
    expect(config.categories).toEqual(expected)
    expect(config.treeCategories.map(({ name }) => name)).toEqual(expected)
  })

  test("image project creation", async () => {
    return await testProjectCreation(
      sampleFormImage,
      sampleProjectImage,
      sampleFormFileData
    )
  })

  test("video project creation", async () => {
    return await testProjectCreation(
      sampleFormVideo,
      sampleProjectVideo,
      sampleVideoFormFileData
    )
  })

  test("image project saving", async () => {
    return await testProjectSaving(sampleProjectImage)
  })

  test("video project saving", async () => {
    return await testProjectSaving(sampleProjectVideo)
  })
})

describe("test task.json creation", () => {
  test("task non-tracking creation", async () => {
    const tasks = await createTasks(sampleProjectImage, undefined, 0, 0, true)
    expect(tasks).toEqual(sampleTasksImage)
  })

  // TODO: Rewrite the track creation testing
  // test('test tracking creation', async () => {
  //   return createTasks(sampleProjectVideo).then((tasks) => {
  //     expect(tasks).toEqual(sampleTasksVideo)
  //   })
  // })
  test("task saving", async () => {
    return await testTaskSaving(sampleTasksImage)
  })
})

describe("create with auto labels", () => {
  test("import then export", async () => {
    return await createTasks(
      sampleProjectAutolabel,
      undefined,
      0,
      0,
      true
    ).then((tasks) => {
      // Only 1 task should be created
      const state: Partial<State> = {
        task: tasks[0]
      }
      const exportedItems = convertStateToExport(state as State)
      expect(exportedItems).toEqual(sampleStateExportImage)
    })
  })
  test("import then export for polygon", async () => {
    return await createTasks(
      sampleProjectAutolabelPolygon,
      undefined,
      0,
      0,
      true
    ).then((tasks) => {
      // Only 1 task should be created
      const state: Partial<State> = {
        task: tasks[0]
      }
      const exportedItems = convertStateToExport(state as State)
      expect(exportedItems).toEqual(sampleStateExportImagePolygon)
    })
  })

  test("import then export for 3d bounding box", async () => {
    return await createTasks(
      sampleProjectAutolabel3dBox,
      undefined,
      0,
      0,
      true
    ).then((tasks) => {
      // Only 1 task should be created
      const state: Partial<State> = {
        task: tasks[0]
      }
      const exportedItems = convertStateToExport(state as State)
      expect(exportedItems).toEqual(sampleStateExportImage3dBox)
    })
  })

  test("import then export for camera sensor", async () => {
    return await createTasks(sampleProjectSensors, undefined, 0, 0, true).then(
      (tasks) => {
        // Only 1 task should be created
        const state: Partial<State> = {
          task: tasks[0]
        }
        const exportedItems = convertStateToExport(state as State)
        expect(exportedItems).toEqual(sampleStateExportImage3dBox)
      }
    )
  })
})

/**
 * Tested that desired project is created from form
 *
 * @param sampleForm
 * @param sampleProject
 * @param formFileData
 */
async function testProjectCreation(
  sampleForm: CreationForm,
  sampleProject: Project,
  formFileData: FormFileData
): Promise<void> {
  return await createProject(sampleForm, formFileData).then((project) => {
    expect(project).toEqual(sampleProject)
  })
}

/**
 * Tests that project is saved correctly
 *
 * @param sampleProject
 */
async function testProjectSaving(sampleProject: Project): Promise<void> {
  await projectStore.saveProject(sampleProject)

  // Check that it saved correctly by loading it and comparing
  const loadedProject = await projectStore.loadProject(
    sampleProject.config.projectName
  )

  expect(loadedProject).toEqual(sampleProject)
}

/**
 * Tests that task is saved correctly
 *
 * @param sampleTasks
 */
async function testTaskSaving(sampleTasks: TaskType[]): Promise<void> {
  await projectStore.saveTasks(sampleTasks)

  // Check that tasks saved correctly by loading them and comparing
  for (const task of sampleTasks) {
    const projectName = task.config.projectName
    const taskId = task.config.taskId
    const loadedTask = await projectStore.loadTask(projectName, taskId)
    expect(loadedTask).toEqual(task)
  }
}
