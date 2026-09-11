/**
 * ROI mask extraction for orthomosaic images.
 *
 * The images are aerial orthomosaics composited into a rectangular canvas: the
 * captured imagery occupies a narrow, often cross/L-shaped footprint and every
 * pixel outside it is pure black padding. Annotations are only meaningful inside
 * that footprint, so the mask produced here defines the valid region.
 *
 * Two departures from the SciPy implementation this replaces, both forced by
 * scale — the largest frame in the corpus is 238.9 megapixels:
 *
 * Flood fills are scanline-based and iterative. A per-pixel stack would need an
 * entry per pixel in the worst case (about 1 GB of indices here); pushing one
 * seed per contiguous run instead keeps the stack to the number of spans, which
 * is orders of magnitude smaller.
 *
 * There is no distance transform. SciPy's `distance_transform_edt` with
 * `return_indices` materialises a float distance array plus two index arrays
 * over the WHOLE canvas, which at this size is several gigabytes, to answer what
 * is in practice a few dozen queries per frame — under 4% of vertices fall
 * outside, with a median spill of 8 px. `nearestInside` searches outward from
 * the query point instead, which is exact and touches only the pixels near that
 * point.
 */

import * as crypto from "crypto"
import * as fs from "fs-extra"
import * as path from "path"
import * as zlib from "zlib"

import { readPngMask } from "./png"

/**
 * Orthomosaic padding is exactly (0, 0, 0); real imagery essentially never is.
 * Measured insensitive: the non-black fraction moves only 0.1415 -> 0.1396
 * across thresholds 2..30 on a sample frame, so this needs no per-image tuning.
 */
export const DEFAULT_THRESHOLD = 10

/** Bit 0 of the working buffer: the pixel is inside the region of interest. */
const INSIDE = 1

/** Bit 1 of the working buffer: scratch flag for the current flood fill. */
const VISITED = 2

/** Both flags, used to test a pixel's full state in one read. */
const BOTH = INSIDE | VISITED

/**
 * Flood fill a 4-connected region, marking it, and report how big it was.
 *
 * Scanline based: it fills a whole horizontal run at a time and pushes only one
 * seed per contiguous run in the rows above and below, so the stack holds spans
 * rather than pixels.
 *
 * `setBits` must be a subset of `testMask` so that marking a pixel makes it fail
 * the eligibility test, which is what terminates the fill.
 *
 * @param data the working buffer, one byte of flags per pixel
 * @param width canvas width
 * @param height canvas height
 * @param seeds pixel indices to start from
 * @param testMask which flag bits take part in the eligibility test
 * @param matchValue the masked value a pixel must have to be filled
 * @param setBits flag bits to set on every filled pixel
 */
function scanlineFill(
  data: Uint8Array,
  width: number,
  height: number,
  seeds: number[],
  testMask: number,
  matchValue: number,
  setBits: number
): number {
  let stack = new Int32Array(Math.max(1024, seeds.length))
  let top = 0
  for (const seed of seeds) {
    if (top === stack.length) {
      const grown = new Int32Array(stack.length * 2)
      grown.set(stack)
      stack = grown
    }
    stack[top++] = seed
  }

  /**
   * Push one index, growing the stack when it is full.
   *
   * @param index the pixel index to revisit later
   */
  const push = (index: number): void => {
    if (top === stack.length) {
      const grown = new Int32Array(stack.length * 2)
      grown.set(stack)
      stack = grown
    }
    stack[top++] = index
  }

  let filled = 0
  while (top > 0) {
    const index = stack[--top]
    if ((data[index] & testMask) !== matchValue) {
      continue
    }

    const y = Math.floor(index / width)
    const rowStart = y * width
    const x = index - rowStart

    let left = x
    while (left > 0 && (data[rowStart + left - 1] & testMask) === matchValue) {
      left--
    }
    let right = x
    while (
      right < width - 1 &&
      (data[rowStart + right + 1] & testMask) === matchValue
    ) {
      right++
    }

    for (let i = rowStart + left; i <= rowStart + right; i++) {
      data[i] |= setBits
      filled++
    }

    // One seed per contiguous eligible run in each neighbouring row.
    for (let dy = -1; dy <= 1; dy += 2) {
      const ny = y + dy
      if (ny < 0 || ny >= height) {
        continue
      }
      const nRow = ny * width
      let inRun = false
      for (let i = left; i <= right; i++) {
        if ((data[nRow + i] & testMask) === matchValue) {
          if (!inRun) {
            push(nRow + i)
            inRun = true
          }
        } else {
          inRun = false
        }
      }
    }
  }

  return filled
}

