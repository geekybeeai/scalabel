import { findExpiredEmbedProjects } from "../../src/server/embed_cleanup"

describe("findExpiredEmbedProjects", () => {
  const NOW_MS = 1_700_000_000_000
  const TTL_MIN = 1440 // 24h

  function project(
    name: string,
    lastActivityMsAgo: number
  ): { name: string; lastActivityMs: number } {
    return { name, lastActivityMs: NOW_MS - lastActivityMsAgo }
  }

  test("only embed_* prefixed projects are considered", () => {
    const projects = [
      project("embed_aaa", 25 * 60 * 60 * 1000),
      project("real_project_1", 25 * 60 * 60 * 1000),
      project("01_long_beach_batch1", 100 * 24 * 60 * 60 * 1000),
      project("embed_bbb", 23 * 60 * 60 * 1000)
    ]
    const expired = findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)
    expect(expired).toEqual(["embed_aaa"])
  })

  test("empty input returns empty array", () => {
    expect(findExpiredEmbedProjects([], NOW_MS, TTL_MIN)).toEqual([])
  })

  test("nothing expired returns empty array", () => {
    const projects = [
      project("embed_aaa", 60_000),
      project("real_proj", 0)
    ]
    expect(findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)).toEqual([])
  })

  test("project with no lastActivityMs is treated as expired", () => {
    const projects = [{ name: "embed_zzz" }]
    const expired = findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)
    expect(expired).toEqual(["embed_zzz"])
  })

  test("real-named project with no lastActivityMs is NOT expired", () => {
    const projects = [{ name: "real_project_with_no_activity" }]
    const expired = findExpiredEmbedProjects(projects, NOW_MS, TTL_MIN)
    expect(expired).toEqual([])
  })
})
