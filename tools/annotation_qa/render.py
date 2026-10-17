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
    for lab in labels:
        if lab["id"] != issue.label_id and lab["id"] not in related:
            _draw_label(right, lab, origin, zoom, COLORS.get(lab.get("category"), DEFAULT_COLOR), 2)
    for lab in labels:
        if lab["id"] in related and lab["id"] != issue.label_id:
            _draw_label(right, lab, origin, zoom, CYAN, 3)
    for lab in labels:
        if lab["id"] == issue.label_id:
            _draw_label(right, lab, origin, zoom, MAGENTA, 3)
    gap = np.full((left.shape[0], GAP, 3), 40, np.uint8)
    return np.hstack([left, gap, right])