/**
 * Fill interior holes in the mask, in place.
 *
 * Required, not cosmetic: dark asphalt, shadows and dark vehicles inside the
 * road threshold to black and would otherwise punch false holes through the
 * region. Background reachable from the canvas edge is true outside; anything
 * else is enclosed and becomes part of the region.
 *
 * @param data the working buffer
 * @param width canvas width
 * @param height canvas height
 */
function fillHoles(data: Uint8Array, width: number, height: number): void {
  const seeds: number[] = []
  for (let x = 0; x < width; x++) {
    seeds.push(x)
    seeds.push((height - 1) * width + x)
  }
  for (let y = 0; y < height; y++) {
    seeds.push(y * width)
    seeds.push(y * width + width - 1)
  }

  scanlineFill(data, width, height, seeds, BOTH, 0, VISITED)

  for (let i = 0; i < data.length; i++) {
    if ((data[i] & BOTH) === 0) {
      // Outside the threshold but unreachable from the edge: an enclosed hole.
      data[i] |= INSIDE
    }
    data[i] &= ~VISITED
  }
}

/**
 * Reduce the mask to its largest connected component, in place.
 *
 * Drops stray specks such as compression noise in the padding.
 *
 * @param data the working buffer
 * @param width canvas width
 * @param height canvas height
 */
function keepLargestComponent(
  data: Uint8Array,
  width: number,
  height: number
): void {
  let bestSeed = -1
  let bestSize = 0
  let components = 0

  for (let i = 0; i < data.length; i++) {
    if ((data[i] & BOTH) !== INSIDE) {
      continue
    }
    components++
    const size = scanlineFill(data, width, height, [i], BOTH, INSIDE, VISITED)
    if (size > bestSize) {
      bestSize = size
      bestSeed = i
    }
  }

  if (components <= 1) {
    for (let i = 0; i < data.length; i++) {
      data[i] &= ~VISITED
    }
    return
  }

  // Re-fill from the winner alone, then keep only what that fill reached.
  for (let i = 0; i < data.length; i++) {
    data[i] &= ~VISITED
  }
  if (bestSeed < 0) {
    return
  }
  scanlineFill(data, width, height, [bestSeed], BOTH, INSIDE, VISITED)
  for (let i = 0; i < data.length; i++) {
    data[i] = (data[i] & VISITED) !== 0 ? INSIDE : 0
  }
}

/**
 * A boolean ROI mask with a nearest-inside query.
 */
export class RoiMask {
  /** canvas width in pixels */
  public readonly width: number
  /** canvas height in pixels */
  public readonly height: number
  /** row-major, 1 where the pixel is inside the region of interest */
  public readonly mask: Uint8Array
  /** how many pixels are inside, cached for `coverage` */
  private readonly insideCount: number

  /**
   * Wrap a finished mask, counting how much of it is inside.
   *
   * @param width canvas width
   * @param height canvas height
   * @param mask row-major mask, 1 inside
   */
  public constructor(width: number, height: number, mask: Uint8Array) {
    this.width = width
    this.height = height
    this.mask = mask
    let count = 0
    for (let i = 0; i < mask.length; i++) {
      count += mask[i]
    }
    this.insideCount = count
  }

