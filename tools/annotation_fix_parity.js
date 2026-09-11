#!/usr/bin/env node
/* eslint-disable */
/**
 * Run the Python and JS annotation-fix engines on the same document and diff
 * the results.
 *
 *   node tools/annotation_fix_parity.js <export.json> [imageRoot] [--python python]
 *
 * Exit 0 when reports agree and every vertex matches to 1e-6; exit 1 with a
 * list of differences otherwise. Sub-pixel differences from nearest-pixel
 * tie-breaks are expected and are listed, not hidden.
 */
const { spawnSync } = require("child_process")
const fs = require("fs")
const path = require("path")

const args = process.argv.slice(2)
const pyFlag = args.indexOf("--python")
const python = pyFlag >= 0 ? args.splice(pyFlag, 2)[1] : "python"
const [docPath, imageRootArg] = args
if (!docPath) {
  console.error(
    "usage: node tools/annotation_fix_parity.js <export.json> [imageRoot] [--python python]"
  )
  process.exit(2)
}
const imageRoot = path.resolve(imageRootArg || "local-data")
const document = JSON.parse(fs.readFileSync(docPath, "utf8"))
const request = JSON.stringify({
  document,
  image_root: imageRoot,
  clamp: true,
  connect: true
})

function runEngine(cmd, cmdArgs, opts) {
  const proc = spawnSync(cmd, cmdArgs, {
    input: request,
    maxBuffer: 1 << 30,
    encoding: "utf8",
    ...opts
  })
  if (proc.error) throw proc.error
  if (proc.stderr) process.stderr.write(proc.stderr)
  return JSON.parse(proc.stdout)
}

const toolsDir = __dirname
const py = runEngine(python, ["-m", "annotation_fix.stdio"], {
  cwd: toolsDir,
  env: { ...process.env, PYTHONPATH: toolsDir }
})
const js = runEngine(process.execPath, [
  path.join(__dirname, "..", "app", "dist", "annotation_fix_worker.js")
])

const diffs = []
function framesOf(doc) {
  return Array.isArray(doc) ? doc : doc.frames || []
}

if (!py.ok || !js.ok) {
  diffs.push(
    `ok: python=${py.ok} (${py.error || ""}) js=${js.ok} (${js.error || ""})`
  )
} else {
  const sp = py.report.summary
  const sj = js.report.summary
  for (const k of Object.keys(sp)) {
    if (JSON.stringify(sp[k]) !== JSON.stringify(sj[k])) {
      diffs.push(
        `summary.${k}: python=${JSON.stringify(sp[k])} js=${JSON.stringify(sj[k])}`
      )
    }
  }
  const fp = framesOf(py.document)
  const fj = framesOf(js.document)
  fp.forEach((frameP, i) => {
    const frameJ = fj[i]
    const rp = py.report.frames[i]
    const rj = js.report.frames[i]
    for (const k of ["clamped", "merged", "labelsAfter", "imageFound"]) {
      if (rp[k] !== rj[k]) {
        diffs.push(`${frameP.name} ${k}: python=${rp[k]} js=${rj[k]}`)
      }
    }
    if (JSON.stringify(rp.connections) !== JSON.stringify(rj.connections)) {
      diffs.push(`${frameP.name} connections differ`)
    }
    const lp = frameP.labels || []
    const lj = (frameJ && frameJ.labels) || []
    if (lp.length !== lj.length) {
      diffs.push(
        `${frameP.name} label count python=${lp.length} js=${lj.length}`
      )
      return
    }
    lp.forEach((labelP, li) => {
      const labelJ = lj[li]
      if (String(labelP.id) !== String(labelJ.id)) {
        diffs.push(
          `${frameP.name} label ${li} id python=${labelP.id} js=${labelJ.id}`
        )
      }
      ;(labelP.poly2d || []).forEach((polyP, pi) => {
        const polyJ = (labelJ.poly2d || [])[pi]
        if (
          !polyJ ||
          polyP.types !== polyJ.types ||
          polyP.vertices.length !== polyJ.vertices.length
        ) {
          diffs.push(
            `${frameP.name} label ${labelP.id} poly ${pi} shape differs`
          )
          return
        }
        polyP.vertices.forEach((v, vi) => {
          const w = polyJ.vertices[vi]
          const d = Math.hypot(v[0] - w[0], v[1] - w[1])
          if (d > 1e-6) {
            diffs.push(
              `${frameP.name} label ${labelP.id} v${vi}: python=[${v}] js=[${w}] (${d.toFixed(3)} px)`
            )
          }
        })
      })
    })
  })
}

console.log(`python: ${JSON.stringify(py.ok ? py.report.summary : py.error)}`)
console.log(`js:     ${JSON.stringify(js.ok ? js.report.summary : js.error)}`)
if (diffs.length === 0) {
  console.log("PARITY: identical")
  process.exit(0)
}
console.log(`PARITY: ${diffs.length} difference(s)`)
for (const d of diffs) console.log("  " + d)
process.exit(1)
