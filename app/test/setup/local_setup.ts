import Logger from "../../src/server/logger"

// Mute logger to avoid polluting unit test info
Logger.mute()

// jsdom (Jest 28+) no longer exposes these Node globals; server-side code
// (redis, @aws-sdk, undici) and formidable need them.
import { TextDecoder, TextEncoder } from "util"
import { setImmediate, clearImmediate } from "timers"

const g = globalThis as unknown as Record<string, unknown>
if (g.TextEncoder === undefined) g.TextEncoder = TextEncoder
if (g.TextDecoder === undefined) g.TextDecoder = TextDecoder
if (g.setImmediate === undefined) g.setImmediate = setImmediate
if (g.clearImmediate === undefined) g.clearImmediate = clearImmediate