  /** Fraction of the canvas inside the ROI (typically 0.07-0.18 here). */
  public get coverage(): number {
    return this.mask.length === 0 ? 0 : this.insideCount / this.mask.length
  }

  /** Whether any pixel at all is inside, so a nearest-inside query can succeed. */
  public get isEmpty(): boolean {
    return this.insideCount === 0
  }

  /**
   * Whether an image-space point lies inside the ROI.
   *
   * Points beyond the canvas are outside by definition.
   *
   * @param x image-space x
   * @param y image-space y
   */
  public contains(x: number, y: number): boolean {
    const ix = Math.round(x)
    const iy = Math.round(y)
    if (ix < 0 || iy < 0 || ix >= this.width || iy >= this.height) {
      return false
    }
    return this.mask[iy * this.width + ix] === 1
  }

  /**
   * Nearest in-ROI pixel to a point, with the distance to it.
   *
   * Searches square rings of growing radius rather than consulting a
   * precomputed distance transform. The winner is the pixel closest to the
   * query point ROUNDED AND CLAMPED onto the canvas, which is what the SciPy
   * implementation this replaces returned: a distance transform is defined over
   * the pixel grid, so `indices[iy, ix]` is the pixel nearest to (ix, iy), not
   * to the unrounded point. The distance is then measured from the true,
   * unrounded point so that sub-pixel spill is not quantised away — again
   * matching the original.
   *
   * Selecting on distance from the clamped centre is also what makes the search
   * cheap. A pixel on the ring at Chebyshev radius r is at least r from the
   * centre, so once a candidate at distance d is in hand, no ring beyond r = d
   * can beat it and the loop stops. Ranking by distance from the unrounded
   * point instead would break that bound for a vertex far outside the canvas,
   * where the two centres diverge by thousands of pixels.
   *
   * @param x image-space x
   * @param y image-space y
   * @returns the nearest inside pixel and its distance, or null for an empty ROI
   */
  public nearestInside(
    x: number,
    y: number
  ): {
    /** nearest inside pixel x */ nx: number
    /** nearest inside pixel y */ ny: number
    /** distance from the query point */ distance: number
  } | null {
    const cx = Math.min(Math.max(Math.round(x), 0), this.width - 1)
    const cy = Math.min(Math.max(Math.round(y), 0), this.height - 1)
    if (this.mask[cy * this.width + cx] === 1) {
      return { nx: cx, ny: cy, distance: 0 }
    }
    if (this.isEmpty) {
      return null
    }

    let bestX = -1
    let bestY = -1
    // Distance from the CLAMPED centre, which is what ranks candidates.
    let best = Infinity

    /**
     * Test one pixel against the running best.
     *
     * @param px candidate x
     * @param py candidate y
     */
    const consider = (px: number, py: number): void => {
      if (this.mask[py * this.width + px] !== 1) {
        return
      }
      const dx = px - cx
      const dy = py - cy
      const distance = Math.sqrt(dx * dx + dy * dy)
      if (distance < best) {
        best = distance
        bestX = px
        bestY = py
      }
    }

    const maxRadius = this.width + this.height
    for (let r = 1; r <= maxRadius; r++) {
      if (r > best) {
        break
      }
      const x0 = cx - r
      const x1 = cx + r
      const y0 = cy - r
      const y1 = cy + r
      const left = Math.max(x0, 0)
      const right = Math.min(x1, this.width - 1)

      if (y0 >= 0) {
        for (let px = left; px <= right; px++) {
          consider(px, y0)
        }
      }
      if (y1 < this.height) {
        for (let px = left; px <= right; px++) {
          consider(px, y1)
        }
      }
      const top = Math.max(y0 + 1, 0)
      const bottom = Math.min(y1 - 1, this.height - 1)
      if (x0 >= 0) {
        for (let py = top; py <= bottom; py++) {
          consider(x0, py)
        }
      }
      if (x1 < this.width) {
        for (let py = top; py <= bottom; py++) {
          consider(x1, py)
        }
      }
    }

    if (bestX < 0) {
      return null
    }
    // Reported from the true point, not the clamped one.
    return {
      nx: bestX,
      ny: bestY,
      distance: Math.sqrt((bestX - x) ** 2 + (bestY - y) ** 2)
    }
  }
}

