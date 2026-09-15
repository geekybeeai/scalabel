import * as child from "child_process"
import { createClient } from "redis"

import Logger from "../../src/server/logger"
import { getTestConfig } from "../server/util/util"

/**
 * Launch redis server
 */
function launchRedisServer(): void {
  const redisProc = child.spawn("redis-server", [
    "--appendonly",
    "no",
    "--save",
    "",
    "--port",
    String(getTestConfig().redis.port),
    "--bind",
    "127.0.0.1",
    "--protected-mode",
    "yes",
    "--loglevel",
    "warning"
  ])
  redisProc.stdout.on("data", (data) => {
    process.stdout.write(data)
  })

  redisProc.stderr.on("data", (data) => {
    process.stdout.write(data)
  })
}

module.exports = async () => {
  Logger.info(
    "Info logger is muted for concise test status report. " +
      "The switch is in test/setup/local_setup.ts"
  )
  const client = createClient({
    socket: {
      host: "127.0.0.1",
      port: getTestConfig().redis.port,
      reconnectStrategy: false
    }
  })
  client.on("error", () => {})
  try {
    await client.connect()
    await client.ping()
    await client.close()
  } catch (_err) {
    Logger.info("Can't find redis server. Launch local redis server.")
    launchRedisServer()
  }
}
