/**
 * ROI mask extraction for orthomosaic images.
 *
 * The images are aerial orthomosaics composited into a rectangular canvas:
 * the captured imagery occupies a narrow footprint and every pixel outside it
 * is pure black padding. The mask produced here defines the valid region.
 *
 * Channel reduction uses max(R, G, B), NOT luminance: luminance pushes dark
 * blue-grey asphalt below the threshold and shatters the mask.
 *
 * Unlike the Python original, no full-canvas distance transform is built.
 * Nearest-inside lookups run a ring search from the vertex, which needs no
 * memory beyond the mask itself and only touches pixels near the few
 * vertices that actually fall outside.
 */

// sharp's runtime is a callable CommonJS export while its types resolve to the
// ESM declaration (default export); tsconfig has no esModuleInterop, so load
// it with require and name the callable type explicitly.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const sharp = require("sharp") as typeof import("sharp").default

/**
 * Orthomosaic padding is exactly (0, 0, 0); real imagery essentially never
 * is. Measured insensitive across thresholds 2..30.
 */
export const DEFAULT_THRESHOLD = 10

/**
 * Python's round(): half to even. Coordinates on exact .5 must land on the
 * same pixel the reference implementation used.
 *
 * @param v value
 */
export function roundHalfEven(v: number): number {
  const f = Math.floor(v)
  const diff = v - f
  if (diff < 0.5) {
    return f
  }
  if (diff > 0.5) {
    return f + 1
  }
  return f % 2 === 0 ? f : f + 1
}

/**
 * A boolean ROI mask with nearest-inside lookup.
 */
export class RoiMask {
  /** mask width */
  public readonly width: number
  /** mask height */
  public readonly height: number
  /** 1 where the pixel is inside the region of interest, row-major */
  public readonly data: Uint8Array

  /**
   * Wrap an existing mask buffer.
   *
   * @param width mask width
   * @param height mask height
   * @param data row-major 0/1 values, length width * height
   */
  constructor(width: number, height: number, data: Uint8Array) {
    this.width = width
    this.height = height
    this.data = data
  }

  /** Fraction of the canvas inside the ROI. */
  public get coverage(): number {
    let inside = 0
    for (let i = 0; i < this.data.length; i++) {
      inside += this.data[i]
    }
    return this.data.length === 0 ? 0 : inside / this.data.length
  }

  /**
   * Whether an image-space point lies inside the ROI. Points beyond the
   * canvas are outside by definition.
   *
   * @param x x coordinate
   * @param y y coordinate
   */
  public contains(x: number, y: number): boolean {
    const ix = roundHalfEven(x)
    const iy = roundHalfEven(y)
    if (ix < 0 || iy < 0 || ix >= this.width || iy >= this.height) {
      return false
    }
    return this.data[iy * this.width + ix] === 1
  }

  /**
   * Nearest in-ROI pixel to a point, with the distance to it measured from
   * the true (unrounded) point. For a point already inside, the point itself
   * with distance 0.
   *
   * Mirrors the reference implementation's distance-transform lookup: the
   * point is rounded and clipped to the canvas, the nearest inside pixel to
   * THAT pixel is taken, and the reported distance is then measured from the
   * true point. Scans square rings around the clipped pixel; a ring's
   * Chebyshev radius under-bounds Euclidean distance, so after a hit the
   * scan continues until the ring radius exceeds the best distance found.
   *
   * @param x x coordinate
   * @param y y coordinate
   */
  public nearestInside(x: number, y: number): [number, number, number] {
    const w = this.width
    const h = this.height
    const ix = Math.min(Math.max(roundHalfEven(x), 0), w - 1)
    const iy = Math.min(Math.max(roundHalfEven(y), 0), h - 1)
    if (this.data[iy * w + ix] === 1) {
      return [ix, iy, 0]
    }

    let bestX = ix
    let bestY = iy
    let bestDist = Infinity
    const maxRadius = Math.max(w, h)

    const visit = (px: number, py: number): void => {
      if (px < 0 || py < 0 || px >= w || py >= h) {
        return
      }
      if (this.data[py * w + px] !== 1) {
        return
      }
      const d = Math.hypot(px - ix, py - iy)
      if (d < bestDist) {
        bestDist = d
        bestX = px
        bestY = py
      }
    }

    for (let r = 1; r <= maxRadius; r++) {
      if (r > bestDist) {
        break
      }
      for (let px = ix - r; px <= ix + r; px++) {
        visit(px, iy - r)
        visit(px, iy + r)
      }
      for (let py = iy - r + 1; py <= iy + r - 1; py++) {
        visit(ix - r, py)
        visit(ix + r, py)
      }
    }

    // Measure from the true point so sub-pixel spill is not quantised away.
    return [bestX, bestY, Math.hypot(bestX - x, bestY - y)]
  }
}

