/** @jest-environment node */
import {
  DEFAULT_CONNECT_TOLERANCE,
  getConnectTolerance
} from "../../../src/server/annotation_fix"

const ENV = "SCALABEL_ANNOTATION_FIX_TOLERANCE"
const original = process.env[ENV]

afterEach(() => {
  if (original === undefined) {
    delete process.env.SCALABEL_ANNOTATION_FIX_TOLERANCE
  } else {
    process.env[ENV] = original
  }
})

test("defaults to 40 px when unset or empty", () => {
  delete process.env.SCALABEL_ANNOTATION_FIX_TOLERANCE
  expect(getConnectTolerance()).toBe(40)
  expect(DEFAULT_CONNECT_TOLERANCE).toBe(40)
  process.env[ENV] = ""
  expect(getConnectTolerance()).toBe(40)
})

test("reads a positive number from the environment", () => {
  process.env[ENV] = "40"
  expect(getConnectTolerance()).toBe(40)
  process.env[ENV] = "12.5"
  expect(getConnectTolerance()).toBe(12.5)
})

test("falls back to the default on garbage or non-positive values", () => {
  process.env[ENV] = "abc"
  expect(getConnectTolerance()).toBe(40)
  process.env[ENV] = "0"
  expect(getConnectTolerance()).toBe(40)
  process.env[ENV] = "-5"
  expect(getConnectTolerance()).toBe(40)
})
