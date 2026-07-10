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
 * Scissors icon for the cut tool (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function ContentCutIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path d={CONTENT_CUT_PATH} />
    </SvgIcon>
  )
}

const CUT_CURSOR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ' +
  'viewBox="0 0 24 24"><path d="' +
  CONTENT_CUT_PATH +
  '" fill="white" stroke="black" stroke-width="1"/></svg>'

/**
 * CSS cursor shown while the cut tool is armed: a scissors glyph (white fill,
 * black outline, visible on any image) with the hotspot at the blade crossing
 * (12, 12). Falls back to crosshair where SVG cursors are unsupported.
 */
export const CUT_CURSOR = `url('data:image/svg+xml;utf8,${encodeURIComponent(
  CUT_CURSOR_SVG
)}') 12 12, crosshair`

/**
 * Glyph for the delete-segment tool: a line with its dashed middle removed
 * (two solid end stubs, two middle dashes). Inlined like CONTENT_CUT_PATH.
 */
export const DELETE_SEGMENT_PATH =
  "M2 11h5v2H2v-2zm15 0h5v2h-5v-2zm-8 0h2v2H9v-2zm4 0h2v2h-2v-2z"

/**
 * Delete-segment icon (toolbar button + context-menu item).
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function DeleteSegmentIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path d={DELETE_SEGMENT_PATH} />
    </SvgIcon>
  )
}

/**
 * Lasso icon for the freeform-select tool: a dashed open loop with a small
 * tail. Drawn with strokes (fill="none") so it reads as a marquee/lasso.
 *
 * @param props standard SvgIcon props (fontSize, style, ...)
 */
export function FreeformSelectIcon(props: SvgIconProps): JSX.Element {
  return (
    <SvgIcon {...props}>
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="3 2"
        strokeLinecap="round"
        d="M12 4c4.5 0 8 2.9 8 6.5S16.5 17 12 17c-3 0-5.6-1-6.6-2.9"
      />
      <path d="M5 13.2l-1.9 4.1 4.1-1.2z" />
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
    <SvgIcon {...props}>
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