/** Growable stack of pixel indices for flood fills. */
class IndexStack {
  private buffer = new Int32Array(1 << 16)
  private size = 0

  /**
   * Push an index, growing the buffer when full.
   *
   * @param index pixel index to push
   */
  public push(index: number): void {
    if (this.size === this.buffer.length) {
      const grown = new Int32Array(this.buffer.length * 2)
      grown.set(this.buffer)
      this.buffer = grown
    }
    this.buffer[this.size++] = index
  }

  /** Pop the top index, or -1 when empty. */
  public pop(): number {
    return this.size === 0 ? -1 : this.buffer[--this.size]
  }
}

/**
 * Flood-fill 4-connected pixels equal to `from`, setting them to `to`.
 *
 * @param data the mask
 * @param width mask width
 * @param height mask height
 * @param seed starting pixel index
 * @param from value to replace
 * @param to replacement value
 * @param stack scratch stack
 * @returns number of pixels filled
 */
function floodFill(
  data: Uint8Array,
  width: number,
  height: number,
  seed: number,
  from: number,
  to: number,
  stack: IndexStack
): number {
  if (data[seed] !== from) {
    return 0
  }
  data[seed] = to
  stack.push(seed)
  let count = 0
  for (let i = stack.pop(); i !== -1; i = stack.pop()) {
    count++
    const x = i % width
    const y = (i - x) / width
    if (x > 0 && data[i - 1] === from) {
      data[i - 1] = to
      stack.push(i - 1)
    }
    if (x < width - 1 && data[i + 1] === from) {
      data[i + 1] = to
      stack.push(i + 1)
    }
    if (y > 0 && data[i - width] === from) {
      data[i - width] = to
      stack.push(i - width)
    }
    if (y < height - 1 && data[i + width] === from) {
      data[i + width] = to
      stack.push(i + width)
    }
  }
  return count
}

/**
 * Fill interior holes in place: background pixels not reachable from the
 * canvas border become foreground. Equivalent to
 * scipy.ndimage.binary_fill_holes with the default 4-connected structure.
 *
 * @param data the mask (0/1)
 * @param width mask width
 * @param height mask height
 */
export function fillHoles(
  data: Uint8Array,
  width: number,
  height: number
): void {
  const stack = new IndexStack()
  // Mark border-reachable background as 2.
  for (let x = 0; x < width; x++) {
    floodFill(data, width, height, x, 0, 2, stack)
    floodFill(data, width, height, (height - 1) * width + x, 0, 2, stack)
  }
  for (let y = 0; y < height; y++) {
    floodFill(data, width, height, y * width, 0, 2, stack)
    floodFill(data, width, height, y * width + width - 1, 0, 2, stack)
  }
  // Remaining 0s are holes; 2s are the real outside.
  for (let i = 0; i < data.length; i++) {
    if (data[i] === 0) {
      data[i] = 1
    } else if (data[i] === 2) {
      data[i] = 0
    }
  }
}

/**
 * Keep only the largest 4-connected foreground component, in place. On a
 * size tie the first component in raster order wins, matching
 * scipy.ndimage.label + argmax.
 *
 * @param data the mask (0/1)
 * @param width mask width
 * @param height mask height
 */
export function keepLargestComponent(
  data: Uint8Array,
  width: number,
  height: number
): void {
  const stack = new IndexStack()
  const seeds: number[] = []
  let bestSeed = -1
  let bestSize = 0
  for (let i = 0; i < data.length; i++) {
    if (data[i] !== 1) {
      continue
    }
    const size = floodFill(data, width, height, i, 1, 2, stack)
    seeds.push(i)
    if (size > bestSize) {
      bestSize = size
      bestSeed = i
    }
  }
  for (const seed of seeds) {
    floodFill(data, width, height, seed, 2, seed === bestSeed ? 1 : 0, stack)
  }
}

/**
 * Extract the ROI mask from an orthomosaic image: threshold max(R, G, B),
 * fill interior holes, keep the largest connected component.
 *
 * @param imagePath path to the image
 * @param threshold values at or below this are padding
 */
export async function computeRoiMask(
  imagePath: string,
  threshold: number = DEFAULT_THRESHOLD
): Promise<RoiMask> {
  const { data: pixels, info } = await sharp(imagePath, {
    limitInputPixels: false
  })
    .removeAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true })

  const { width, height, channels } = info
  const data = new Uint8Array(width * height)
  for (let i = 0, p = 0; i < data.length; i++, p += channels) {
    let max = pixels[p]
    for (let c = 1; c < channels; c++) {
      if (pixels[p + c] > max) {
        max = pixels[p + c]
      }
    }
    data[i] = max > threshold ? 1 : 0
  }

  fillHoles(data, width, height)
  keepLargestComponent(data, width, height)
  return new RoiMask(width, height, data)
}
