/**
 * Minimal PNG reader that goes straight from file to a threshold mask.
 *
 * Written by hand against Node's built-in zlib rather than pulling in an image
 * library, for two reasons. The obvious one is dependencies: `canvas` is in the
 * tree but is never built (the install runs with --ignore-scripts) and making it
 * work would mean adding Cairo and Pango to the image — a heavier runtime
 * dependency than the Python this replaces.
 *
 * The real reason is memory. These orthomosaics reach 238.9 megapixels, which is
 * 717 MB of RGB once decoded, and every general-purpose decoder hands back the
 * whole surface. Nothing here needs the pixels: the only question asked of them
 * is "is this one brighter than the threshold", one bit of answer per pixel. So
 * the inflate stream is consumed a row at a time and each row is reduced to mask
 * bytes and dropped. Peak memory is the mask itself (one byte per pixel) plus
 * two scanlines, not the decoded image.
 *
 * Interlaced PNGs are rejected rather than supported: Adam7 interleaves seven
 * passes over the whole image, so a row cannot be finished (and therefore cannot
 * be dropped) until the last pass arrives, which is exactly the property this
 * reader exists to avoid. No orthomosaic in the corpus is interlaced.
 */

import * as fs from "fs-extra"
import * as zlib from "zlib"

/** First eight bytes of every PNG file. */
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])

/**
 * Channel count per PNG colour type, indexed by the type byte.
 *
 * Types 1, 5 and 7 do not exist; the zeros are placeholders that the colour-type
 * check rejects before they can be used.
 */
const CHANNELS: number[] = [1, 0, 3, 1, 2, 0, 4]

/** Colour type 3: sample values index a palette rather than being intensities. */
const COLOR_TYPE_PALETTE = 3

/**
 * An image already reduced to one byte per pixel.
 */
export interface ThresholdedImage {
  /** image width in pixels */
  width: number
  /** image height in pixels */
  height: number
  /** row-major, one byte per pixel, 1 inside the imagery and 0 in the padding */
  mask: Uint8Array
}

/**
 * The parts of IHDR that decide how samples are laid out.
 */
interface Header {
  /** image width in pixels */
  width: number
  /** image height in pixels */
  height: number
  /** bits per sample: 1, 2, 4, 8 or 16 */
  bitDepth: number
  /** PNG colour type: 0, 2, 3, 4 or 6 */
  colorType: number
  /** 0 for a plain image, 1 for Adam7, which is not supported */
  interlace: number
}

/**
 * Paeth predictor from the PNG specification.
 *
 * @param a the byte to the left
 * @param b the byte above
 * @param c the byte above-left
 */
function paeth(a: number, b: number, c: number): number {
  const p = a + b - c
  const pa = Math.abs(p - a)
  const pb = Math.abs(p - b)
  const pc = Math.abs(p - c)
  if (pa <= pb && pa <= pc) {
    return a
  }
  return pb <= pc ? b : c
}

/**
 * Reverse one scanline's filter, in place.
 *
 * Each filter references the bytes to the left and the corresponding bytes of
 * the previous row, so rows must be undone in order. Bytes off the top or left
 * edge read as zero.
 *
 * @param filter the filter type byte that preceded the row
 * @param row the filtered row, overwritten with the reconstructed bytes
 * @param previous the already-reconstructed row above
 * @param bpp bytes per complete pixel, the stride between left neighbours
 */
function unfilter(
  filter: number,
  row: Buffer,
  previous: Buffer,
  bpp: number
): void {
  const length = row.length
  switch (filter) {
    case 0:
      break
    case 1:
      for (let i = bpp; i < length; i++) {
        row[i] = (row[i] + row[i - bpp]) & 0xff
      }
      break
    case 2:
      for (let i = 0; i < length; i++) {
        row[i] = (row[i] + previous[i]) & 0xff
      }
      break
    case 3:
      for (let i = 0; i < bpp; i++) {
        row[i] = (row[i] + (previous[i] >> 1)) & 0xff
      }
      for (let i = bpp; i < length; i++) {
        row[i] = (row[i] + ((row[i - bpp] + previous[i]) >> 1)) & 0xff
      }
      break
    case 4:
      for (let i = 0; i < bpp; i++) {
        row[i] = (row[i] + previous[i]) & 0xff
      }
      for (let i = bpp; i < length; i++) {
        row[i] =
          (row[i] + paeth(row[i - bpp], previous[i], previous[i - bpp])) & 0xff
      }
      break
    default:
      throw new Error(`unknown PNG filter type ${filter}`)
  }
}

