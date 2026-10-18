"""Side-by-side review crops: raw on the left, labelled on the right."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from annotation_qa.geometry import dense
from annotation_qa.imaging import FrameImage

_DRAW_DIR = Path(__file__).resolve().parents[2] / "scalabel" / "tools"
if str(_DRAW_DIR) not in sys.path:
    sys.path.insert(0, str(_DRAW_DIR))

from draw_poly2d import COLORS, DEFAULT_COLOR  # noqa: E402

MAGENTA = (255, 0, 255)
CYAN = (255, 255, 0)
GAP = 8
RENDER_VERSION = "v2"
CATEGORY_HILITE = {"curb_road_edge": (0, 0, 255), "without_curb_road_edge": (0, 220, 0)}


def hilite_color(category):
    """Bright colour used for the changed label when its category changed."""
    return CATEGORY_HILITE.get(category) or COLORS.get(category, DEFAULT_COLOR)


def _tag(panel, text):
    """White text on a dark box, top-left of a panel."""
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    cv2.rectangle(panel, (0, 0), (w + 12, h + base + 10), (30, 30, 30), -1)
    cv2.putText(panel, text, (6, h + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2,
                cv2.LINE_AA)


def _draw_label(canvas, label, origin, zoom, color, width):
    for poly in label.get("poly2d") or []:
        pts = dense(poly)
        if len(pts) < 2 or not np.isfinite(pts).all():
            continue
        p = np.rint((pts - origin) * zoom).astype(np.int32)
        cv2.polylines(canvas, [p.reshape(-1, 1, 2)], bool(poly.get("closed")), color, width)


def issue_crop(image: FrameImage, frame: dict, issue, half: int = 90, zoom: int = 3,
               raw_only: bool = False) -> np.ndarray:
    """Raw | 8 px gap | labelled, each (2*half*zoom) square; black where off-canvas.

    ``raw_only`` returns just the raw panel (no labels)."""
    ax, ay = int(round(issue.anchor[0])), int(round(issue.anchor[1]))
    x0, y0 = ax - half, ay - half
    raw = np.zeros((2 * half, 2 * half, 3), np.uint8)
    c = image.crop(x0, y0, x0 + 2 * half, y0 + 2 * half)
    if c.size:
        ox, oy = max(-x0, 0), max(-y0, 0)
        raw[oy:oy + c.shape[0], ox:ox + c.shape[1]] = c
    left = cv2.resize(raw, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    if raw_only:
        return left
    right = left.copy()
    origin = np.array([x0, y0], dtype=float)
    related = set(issue.related_label_ids)
    labels = frame.get("labels") or []
    # split pieces etc.: label id -> category, each drawn in its class colour
    hls = dict(getattr(issue, "highlights", None) or {})
    for lab in labels:
        if lab["id"] != issue.label_id and lab["id"] not in related and lab["id"] not in hls:
            _draw_label(right, lab, origin, zoom, COLORS.get(lab.get("category"), DEFAULT_COLOR), 2)
    for lab in labels:
        if lab["id"] in hls and lab["id"] != issue.label_id:
            _draw_label(right, lab, origin, zoom, (255, 255, 255), 6)
            _draw_label(right, lab, origin, zoom, hilite_color(hls[lab["id"]]), 4)
    for lab in labels:
        if lab["id"] in related and lab["id"] != issue.label_id:
            _draw_label(right, lab, origin, zoom, CYAN, 3)
    for lab in labels:
        if lab["id"] == issue.label_id:
            hl = getattr(issue, "highlight_category", None)
            if hl:
                _draw_label(right, lab, origin, zoom, (255, 255, 255), 6)
                _draw_label(right, lab, origin, zoom, hilite_color(hl), 4)
            else:
                _draw_label(right, lab, origin, zoom, MAGENTA, 3)
    for x, y in getattr(issue, "markers", None) or []:  # e.g. split cut points
        c = (int(round((x - x0) * zoom)), int(round((y - y0) * zoom)))
        cv2.circle(right, c, 9, (255, 255, 255), 3)
        cv2.circle(right, c, 9, (0, 0, 0), 1)
    if getattr(issue, "panel_tag", None):
        _tag(right, issue.panel_tag)
    gap = np.full((left.shape[0], GAP, 3), 40, np.uint8)
    return np.hstack([left, gap, right])