/**
 * Extract the ROI mask from an orthomosaic image.
 *
 * Three steps, in order: threshold the brightest channel to separate imagery
 * from black padding, fill interior holes, then keep the largest connected
 * component.
 *
 * @param imagePath the image to read
 * @param threshold brightness above which a pixel counts as imagery
 */
export async function computeRoiMask(
  imagePath: string,
  threshold: number = DEFAULT_THRESHOLD
): Promise<RoiMask> {
  const { width, height, mask } = await readPngMask(imagePath, threshold)
  fillHoles(mask, width, height)
  keepLargestComponent(mask, width, height)
  return new RoiMask(width, height, mask)
}

/**
 * Cache identity for a mask: path, mtime, size and threshold.
 *
 * @param imagePath the image the mask came from
 * @param threshold the threshold it was built with
 */
async function cacheKey(imagePath: string, threshold: number): Promise<string> {
  const stat = await fs.stat(imagePath)
  const raw = `${path.resolve(imagePath)}|${stat.mtimeMs}|${
    stat.size
  }|${threshold}`
  return crypto.createHash("sha1").update(raw).digest("hex")
}

/** Magic bytes and version prefixing a cached mask file. */
const CACHE_MAGIC = "AFM1"

/**
 * Compute a ROI mask, reusing a cached one when the image is unchanged.
 *
 * Masks are stored bit-packed and deflated, so even a 238-megapixel frame caches
 * to a few megabytes. A corrupt or unreadable entry falls through to
 * recomputation rather than failing, and a cache write failure is ignored.
 *
 * @param imagePath the image to read
 * @param threshold brightness above which a pixel counts as imagery
 * @param cacheDir where to memoise masks, or undefined to disable caching
 */
export async function loadRoiMask(
  imagePath: string,
  threshold: number = DEFAULT_THRESHOLD,
  cacheDir?: string
): Promise<RoiMask> {
  if (cacheDir === undefined || cacheDir === "") {
    return await computeRoiMask(imagePath, threshold)
  }

  const file = path.join(
    cacheDir,
    `${await cacheKey(imagePath, threshold)}.bin`
  )

  try {
    const packed = zlib.inflateSync(await fs.readFile(file))
    if (packed.subarray(0, 4).toString("ascii") === CACHE_MAGIC) {
      const width = packed.readUInt32LE(4)
      const height = packed.readUInt32LE(8)
      const bits = packed.subarray(12)
      const total = width * height
      if (bits.length === Math.ceil(total / 8)) {
        const mask = new Uint8Array(total)
        for (let i = 0; i < total; i++) {
          mask[i] = (bits[i >> 3] >> (7 - (i & 7))) & 1
        }
        return new RoiMask(width, height, mask)
      }
    }
  } catch {
    // Missing or unreadable cache entry: recompute below.
  }

  const roi = await computeRoiMask(imagePath, threshold)

  try {
    await fs.ensureDir(cacheDir)
    const total = roi.mask.length
    const packed = Buffer.alloc(12 + Math.ceil(total / 8))
    packed.write(CACHE_MAGIC, 0, "ascii")
    packed.writeUInt32LE(roi.width, 4)
    packed.writeUInt32LE(roi.height, 8)
    for (let i = 0; i < total; i++) {
      if (roi.mask[i] === 1) {
        packed[12 + (i >> 3)] |= 1 << (7 - (i & 7))
      }
    }
    await fs.writeFile(file, zlib.deflateSync(packed))
  } catch {
    // A cache write failure must never fail the run.
  }

  return roi
}
