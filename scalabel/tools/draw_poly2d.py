"""Draw Scalabel poly2d annotations onto their images.

Usage:
    python3 scalabel/tools/draw_poly2d.py EXPORT.json
        --root local-data --out OUT_DIR [--width N]

Only needs numpy and opencv-python; it imports nothing from scalabel, so it
runs without the package's other dependencies installed.

Vertex types follow the Scalabel app (app/src/drawable/2d/polygon2d.ts):
'L' is a vertex joined by a straight line; a 'C' starts a cubic Bezier whose
two control points are vertices i and i+1 and whose end point is vertex i+2.
"""
import argparse
import json
import os

import cv2
import numpy as np

# RGB colors per category, copied from CATEGORY_NAME_COLOR_MAP in
# app/src/drawable/util.ts so renders match the annotator.
RGB_COLORS = {
    "curb_road_edge": (210, 50, 50),           # red
    "without_curb_road_edge": (50, 200, 50),   # green
    "continuous_white_line": (255, 215, 0),    # yellow
    "dashed_white_line": (100, 180, 255),      # blue
    "double_yellow_line": (102, 51, 0),        # brown
    "center_turn_lane": (255, 140, 0),         # orange
    "solid_single_line": (255, 215, 0),        # yellow
}
COLORS = {k: (b, g, r) for k, (r, g, b) in RGB_COLORS.items()}  # OpenCV is BGR
DEFAULT_COLOR = (255, 255, 255)

# Sidebar display names (app/src/components/toolbar_category.tsx).
DISPLAY_NAME = {
    "continuous_white_line": "continuous_line",
    "dashed_white_line": "dashed_line",
}


def bezier(p0, p1, p2, p3, steps=40):
    t = np.linspace(0.0, 1.0, steps)[:, None]
    return ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1
            + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3)


def poly_to_points(poly):
    pts = np.asarray(poly["vertices"], dtype=np.float64)
    types = poly.get("types") or "L" * len(pts)
    n = len(pts)
    out = [pts[0]]
    i = 1
    while i < n:
        if types[i] == "C":
            end = pts[(i + 2) % n]
            out.extend(bezier(out[-1], pts[i], pts[(i + 1) % n], end)[1:])
            i += 3
        else:
            out.append(pts[i])
            i += 1
    if poly.get("closed"):
        out.append(pts[0])
    return np.asarray(out)


def draw_frame(img, labels, width):
    for label in labels or []:
        color = COLORS.get(label["category"], DEFAULT_COLOR)
        for poly in label.get("poly2d") or []:
            pts = np.round(poly_to_points(poly)).astype(np.int32)
            cv2.polylines(img, [pts], False, color, width, cv2.LINE_AA)
            for v in poly["vertices"]:
                cv2.circle(img, (int(v[0]), int(v[1])), width + 2, color, -1,
                           cv2.LINE_AA)
    return img


def draw_legend(img, categories, width):
    scale = max(img.shape[1] / 1500.0, 0.6)
    y = int(40 * scale)
    for name in categories:
        color = COLORS.get(name, DEFAULT_COLOR)
        x0 = int(20 * scale)
        cv2.rectangle(img, (x0 - 8, y - int(32 * scale)),
                      (x0 + int(480 * scale), y + int(10 * scale)), (51, 51, 51), -1)
        cv2.rectangle(img, (x0, y - int(26 * scale)),
                      (x0 + int(30 * scale), y + int(4 * scale)), color, -1)
        cv2.putText(img, DISPLAY_NAME.get(name, name), (x0 + int(45 * scale), y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (230, 230, 230),
                    max(1, int(2 * scale)), cv2.LINE_AA)
        y += int(42 * scale)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--root", default="local-data",
                    help="directory the frame urls are relative to")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=0,
                    help="line width in px (0 = scale with image size)")
    args = ap.parse_args()

    with open(args.export) as f:
        data = json.load(f)
    frames = data["frames"] if isinstance(data, dict) else data
    categories = [c["name"] for c in data.get("config", {}).get("categories", [])] \
        if isinstance(data, dict) else sorted(COLORS)
    os.makedirs(args.out, exist_ok=True)

    for frame in frames:
        path = os.path.join(args.root, frame["url"])
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            print("skip (cannot read):", path)
            continue
        width = args.width or max(2, round(max(img.shape[:2]) / 1500))
        draw_frame(img, frame.get("labels"), width)
        draw_legend(img, categories, width)
        name = os.path.splitext(os.path.basename(frame["url"]))[0]
        dst = os.path.join(args.out, name + "_annotated.jpg")
        cv2.imwrite(dst, img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"{len(frame.get('labels') or []):4d} labels -> {dst}")


if __name__ == "__main__":
    main()
