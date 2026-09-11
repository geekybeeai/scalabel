/**
 * Corrects a project's annotations in the background, one task at a time.
 *
 * Correction is slow — several seconds per frame at full resolution, so a
 * 221-frame project runs for many minutes. That is well past the 10-minute
 * browser submission timeout, so it cannot happen inline during project
 * creation: the request would abort and no project would be created at all.
 *
 * So project creation returns immediately and the work happens here. A project
 * is already split into tasks (50 frames each by default), so correcting one
 * task at a time means the first becomes available within a few minutes while
 * the rest are still queued.
 *
 * Correction runs on the RAW export frames and the task is then rebuilt from
 * them with `createTasks`, reusing the exact conversion project creation uses.
 * Correcting already-converted tasks would mean an internal -> export ->
 * internal round trip, which risks losing whatever the conversion does not
 * round-trip cleanly.
 *
 * Sequential on purpose, though the reason has weakened. Under the Python
 * implementation a single full-resolution frame could hold several GB while its
 * mask was built, and concurrency risked exhausting memory on exactly the
 * largest images. The TypeScript implementation streams the image and peaks at
 * roughly one byte per pixel — about 340 MB for the largest frame in the corpus
 * — so memory no longer forbids running tasks in parallel. What still argues
 * for sequential is that the work is CPU-bound in a worker thread and finishing
 * the first task soonest is more useful than finishing all of them together.
 */

import { Project } from "../types/project"
import { ItemExport } from "../types/export"
import { correctAnnotations } from "./annotation_fix"
import {
  CorrectionState,
  clearProjectStatuses,
  initProjectStatuses,
  setTaskStatus
} from "./correction_status"
import Logger from "./logger"

/**
 * How a project's frames are grouped into tasks.
 */
export interface TaskChunk {
  /** the task's id, matching what createTasks assigns */
  taskId: string
  /** index of this task within the project */
  taskIndex: number
  /** the frames belonging to this task */
  items: Array<Partial<ItemExport>>
}

/**
 * Split a project's items into per-task chunks.
 *
 * Mirrors how `createTasks` partitions items, so chunk N corresponds to task N
 * and the task ids line up with the ones already saved on disk.
 *
 * @param project the project whose items to split
 * @param indexToId renders a task index as the id createTasks used
 */
export function splitIntoTaskChunks(
  project: Project,
  indexToId: (index: number) => string
): TaskChunk[] {
  const taskSize = project.config.taskSize
  const chunks: TaskChunk[] = []
  if (taskSize <= 0) {
    return chunks
  }
  for (let start = 0; start < project.items.length; start += taskSize) {
    const taskIndex = chunks.length
    chunks.push({
      taskId: indexToId(taskIndex),
      taskIndex,
      items: project.items.slice(start, start + taskSize)
    })
  }
  return chunks
}

/**
 * Correct every task in a project, in the background.
 *
 * Never throws and never rejects: this runs detached from the request that
 * started it, so a failure has nowhere to surface except the log and the
 * task's own status. A task whose correction fails keeps the annotations it was
 * created with and is marked FAILED, so a broken helper never blocks work.
 *
 * @param project the project being corrected
 * @param chunks the project's items grouped per task
 * @param rebuildTask rebuilds and saves one task from corrected frames
 */
export function correctProjectInBackground(
  project: Project,
  chunks: TaskChunk[],
  rebuildTask: (
    chunk: TaskChunk,
    corrected: Array<Partial<ItemExport>>
  ) => Promise<void>
): void {
  if (chunks.length === 0) {
    return
  }

  const projectName = project.config.projectName
  initProjectStatuses(
    projectName,
    chunks.map((chunk) => chunk.taskId)
  )

  // Detached on purpose: the caller has already responded to the browser.
  void (async () => {
    Logger.info(
      `Annotation auto-correct: starting ${chunks.length} task(s) for ` +
        `project ${projectName}`
    )

    for (const chunk of chunks) {
      setTaskStatus(projectName, chunk.taskId, {
        state: CorrectionState.RUNNING
      })

      try {
        const before = chunk.items.reduce(
          (total, item) => total + (item.labels?.length ?? 0),
          0
        )
        const corrected = await correctAnnotations(chunk.items)
        const after = corrected.reduce(
          (total, item) => total + (item.labels?.length ?? 0),
          0
        )

        await rebuildTask(chunk, corrected)

        setTaskStatus(projectName, chunk.taskId, {
          state: CorrectionState.READY,
          merged: Math.max(0, before - after)
        })
        Logger.info(
          `Annotation auto-correct: task ${chunk.taskId} ready ` +
            `(${Math.max(0, before - after)} lines merged)`
        )
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error)
        // The task keeps the annotations it was created with.
        setTaskStatus(projectName, chunk.taskId, {
          state: CorrectionState.FAILED,
          error: reason
        })
        Logger.error(
          new Error(
            `Annotation auto-correct failed for task ${chunk.taskId}: ${reason}`
          )
        )
      }
    }

    Logger.info(`Annotation auto-correct: finished project ${projectName}`)
    // Statuses only matter while work is outstanding. Dropping them makes every
    // task read as ready, which is correct once the run is over.
    clearProjectStatuses(projectName)
  })()
}
