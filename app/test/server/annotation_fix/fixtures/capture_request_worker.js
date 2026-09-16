let raw = ""
process.stdin.setEncoding("utf8")
process.stdin.on("data", (chunk) => {
  raw += chunk
})
process.stdin.on("end", () => {
  const request = JSON.parse(raw)
  const document = request.document.map((frame) => ({
    ...frame,
    name: `min-angle:${request.min_angle}`
  }))
  process.stdout.write(JSON.stringify({ ok: true, document }))
})
