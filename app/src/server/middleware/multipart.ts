import { IncomingMessage } from "http"
import { NextFunction, Request, Response } from "express"
import formidable, { File } from "formidable"

const maxFileSize = 1000 * 1024 * 1024 // 1G

/** Parsed text fields; single values are unwrapped from formidable's arrays */
export type Fields = Record<string, string | string[] | undefined>
/** Parsed files; single files are unwrapped from formidable's arrays */
export type Files = Record<string, File | File[] | undefined>

/**
 * A middleware to parse multipart/form-data request, after which two
 * properties, `fields` and `files`, will be added to the request.
 *
 * @param req -
 * @param _
 * @param next
 */
export function multipartFormData(
  req: Request,
  _: Response,
  next: NextFunction
): void {
  parseMultipartFormData(req)
    .then(({ fields, files }) => {
      Object.assign(req, { fields, files })
      next()
    })
    .catch(next)
}

/**
 * Formidable v3 always returns arrays; collapse single-element arrays so
 * downstream handlers keep receiving one value per field, as before.
 *
 * @param record
 */
function unwrapSingles<T>(
  record: Record<string, T[] | undefined>
): Record<string, T | T[] | undefined> {
  const result: Record<string, T | T[] | undefined> = {}
  for (const key of Object.keys(record)) {
    const value = record[key]
    result[key] = value !== undefined && value.length === 1 ? value[0] : value
  }
  return result
}

/**
 * Parse an incoming multipart/form-data request.
 *
 * @param req
 */
export async function parseMultipartFormData(
  req: IncomingMessage
): Promise<{ fields: Fields; files: Files }> {
  const form = formidable({ maxFileSize })
  const [fields, files] = await form.parse(req)
  return { fields: unwrapSingles(fields), files: unwrapSingles(files) }
}
