/**
 * Tracks per-task annotation-correction progress.
 *
 * Correcting a whole project is slow — roughly 6 seconds per frame at full
 * resolution, so a 221-frame project runs about 22 minutes. That is far beyond
 * the 10-minute browser submission timeout, so correction cannot happen inline
 * during project creation: the request would abort and no project would be
 * created at all.
 *
 * Instead, project creation returns immediately and correction runs in the
 * background one task at a time. Because a project is already split into tasks
 * (50 frames each by default), the first task finishes in a few minutes and can
 * be annotated while the rest are still being corrected.
 *
 * Status lives in memory only. A server restart mid-correction leaves tasks
 * marked pending forever, so `loadStatus` treats an unknown task as ready
 * rather than blocking it — the annotations are still there, just uncorrected.
 */

/** How a task's correction ended up. */
export enum CorrectionState {
  /** queued, not started */
  PENDING = "pending",
  /** currently being corrected */
  RUNNING = "running",
  /** corrected successfully */
  READY = "ready",
  /** correction failed; the task holds its original annotations */
  FAILED = "failed"
}

/**
 * Correction status for one task.
 */
export interface TaskCorrectionStatus {
  /** where this task is in the pipeline */
  state: CorrectionState
  /** vertices pulled back inside the region of interest */
  clamped?: number
  /** polyline pairs joined into one label */
  merged?: number
  /** why the correction failed, when it did */
  error?: string
}

/** projectName -> taskId -> status */
const statuses = new Map<string, Map<string, TaskCorrectionStatus>>()

/**
 * Record a task's correction status.
 *
 * @param projectName the project the task belongs to
 * @param taskId the task's id
 * @param status what to record
 */
export function setTaskStatus(
  projectName: string,
  taskId: string,
  status: TaskCorrectionStatus
): void {
  let project = statuses.get(projectName)
  if (project === undefined) {
    project = new Map<string, TaskCorrectionStatus>()
    statuses.set(projectName, project)
  }
  project.set(taskId, status)
}

/**
 * Read one task's correction status.
 *
 * Returns undefined when nothing is tracked — either the project was created
 * without auto-correct, or the server restarted. Callers must treat that as
 * ready: annotations exist either way, and blocking on lost state would strand
 * the task permanently.
 *
 * @param projectName the project the task belongs to
 * @param taskId the task's id
 */
export function getTaskStatus(
  projectName: string,
  taskId: string
): TaskCorrectionStatus | undefined {
  return statuses.get(projectName)?.get(taskId)
}

/**
 * Read every tracked task status for a project.
 *
 * @param projectName the project to read
 */
export function getProjectStatuses(projectName: string): {
  [taskId: string]: TaskCorrectionStatus
} {
  const project = statuses.get(projectName)
  const out: { [taskId: string]: TaskCorrectionStatus } = {}
  if (project !== undefined) {
    for (const [taskId, status] of project) {
      out[taskId] = status
    }
  }
  return out
}

/**
 * Mark every task in a project as queued for correction.
 *
 * Called before the background work starts so the dashboard shows the whole
 * project as pending straight away, rather than tasks appearing one at a time.
 *
 * @param projectName the project being corrected
 * @param taskIds every task id in the project
 */
export function initProjectStatuses(
  projectName: string,
  taskIds: string[]
): void {
  for (const taskId of taskIds) {
    setTaskStatus(projectName, taskId, { state: CorrectionState.PENDING })
  }
}

/**
 * Forget a project's statuses, freeing the memory once it is fully corrected.
 *
 * @param projectName the project to drop
 */
export function clearProjectStatuses(projectName: string): void {
  statuses.delete(projectName)
}
