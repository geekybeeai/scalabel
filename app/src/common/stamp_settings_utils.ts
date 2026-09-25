/** Parse a numeric input, returning null while the user is typing an invalid value.
 *
 * @param value input text
 */
export function parseStampNumber(value: string): number | null {
  if (value.trim() === "") {
    return null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** Clamp a stamp setting to the same range exposed by its slider.
 *
 * @param value number to clamp
 * @param min minimum accepted value
 * @param max maximum accepted value
 */
export function clampStampNumber(
  value: number,
  min: number,
  max: number
): number {
  return Math.min(max, Math.max(min, value))
}
