import SvgIcon, { SvgIconProps } from "@material-ui/core/SvgIcon"
import React from "react"

/**
 * The material design "content_cut" scissors path (24x24 viewBox). Inlined
 * because @material-ui/icons v4 does not ship a ContentCut icon.
 */
export const CONTENT_CUT_PATH =
  "M9.64 7.64c.23-.5.36-1.05.36-1.64 0-2.21-1.79-4-4-4S2 3.79 2 6s1.79 4 4 " +
  "4c.59 0 1.14-.13 1.64-.36L10 12l-2.36 2.36C7.14 14.13 6.59 14 6 " +
  "14c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4c0-.59-.13-1.14-.36-1.64L12 " +
  "14l7 7h3v-1L9.64 7.64zM6 8c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 " +
  "2zm0 12c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm6-7.5c-.28 " +
  "0-.5-.22-.5-.5s.22-.5.5-.5.5.22.5.5-.22.5-.5.5zM19 3l-6 6 2 2 7-7V3z"

/**
 * Expanded viewBox shared by the custom toolbar icons. Their glyphs are drawn
 * to the edges of the 24x24 box, while stock material icons are inset a
 * couple of pixels — the 2px margin here shrinks the custom glyphs (~14%) so
 * they optically match the rest of the toolbar.
 */
const INSET_VIEW_BOX = "-2 -2 28 28"

/**
 * Scissors icon for the cut tool (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function ContentCutIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path d={CONTENT_CUT_PATH} />
    </SvgIcon>
  )
}

/**
 * An S-curve, drawn under the scissors to mark the curve-aware cut tool.
 * Distinguishes it at a glance from the plain scissors, which refuse curves.
 */
const CURVE_HINT_PATH = "M2 21c5 0 5-7 10-7s5 7 10 7"

/**
 * Scissors over a curve: the cut tool that also splits curved segments.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function ContentCutCurveIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path d={CONTENT_CUT_PATH} transform="translate(0,-3) scale(0.92)" />
      <path
        d={CURVE_HINT_PATH}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
      />
    </SvgIcon>
  )
}

/**
 * Glyph for the straighten tool: a curve flattening into a straight line,
 * with the endpoint anchors marked. Reads as the inverse of the curve tools.
 */
const STRAIGHTEN_CURVE_PATH = "M3 7c5 0 6-4 9-4"
const STRAIGHTEN_LINE_PATH = "M3 17h18"

/**
 * Curve-to-straight icon for the straighten tool.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function StraightenIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path
        d={STRAIGHTEN_CURVE_PATH}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        opacity={0.55}
      />
      <path
        d={STRAIGHTEN_LINE_PATH}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
      />
      <circle cx={3} cy={17} r={2.6} fill="currentColor" />
      <circle cx={21} cy={17} r={2.6} fill="currentColor" />
      <path
        d="M15 6l3 3-3 3"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </SvgIcon>
  )
}

const STRAIGHTEN_CURSOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ' +
  'viewBox="0 0 24 24">' +
  '<path d="' + STRAIGHTEN_LINE_PATH +
  '" fill="none" stroke="white" stroke-width="4"/>' +
  '<path d="' + STRAIGHTEN_LINE_PATH +
  '" fill="none" stroke="black" stroke-width="2"/>' +
  '<path d="' + STRAIGHTEN_CURVE_PATH +
  '" fill="none" stroke="white" stroke-width="4"/>' +
  '<path d="' + STRAIGHTEN_CURVE_PATH +
  '" fill="none" stroke="black" stroke-width="2"/></svg>'

/**
 * CSS cursor shown while the straighten tool is armed. Same hotspot as the cut
 * cursors so every one-shot tool aims identically.
 */
export const STRAIGHTEN_CURSOR = `url('data:image/svg+xml;utf8,${encodeURIComponent(
  STRAIGHTEN_CURSOR_SVG
)}') 12 12, crosshair`

