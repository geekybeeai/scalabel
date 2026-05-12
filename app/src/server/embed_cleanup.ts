import * as fs from "fs-extra"

import Logger from "./logger"
import { getProjectDir } from "./path"
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
 *
 * Pure function — exported for unit testing without a live filesystem.
 *
 * @param projects
 * @param nowMs
 * @param ttlMinutes
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
 * Resolves the directory mtime of a project as a proxy for last-activity.
 * For LOCAL storage the project directory's mtime is bumped on each disk
 * writeback. Returns 0 on failure (treated as "never active" → eligible
 * for cleanup once the TTL has fully elapsed since epoch — effectively
 * harmless for embed_* projects, since they should not exist past their
 * TTL anyway).
 *
 * @param projectStore
 * @param projectName
 */
async function getProjectLastActivityMs(
  projectStore: ProjectStore,
  projectName: string
): Promise<number> {
  try {
    const storage = projectStore.getStorage()
    const projectDirAbs = storage.fullDir(getProjectDir(projectName))
    const stat = await fs.stat(projectDirAbs)
    return stat.mtimeMs
  } catch {
    return 0
  }
}

/**
 * Starts a setInterval that periodically deletes expired embed_* projects.
 * Returns the timer handle so callers can cancel during shutdown.
 *
 * @param projectStore
 * @param cleanupIntervalMinutes
 * @param sessionTtlMinutes
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
          lastActivityMs: name.startsWith("embed_")
            ? await getProjectLastActivityMs(projectStore, name)
            : undefined
        }))
      )
      const expired = findExpiredEmbedProjects(
        metas,
        Date.now(),
        sessionTtlMinutes
      )
      for (const name of expired) {
        if (!name.startsWith("embed_")) {
          // Belt-and-braces guard. Should be unreachable.
          continue
        }
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
