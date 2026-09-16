/** @jest-environment node */
import {
  DEFAULT_CONNECT_MIN_ANGLE,
  DEFAULT_CONNECT_TOLERANCE,
  getConnectMinAngle,
  getConnectTolerance
} from "../../../src/server/annotation_fix"

const ENV = "SCALABEL_ANNOTATION_FIX_TOLERANCE"
const original = process.env[ENV]
const MIN_ANGLE_ENV = "SCALABEL_ANNOTATION_FIX_MIN_ANGLE"
const originalMinAngle = process.env[MIN_ANGLE_ENV]

afterEach(() => {
  if (original === undefined) {
    delete process.env.SCALABEL_ANNOTATION_FIX_TOLERANCE
  } else {
    process.env[ENV] = original
  }
  if (originalMinAngle === undefined) {
    delete process.env.SCALABEL_ANNOTATION_FIX_MIN_ANGLE
  } else {
    process.env[MIN_ANGLE_ENV] = originalMinAngle
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

test("defaults the production continuity guard to 150 degrees", () => {
  delete process.env.SCALABEL_ANNOTATION_FIX_MIN_ANGLE
  expect(getConnectMinAngle()).toBe(150)
  expect(DEFAULT_CONNECT_MIN_ANGLE).toBe(150)
  process.env[MIN_ANGLE_ENV] = ""
  expect(getConnectMinAngle()).toBe(150)
})

test("reads disabled and bounded continuity guard values from the environment", () => {
  process.env[MIN_ANGLE_ENV] = "0"
  expect(getConnectMinAngle()).toBe(0)
  process.env[MIN_ANGLE_ENV] = "150"
  expect(getConnectMinAngle()).toBe(150)
  process.env[MIN_ANGLE_ENV] = "180"
  expect(getConnectMinAngle()).toBe(180)
})

test("falls back to 150 degrees for invalid continuity guard values", () => {
  for (const value of ["abc", "-1", "180.1", "Infinity"]) {
    process.env[MIN_ANGLE_ENV] = value
    expect(getConnectMinAngle()).toBe(150)
  }
})