/**
 * Two line ends meeting at a shared point: endpoint snapping is ON.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function SnapOnIcon(props: SvgIconProps): JSX.Element {
  const stroke = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const
  }
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path {...stroke} d="M2 12h9" />
      <path {...stroke} d="M13 12h9" />
      <circle cx={12} cy={12} r={3.4} fill="currentColor" />
    </SvgIcon>
  )
}

/**
 * The same two line ends held apart, with a slash: endpoint snapping is OFF.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function SnapOffIcon(props: SvgIconProps): JSX.Element {
  const stroke = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const
  }
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path {...stroke} d="M2 12h7" />
      <path {...stroke} d="M15 12h7" />
      <circle cx={9} cy={12} r={2.4} fill="currentColor" />
      <circle cx={15} cy={12} r={2.4} fill="currentColor" />
      <path {...stroke} d="M4 20L20 4" />
    </SvgIcon>
  )
}

const CUT_CURSOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ' +
  'viewBox="0 0 24 24"><path d="' +
  CONTENT_CUT_PATH +
  '" fill="white" stroke="black" stroke-width="1"/></svg>'

const CURVE_CUT_CURSOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ' +
  'viewBox="0 0 24 24"><path d="' +
  CONTENT_CUT_PATH +
  '" transform="translate(0,-3) scale(0.92)" fill="white" stroke="black" ' +
  'stroke-width="1"/><path d="' +
  CURVE_HINT_PATH +
  '" fill="none" stroke="white" stroke-width="3"/><path d="' +
  CURVE_HINT_PATH +
  '" fill="none" stroke="black" stroke-width="1.5"/></svg>'

/**
 * CSS cursor shown while the curve-aware cut tool is armed. Same hotspot as
 * CUT_CURSOR so the two tools aim identically.
 */
export const CURVE_CUT_CURSOR = `url('data:image/svg+xml;utf8,${encodeURIComponent(
  CURVE_CUT_CURSOR_SVG
)}') 12 12, crosshair`

/**
 * CSS cursor shown while the cut tool is armed: a scissors glyph (white fill,
 * black outline, visible on any image) with the hotspot at the blade crossing
 * (12, 12). Falls back to crosshair where SVG cursors are unsupported.
 */
export const CUT_CURSOR = `url('data:image/svg+xml;utf8,${encodeURIComponent(
  CUT_CURSOR_SVG
)}') 12 12, crosshair`

/**
 * Glyph for the delete-segment tool: the material design "delete" trash can
 * (24x24 viewBox). Inlined like CONTENT_CUT_PATH.
 */
export const DELETE_SEGMENT_PATH =
  "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 " +
  "1H5v2h14V4z"

/**
 * Delete-segment icon (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function DeleteSegmentIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path d={DELETE_SEGMENT_PATH} />
    </SvgIcon>
  )
}

/**
 * Freeform-select icon: a dashed irregular closed loop (freeform marquee).
 * Drawn with strokes (fill="none"), mirroring RectangleSelectIcon.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function FreeformSelectIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="3 2"
        strokeLinecap="round"
        d="M12 4.5c4.6 0 7.5 2.4 7.5 5.7 0 3.6-3.4 6.8-8 6.8-3.9 0-7-2.3-7-5.4 0-3.7 3.1-7.1 7.5-7.1z"
      />
    </SvgIcon>
  )
}

/**
 * Reset-rotation icon: the lucide "refresh-ccw" glyph (two counter-clockwise
 * arrows), inlined as strokes because lucide-react is not a dependency.
 * Mirrors FreeformSelectIcon (strokes, fill="none").
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function RefreshCcwIcon(props: SvgIconProps): JSX.Element {
  const strokeProps = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const
  }
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <path {...strokeProps} d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path {...strokeProps} d="M3 3v5h5" />
      <path {...strokeProps} d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
      <path {...strokeProps} d="M16 16h5v5" />
    </SvgIcon>
  )
}

/**
 * Rectangle-marquee icon for the rectangle select mode: a dashed axis-aligned
 * rectangle. Mirrors FreeformSelectIcon (strokes, fill="none").
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function RectangleSelectIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props} viewBox={INSET_VIEW_BOX}>
      <rect
        x={4}
        y={6}
        width={16}
        height={12}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="3 2"
      />
    </SvgIcon>
  )
}
