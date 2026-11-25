# Viewport Polyline Rendering and Interaction Implementation Plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. The user explicitly prohibits running test scripts and will test manually.

**Goal:** Keep polyline strokes, handles, snapping, and body picking consistent at zoom 1–60 without stretching annotation buffers or changing saved geometry.

**Architecture:** Keep the existing image canvas. Pin the annotation canvases to the viewport; project original-image coordinates through a shared fitted-image/pan/rotation transform. Use a fixed logical drawing resolution of two units per CSS pixel and a device-resolution context transform. Cache original-coordinate paths and bounds; use native stroke geometry for polyline picking instead of color readback.

**Tech Stack:** Existing TypeScript, React, and Canvas 2D; no new dependencies.

**Spec:** User-approved design in the current conversation and `docs/polyline-feature-map.md`.

## Global Constraints

- Default visible line width: 3 CSS pixels, multiplied by the existing width control.
- Preserve colors, alpha, handle gestures, merge/disjoint rules, and original-image storage/export coordinates.
- Snap acceptance stays 15 CSS pixels, independent of zoom and backing allocation.
- Whole-line dragging and W/E picking require the actual stroke; handles retain their own hit targets.
- Keep frame batching, cache geometry, resize buffers only on viewport/display-density changes, and preserve image motion-resolution handling.
- Do not execute tests, benchmark scripts, commit, or push. Static source review and compiler checks are allowed.

## Review Focus

- Pan/zoom/rotation must align image, annotation, previews, and pointer coordinates.
- Dense and curved geometry must not trigger full path rebuilding on every view change.
- Pointer release must apply the final position; dragging keeps its selected target.
- Hidden categories and curves-only filtering must also govern hit detection and snapping.
- Zoom-only operations must not dispatch geometry changes or alter exported labels.

## Tasks

- [x] Add pure viewport coordinate helpers and cached polyline paths/bounds.
- [x] Pin annotation canvases to the viewport and use fixed-size display styles.
- [x] Add precise stroke/handle picking, correct snapping scale, and accurate W/E targeting.
- [x] Review final pointer positions, overlays, filtering, allocations, and geometry persistence.
- [x] Update the feature map and report static verification plus manual checks; run no tests.

## Execution Notes

- Work in the current feature branch so the user can manually exercise this checkout.
- Retain the pre-existing feature-map edits.
- The user's no-test instruction overrides the testing steps in the workflow skills.
- Static review corrected D+click final-release replay, invisible curves-only
  snap targets, repeated curve-group scans, mixed-drawable stacking, and
  point-drag/pan-window conflicts. A focused read-only re-review confirmed fixes.

## Manual Acceptance Checks

- Compare strokes, opacity, point handles, and snap acceptance at reset, 3×,
  intermediate zoom, and the maximum reachable zoom; exercise thickness buttons.
- Drag LINE/MID/CURVE handles, use C/D clicks, join and disjoint, then undo/redo.
  Include a trackpad double-tap drag and release outside the viewer.
- Pick and move complete straight/curved/closed lines; verify nearby empty space
  does not grab them. Repeat W/E and verify the preview follows the actual curve.
- Pan, wheel/pinch zoom, rotate, resize the pane, and switch image sizes on a
  dense task. Check alignment, smoothness, blank frames, and cursor response.
- Verify hidden categories, curves-only targets, mixed box/line stacking, and
  letterbox margins. Confirm view-only changes leave exported geometry unchanged;
  intentional edits retain their normal JSON format and original-image coordinates.

No test or benchmark script was executed. Runtime smoothness and visual behavior
are pending the user's manual verification.
