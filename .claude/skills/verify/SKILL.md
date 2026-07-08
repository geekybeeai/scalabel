---
name: verify
description: Runtime-verify 2D annotator changes by driving the label page in headless Chrome over CDP. Use when verifying canvas/toolbar features (cut tool, delete segment, drawing) end-to-end on this fork.
---

# Verifying scalabel 2D annotator changes at runtime

## Build + serve

- `npm run build` produces `app/dist` (~2 min). The server serves the bundle
  from disk per request — **rebuild after every src change or the browser
  tests stale code.**
- Server: `node app/dist/main.js --config ./local-data/scalabel/config.yml`
  (port 8686). The dev server is often ALREADY RUNNING (EADDRINUSE) — just
  use it; it picks up a rebuilt dist without restart. Redis is not installed;
  the server runs without it.

## Ready-made data

- Project `test2` = `polyline2d` labels with a locally-served image that
  exists on disk (`local-data/items/temp/SLC 4_verification.png`).
  Label page: `http://localhost:8686/label?project_name=test2&task_index=0`
- `test` is `polygon2d` (closed shapes — cut/delete tools reject those).

## Driving the GUI (no playwright/puppeteer installed)

- Headless Chrome + raw CDP works; `ws@7` is available in `node_modules`
  (require it by ABSOLUTE path if the script lives outside the repo).
- Launch: `chrome.exe --headless=new --disable-gpu
  --remote-debugging-port=9333 --window-size=1400,900
  --user-data-dir=%TEMP%\chrome-cdp-verify about:blank` (the launcher process
  exits immediately on Windows — check `http://127.0.0.1:9333/json/version`).
- New tab needs HTTP **PUT** `/json/new?<encoded url>` on modern Chrome.
- A working driver pattern (draw polyline via clicks + Enter, click toolbar
  buttons found by their inline SVG path `d` prefix, right-click for the
  context menu, `Page.captureScreenshot` per step) exists from the
  delete-segment verification — see `cdp_verify.js` in the session scratchpad
  or rebuild from this recipe.
- Wait for `document.querySelectorAll('canvas')` rects >200px wide plus ~2.5s
  for the image before interacting; clicks only register once the frame
  is loaded.
- Kill ONLY the verification Chrome afterwards (match `chrome-cdp-verify` in
  the command line), never the user's browser or dev server.

## Gotchas

- Driving the label page WRITES to the project's saved session (labels you
  draw persist) — use a scratch project or tell the user what you left
  behind.
- Toolbar buttons: cut = SVG path starting `M9.64`; delete-segment = path
  starting `M2 11h5`.