/**
 * Read one sample from a row whose bit depth is below 8.
 *
 * Sub-byte depths pack several samples into each byte, most significant first.
 *
 * @param row the reconstructed row
 * @param index which sample to read
 * @param bitDepth bits per sample: 1, 2 or 4
 */
function readPackedSample(
  row: Buffer,
  index: number,
  bitDepth: number
): number {
  const perByte = 8 / bitDepth
  const byte = row[Math.floor(index / perByte)]
  const shift = 8 - bitDepth * ((index % perByte) + 1)
  return (byte >> shift) & ((1 << bitDepth) - 1)
}

/**
 * Reduce one reconstructed row to mask bytes.
 *
 * Channel reduction is max(R, G, B), never luminance: luminance weights green
 * ~0.59 and blue ~0.11, which drops dark blue-grey asphalt below the threshold
 * and shatters the mask. Any non-zero channel means real imagery.
 *
 * 16-bit samples are compared on their high byte alone, which is the same
 * comparison as scaling the threshold up to the wider range.
 *
 * @param row the reconstructed row
 * @param header how samples are laid out
 * @param palette RGB triples for colour type 3, otherwise undefined
 * @param threshold a pixel is imagery when its brightest channel exceeds this
 * @param mask destination mask
 * @param offset index in mask of this row's first pixel
 */
function thresholdRow(
  row: Buffer,
  header: Header,
  palette: Buffer | undefined,
  threshold: number,
  mask: Uint8Array,
  offset: number
): void {
  const { width, bitDepth, colorType } = header
  const channels = CHANNELS[colorType]

  // The overwhelmingly common case for these images: 8-bit RGB or RGBA. Kept as
  // its own branch because it runs hundreds of millions of times per frame.
  if (bitDepth === 8 && (colorType === 2 || colorType === 6)) {
    for (let x = 0, p = 0; x < width; x++, p += channels) {
      const r = row[p]
      const g = row[p + 1]
      const b = row[p + 2]
      let max = r > g ? r : g
      if (b > max) {
        max = b
      }
      mask[offset + x] = max > threshold ? 1 : 0
    }
    return
  }

  if (bitDepth === 8 && (colorType === 0 || colorType === 4)) {
    for (let x = 0, p = 0; x < width; x++, p += channels) {
      mask[offset + x] = row[p] > threshold ? 1 : 0
    }
    return
  }

  if (bitDepth === 16) {
    // Two bytes per sample, big-endian, so the high byte sits at the even index.
    const limit = colorType === 2 || colorType === 6 ? 3 : 1
    for (let x = 0, p = 0; x < width; x++, p += channels * 2) {
      let max = row[p]
      for (let c = 1; c < limit; c++) {
        const value = row[p + c * 2]
        if (value > max) {
          max = value
        }
      }
      mask[offset + x] = max > threshold ? 1 : 0
    }
    return
  }

  if (colorType === COLOR_TYPE_PALETTE && palette !== undefined) {
    for (let x = 0; x < width; x++) {
      const index = bitDepth === 8 ? row[x] : readPackedSample(row, x, bitDepth)
      const p = index * 3
      const r = palette[p]
      const g = palette[p + 1]
      const b = palette[p + 2]
      let max = r > g ? r : g
      if (b > max) {
        max = b
      }
      mask[offset + x] = max > threshold ? 1 : 0
    }
    return
  }

  if (colorType === 0) {
    // Grayscale below 8 bits: scale the sample up to the 0-255 range so the
    // threshold keeps its meaning.
    const scale = 255 / ((1 << bitDepth) - 1)
    for (let x = 0; x < width; x++) {
      const value = readPackedSample(row, x, bitDepth) * scale
      mask[offset + x] = value > threshold ? 1 : 0
    }
    return
  }

  throw new Error(
    `unsupported PNG: bit depth ${bitDepth}, colour type ${colorType}`
  )
}

/**
 * What the container parse yields.
 */
interface Container {
  /** image layout from IHDR */
  header: Header
  /** palette entries, present only for colour type 3 */
  palette?: Buffer
  /** compressed image data, split across however many IDAT chunks */
  idat: Buffer[]
}

/**
 * Parse the PNG container, returning the header, palette and IDAT payloads.
 *
 * The compressed file is small enough to hold whole — tens of megabytes — so
 * only the inflated data is streamed.
 *
 * @param file the complete PNG file
 */
function parseChunks(file: Buffer): Container {
  if (file.length < 8 || !file.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error("not a PNG file")
  }

  let header: Header | undefined
  let palette: Buffer | undefined
  const idat: Buffer[] = []

  let offset = 8
  while (offset + 8 <= file.length) {
    const length = file.readUInt32BE(offset)
    const type = file.toString("ascii", offset + 4, offset + 8)
    const start = offset + 8
    const end = start + length
    if (end > file.length) {
      throw new Error(`truncated PNG chunk ${type}`)
    }

    if (type === "IHDR") {
      header = {
        width: file.readUInt32BE(start),
        height: file.readUInt32BE(start + 4),
        bitDepth: file[start + 8],
        colorType: file[start + 9],
        interlace: file[start + 12]
      }
    } else if (type === "PLTE") {
      palette = file.subarray(start, end)
    } else if (type === "IDAT") {
      idat.push(file.subarray(start, end))
    } else if (type === "IEND") {
      break
    }

    // 4 trailing bytes of CRC. Not verified: a corrupt image surfaces as an
    // inflate or filter error, and the caller treats every failure the same way.
    offset = end + 4
  }

  if (header === undefined) {
    throw new Error("PNG has no IHDR chunk")
  }
  if (header.width <= 0 || header.height <= 0) {
    throw new Error("PNG has no pixels")
  }
  if (header.interlace !== 0) {
    throw new Error("interlaced PNGs are not supported")
  }
  const channels = CHANNELS[header.colorType]
  if (channels === undefined || channels === 0) {
    throw new Error(`unsupported PNG colour type ${header.colorType}`)
  }
  if (header.colorType === COLOR_TYPE_PALETTE && palette === undefined) {
    throw new Error("paletted PNG has no PLTE chunk")
  }

  return { header, palette, idat }
}

/**
 * Read a PNG and return it already reduced to a threshold mask.
 *
 * @param imagePath the file to read
 * @param threshold brightness above which a pixel counts as imagery
 */
export async function readPngMask(
  imagePath: string,
  threshold: number
): Promise<ThresholdedImage> {
  const file: Buffer = await fs.readFile(imagePath)
  const { header, palette, idat } = parseChunks(file)
  const { width, height, bitDepth, colorType } = header

  const channels = CHANNELS[colorType]
  const bitsPerPixel = channels * bitDepth
  const rowBytes = Math.ceil((bitsPerPixel * width) / 8)
  // Filters step by whole pixels, or by one byte when a pixel is narrower.
  const bpp = Math.max(1, Math.ceil(bitsPerPixel / 8))

  const mask = new Uint8Array(width * height)

  let current = Buffer.allocUnsafe(rowBytes)
  let previous = Buffer.alloc(rowBytes)
  let filter = -1
  let filled = 0
  let y = 0

  /**
   * Take inflated bytes and turn every row they complete into mask bytes.
   *
   * @param chunk the next run of inflated output
   */
  const consume = (chunk: Buffer): void => {
    let position = 0
    while (position < chunk.length && y < height) {
      if (filter < 0) {
        filter = chunk[position]
        position++
        continue
      }
      const wanted = rowBytes - filled
      const available = chunk.length - position
      const take = wanted < available ? wanted : available
      chunk.copy(current, filled, position, position + take)
      filled += take
      position += take

      if (filled < rowBytes) {
        continue
      }

      unfilter(filter, current, previous, bpp)
      thresholdRow(current, header, palette, threshold, mask, y * width)

      // The row just reconstructed becomes the reference for the next one and
      // its buffer is reused, so only two scanlines are ever live.
      const swap = previous
      previous = current
      current = swap
      filled = 0
      filter = -1
      y++
    }
  }

  await new Promise<void>((resolve, reject) => {
    const inflate = zlib.createInflate()
    inflate.on("data", (chunk: Buffer) => {
      try {
        consume(chunk)
      } catch (error) {
        inflate.destroy(error as Error)
      }
    })
    inflate.on("end", resolve)
    inflate.on("error", reject)
    for (const part of idat) {
      inflate.write(part)
    }
    inflate.end()
  })

  if (y < height) {
    throw new Error(`PNG ended after ${y} of ${height} rows`)
  }

  return { width, height, mask }
}
